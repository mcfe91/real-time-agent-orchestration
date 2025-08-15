# =============================================================================
# IMPORTS
# =============================================================================
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum
import uuid
import json
from datetime import datetime
import asyncio

# =============================================================================
# DOMAIN LAYER - Core business entities and interfaces
# =============================================================================

class AgentStatus(Enum):
    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class AgentSession:
    id: str
    agent_type: str
    config: Dict[str, Any]
    status: AgentStatus
    created_at: datetime
    session_metadata: Dict[str, Any] | None = None 

@dataclass
class AgentEvent:
    session_id: str
    event_type: str
    data: Dict[str, Any]
    timestamp: datetime

# Domain interfaces (ports)
class AgentRepository(ABC):
    @abstractmethod
    async def save_session(self, session: AgentSession) -> None: ...
    
    @abstractmethod
    async def get_session(self, session_id: str) -> Optional[AgentSession]: ...
    
    @abstractmethod
    async def update_status(self, session_id: str, status: AgentStatus) -> None: ...

class EventStore(ABC):
    @abstractmethod
    async def store_event(self, event: AgentEvent) -> None: ...
    
    @abstractmethod
    async def get_events(self, session_id: str) -> List[AgentEvent]: ...

class NotificationService(ABC):
    @abstractmethod
    async def notify_session(self, session_id: str, message: Dict[str, Any]) -> None: ...

class TaskQueue(ABC):
    @abstractmethod
    async def enqueue_agent(self, session_id: str, agent_type: str, config: Dict[str, Any]) -> None: ...
    
    @abstractmethod
    async def cancel_agent(self, session_id: str) -> None: ...

class PubSub(ABC):
    @abstractmethod
    async def publish(self, channel: str, message: Dict[str, Any]) -> None: ...
    
    @abstractmethod
    async def subscribe(self, channel: str, callback) -> None: ...

    @abstractmethod
    async def unsubscribe(self, channel: str) -> None: ...

# =============================================================================
# APPLICATION LAYER - Use cases and orchestration
# =============================================================================

class AgentService:
    def __init__(
        self, 
        repository: AgentRepository,
        event_store: EventStore,
        notification_service: NotificationService,
        task_queue: TaskQueue,
        pubsub: PubSub
    ):
        self.repository = repository
        self.event_store = event_store
        self.notification_service = notification_service
        self.task_queue = task_queue
        self.pubsub = pubsub
    
    async def create_agent(self, agent_type: str, config: Dict[str, Any]) -> str:
        session_id = str(uuid.uuid4())
        session = AgentSession(
            id=session_id,
            agent_type=agent_type,
            config=config,
            status=AgentStatus.CREATED,
            created_at=datetime.now()
        )
        
        await self.repository.save_session(session)
        await self.task_queue.enqueue_agent(session_id, agent_type, config)
        
        return session_id
    
    async def stop_agent(self, session_id: str) -> None:
        await self.task_queue.cancel_agent(session_id)
        await self.repository.update_status(session_id, AgentStatus.STOPPED)
        await self.notification_service.notify_session(session_id, {
            "type": "status_change",
            "status": AgentStatus.STOPPED.value
        })
    
    async def handle_agent_event(self, session_id: str, event_type: str, data: Dict[str, Any]) -> None:
        event = AgentEvent(
            session_id=session_id,
            event_type=event_type,
            data=data,
            timestamp=datetime.now()
        )
        
        await self.event_store.store_event(event)
        await self.notification_service.notify_session(session_id, {
            "type": "agent_event",
            "event": event_type,
            "data": data
        })
    
    async def send_user_message(self, session_id: str, message: Dict[str, Any]) -> None:
        """Send user message to agent via both persistence and real-time pub/sub"""
        # Store for persistence
        await self.handle_agent_event(session_id, "user_message", message)
        
        # Send real-time notification to agent
        await self.pubsub.publish(f"agent:{session_id}", {
            "type": "user_message",
            "data": message,
            "timestamp": datetime.now().isoformat()
        })
    
    async def get_session_status(self, session_id: str) -> Optional[AgentSession]:
        return await self.repository.get_session(session_id)

# =============================================================================
# AGENT INTERFACE AND BASE CLASSES
# =============================================================================

class Agent(ABC):
    """Base interface for all agents"""
    
    def __init__(self, agent_service: AgentService):
        self.agent_service = agent_service
        self.history = {}  # Structured event history
    
    @abstractmethod
    async def run(self, session_id: str, config: Dict[str, Any]) -> None:
        """Main agent execution method"""
        pass
    
    async def load_session_history(self, session_id: str) -> None:
        """Load and structure conversation history from database"""
        events = await self.agent_service.event_store.get_events(session_id)
        
        # Structure events by type for fast access
        self.history = {}
        for event in events:
            if event.event_type not in self.history:
                self.history[event.event_type] = []
            self.history[event.event_type].append(event)
    
    def get_conversation_context(self, limit: int = 20) -> List[AgentEvent]:
        """Get recent conversation messages in chronological order"""
        conversation_events = []
        
        # Combine user messages and agent responses
        for event_type in ["user_message", "agent_response"]:
            if event_type in self.history:
                conversation_events.extend(self.history[event_type])
        
        # Sort by timestamp and return recent ones
        conversation_events.sort(key=lambda e: e.timestamp)
        return conversation_events[-limit:] if limit else conversation_events
    
    async def emit_event(self, session_id: str, event_type: str, data: Dict[str, Any]) -> None:
        """Helper method for agents to emit events"""
        await self.agent_service.handle_agent_event(session_id, event_type, data)
        
        # Update local history
        event = AgentEvent(
            session_id=session_id,
            event_type=event_type,
            data=data,
            timestamp=datetime.now()
        )
        if event_type not in self.history:
            self.history[event_type] = []
        self.history[event_type].append(event)
    
    async def subscribe_to_user_messages(self, session_id: str, callback) -> None:
        """Subscribe to real-time user messages for this session"""
        async def handle_message(message):
            # Update local history when new message arrives
            if message["type"] == "user_message":
                event = AgentEvent(
                    session_id=session_id,
                    event_type="user_message",
                    data=message["data"],
                    timestamp=datetime.fromisoformat(message["timestamp"])
                )
                if "user_message" not in self.history:
                    self.history["user_message"] = []
                self.history["user_message"].append(event)
            
            await callback(message)
        
        await self.agent_service.pubsub.subscribe(f"agent:{session_id}", handle_message)

    async def unsubscribe_from_user_messages(self, session_id: str) -> None:
        """Unsubscribe from user messages when agent shuts down"""
        await self.agent_service.pubsub.unsubscribe(f"agent:{session_id}")

class AgentRegistry:
    """Registry for different agent types"""
    
    def __init__(self):
        self._agents: Dict[str, type] = {}
    
    def register(self, agent_type: str, agent_class: type) -> None:
        """Register an agent class for a given type"""
        if not issubclass(agent_class, Agent):
            raise ValueError(f"Agent class must inherit from Agent")
        self._agents[agent_type] = agent_class
    
    def create_agent(self, agent_type: str, agent_service: AgentService) -> Agent:
        """Create an agent instance of the given type"""
        if agent_type not in self._agents:
            raise ValueError(f"Unknown agent type: {agent_type}")
        return self._agents[agent_type](agent_service)
    
    def get_registered_types(self) -> List[str]:
        """Get list of all registered agent types"""
        return list(self._agents.keys())

class AgentWorker:
    """Generic worker that can execute any registered agent type"""
    
    def __init__(self, agent_service: AgentService, registry: AgentRegistry):
        self.agent_service = agent_service
        self.registry = registry
    
    async def execute_agent(self, session_id: str, agent_type: str, config: Dict[str, Any]) -> None:
        """Execute an agent of the given type with session resumption"""
        try:
            # Update status to running
            await self.agent_service.repository.update_status(session_id, AgentStatus.RUNNING)
            await self.agent_service.notification_service.notify_session(session_id, {
                "type": "status_change",
                "status": AgentStatus.RUNNING.value
            })
            
            # Create agent and load session history
            agent = self.registry.create_agent(agent_type, self.agent_service)
            await agent.load_session_history(session_id)
            
            # Run the agent with full conversation context
            await agent.run(session_id, config)
            
            # Mark as completed if not already set by agent
            session = await self.agent_service.get_session_status(session_id)
            if session and session.status == AgentStatus.RUNNING:
                await self.agent_service.repository.update_status(session_id, AgentStatus.COMPLETED)
                await self.agent_service.notification_service.notify_session(session_id, {
                    "type": "status_change", 
                    "status": AgentStatus.COMPLETED.value
                })
                
        except Exception as e:
            # Mark as failed on any exception
            await self.agent_service.repository.update_status(session_id, AgentStatus.FAILED)
            await self.agent_service.notification_service.notify_session(session_id, {
                "type": "status_change",
                "status": AgentStatus.FAILED.value,
                "error": str(e)
            })
            raise

# =============================================================================
# EXAMPLE AGENT IMPLEMENTATION
# =============================================================================

class ExampleAgent(Agent):
    """Example agent showing session resumption and conversation context"""
    
    async def run(self, session_id: str, config: Dict[str, Any]) -> None:
        """Example agent that uses conversation history for intelligent responses"""
        print(f"Starting ExampleAgent for session {session_id}")
        
        # Check if this is a resumed session
        conversation_context = self.get_conversation_context()
        if conversation_context:
            print(f"Resuming session with {len(conversation_context)} previous messages")
            await self.emit_event(session_id, "agent_response", {
                "message": f"Welcome back! I can see our previous conversation with {len(conversation_context)} messages."
            })
        else:
            await self.emit_event(session_id, "agent_response", {"message": "Hello! This is a new conversation."})
        
        # Subscribe to user messages with context-aware responses
        async def handle_user_message(message):
            recent_context = self.get_conversation_context(limit=10)
            
            await self.emit_event(session_id, "agent_response", {
                "message": f"I received: {message['data'].get('content', 'No content')}",
                "context_used": f"Based on {len(recent_context)} previous messages"
            })

        subscription_active = False
        try:
            # Subscribe to user messages
            await self.subscribe_to_user_messages(session_id, handle_user_message)
            subscription_active = True
            
            heartbeat_count = 0
            while True:
                await asyncio.sleep(30)  # Heartbeat every 30 seconds
                heartbeat_count += 1
                
                # Check if session is still active (user still connected)
                session = await self.agent_service.get_session_status(session_id)
                if not session or session.status in [AgentStatus.STOPPED, AgentStatus.FAILED]:
                    break
                
                # Send periodic heartbeat to show agent is alive
                await self.emit_event(session_id, "agent_heartbeat", {
                    "heartbeat": heartbeat_count,
                    "message": f"Agent is alive (heartbeat #{heartbeat_count})",
                    "uptime_minutes": heartbeat_count * 0.5
                })

        except Exception as e:
            print(f"Error in ExampleAgent: {e}")
            raise
        finally:
            # Clean up subscription when agent exits
            if subscription_active:
                try:
                    await self.unsubscribe_from_user_messages(session_id)
                except Exception as e:
                    print(f"Error unsubscribing: {e}")
        
        print(f"ExampleAgent completed for session {session_id}")


# =============================================================================
# INFRASTRUCTURE LAYER - Concrete implementations
# =============================================================================

import sqlalchemy as sa
from sqlalchemy.orm import declarative_base  # Updated import
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

class SessionModel(Base):
    __tablename__ = "agent_sessions"
    
    id = sa.Column(sa.String, primary_key=True)
    agent_type = sa.Column(sa.String, nullable=False)
    config = sa.Column(sa.Text, nullable=False)  # JSON
    status = sa.Column(sa.String, nullable=False)
    created_at = sa.Column(sa.DateTime, nullable=False)
    session_metadata = sa.Column(sa.Text)  # JSON - renamed from metadata

class EventModel(Base):
    __tablename__ = "agent_events"
    
    id = sa.Column(sa.String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = sa.Column(sa.String, nullable=False, index=True)
    event_type = sa.Column(sa.String, nullable=False)
    data = sa.Column(sa.Text, nullable=False)  # JSON
    timestamp = sa.Column(sa.DateTime, nullable=False)

class SQLAgentRepository(AgentRepository):
    def __init__(self, database_url: str):
        self.engine = sa.create_engine(database_url)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
    
    async def save_session(self, session: AgentSession) -> None:
        db = self.SessionLocal()
        try:
            db_session = SessionModel(
                id=session.id,
                agent_type=session.agent_type,
                config=json.dumps(session.config),
                status=session.status.value,
                created_at=session.created_at,
                session_metadata=json.dumps(session.session_metadata or {})  # Updated field name
            )
            db.merge(db_session)
            db.commit()
        finally:
            db.close()
    
    async def get_session(self, session_id: str) -> Optional[AgentSession]:
        db = self.SessionLocal()
        try:
            db_session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
            if not db_session:
                return None
            
            return AgentSession(
                id=db_session.id,
                agent_type=db_session.agent_type,
                config=json.loads(db_session.config),
                status=AgentStatus(db_session.status),
                created_at=db_session.created_at,
                session_metadata=json.loads(db_session.session_metadata or "{}")  # Updated field name
            )
        finally:
            db.close()
    
    async def update_status(self, session_id: str, status: AgentStatus) -> None:
        db = self.SessionLocal()
        try:
            db.query(SessionModel).filter(SessionModel.id == session_id).update(
                {"status": status.value}
            )
            db.commit()
        finally:
            db.close()

class SQLEventStore(EventStore):
    def __init__(self, database_url: str):
        self.engine = sa.create_engine(database_url)
        self.SessionLocal = sessionmaker(bind=self.engine)
    
    async def store_event(self, event: AgentEvent) -> None:
        db = self.SessionLocal()
        try:
            db_event = EventModel(
                session_id=event.session_id,
                event_type=event.event_type,
                data=json.dumps(event.data),
                timestamp=event.timestamp
            )
            db.add(db_event)
            db.commit()
        finally:
            db.close()
    
    async def get_events(self, session_id: str) -> List[AgentEvent]:
        db = self.SessionLocal()
        try:
            events = db.query(EventModel).filter(
                EventModel.session_id == session_id
            ).order_by(EventModel.timestamp).all()
            
            return [
                AgentEvent(
                    session_id=event.session_id,
                    event_type=event.event_type,
                    data=json.loads(event.data),
                    timestamp=event.timestamp
                )
                for event in events
            ]
        finally:
            db.close()

class WebSocketNotificationService(NotificationService):
    def __init__(self):
        self.connections: Dict[str, set] = {}
    
    def add_connection(self, session_id: str, websocket):
        if session_id not in self.connections:
            self.connections[session_id] = set()
        self.connections[session_id].add(websocket)
    
    def remove_connection(self, session_id: str, websocket):
        if session_id in self.connections:
            self.connections[session_id].discard(websocket)
            if not self.connections[session_id]:
                del self.connections[session_id]
    
    async def notify_session(self, session_id: str, message: Dict[str, Any]) -> None:
        if session_id in self.connections:
            dead_connections = set()
            for websocket in self.connections[session_id].copy():
                try:
                    await websocket.send_text(json.dumps(message))
                except Exception:
                    dead_connections.add(websocket)
            
            # Clean up dead connections
            for dead_ws in dead_connections:
                self.remove_connection(session_id, dead_ws)

class InMemoryPubSub(PubSub):
    def __init__(self):
        self.subscribers: Dict[str, List] = {}
    
    async def publish(self, channel: str, message: Dict[str, Any]) -> None:
        if channel in self.subscribers:
            for callback in self.subscribers[channel]:
                try:
                    await callback(message)
                except Exception as e:
                    print(f"Error in pub/sub callback: {e}")
    
    async def subscribe(self, channel: str, callback) -> None:
        if channel not in self.subscribers:
            self.subscribers[channel] = []
        self.subscribers[channel].append(callback)

    async def unsubscribe(self, channel: str) -> None:
        if channel in self.subscribers:
            del self.subscribers[channel]

class InMemoryTaskQueue(TaskQueue):
    def __init__(self, agent_worker: AgentWorker):
        self.agent_worker = agent_worker
    
    async def enqueue_agent(self, session_id: str, agent_type: str, config: Dict[str, Any]) -> None:
        # For testing, run immediately instead of background task
        asyncio.create_task(self.agent_worker.execute_agent(session_id, agent_type, config))
    
    async def cancel_agent(self, session_id: str) -> None:
        # Simple implementation for testing
        pass

# =============================================================================
# TRANSPORT LAYER - FastAPI
# =============================================================================

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

class CreateAgentRequest(BaseModel):
    agent_type: str
    config: Dict[str, Any]

def create_app(agent_service: AgentService, notification_service: WebSocketNotificationService) -> FastAPI:
    app = FastAPI(title="Agent Orchestration System")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],  
        allow_headers=["*"],
    )
    
    @app.post("/agents")
    async def create_agent(request: CreateAgentRequest):
        session_id = await agent_service.create_agent(
            request.agent_type, 
            request.config
        )
        return {
            "session_id": session_id,
            "websocket_url": f"ws://localhost:8000/ws/{session_id}"
        }
    
    @app.post("/agents/{session_id}/stop")
    async def stop_agent(session_id: str):
        await agent_service.stop_agent(session_id)
        return {"message": "Agent stopped"}
    
    @app.get("/agents/{session_id}")
    async def get_agent_status(session_id: str):
        session = await agent_service.get_session_status(session_id)
        if not session:
            return {"error": "Session not found"}, 404
        return {
            "session_id": session.id,
            "status": session.status.value,
            "agent_type": session.agent_type
        }
    
    @app.websocket("/ws/{session_id}")
    async def websocket_endpoint(websocket: WebSocket, session_id: str):
        await websocket.accept()
        notification_service.add_connection(session_id, websocket)
        
        # Send session history on connect
        session = await agent_service.get_session_status(session_id)
        if session:
            events = await agent_service.event_store.get_events(session_id)
            await websocket.send_text(json.dumps({
                "type": "session_history",
                "events": [
                    {
                        "event_type": e.event_type,
                        "data": e.data,
                        "timestamp": e.timestamp.isoformat()
                    }
                    for e in events
                ]
            }))
        
        try:
            while True:
                # Receive message from user
                data = await websocket.receive_text()
                try:
                    message = json.loads(data)
                    # Send user message via clean interface
                    await agent_service.send_user_message(session_id, message)
                    # Acknowledge receipt
                    await websocket.send_text(json.dumps({
                        "type": "message_received",
                        "message": "Message received"
                    }))
                except json.JSONDecodeError:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": "Invalid JSON format"
                    }))

        except WebSocketDisconnect:
            notification_service.remove_connection(session_id, websocket)
            
            session = await agent_service.get_session_status(session_id)
            if session and session.status == AgentStatus.RUNNING:
                await agent_service.repository.update_status(session_id, AgentStatus.STOPPED)
                print(f"User disconnected, marked session {session_id} as stopped")
    
    return app

# =============================================================================
# MAIN APPLICATION
# =============================================================================

def main():
    # Configuration
    DATABASE_URL = "sqlite:///agents.db"
    
    # Create infrastructure instances
    repository = SQLAgentRepository(DATABASE_URL)
    event_store = SQLEventStore(DATABASE_URL)
    notification_service = WebSocketNotificationService()
    pubsub = InMemoryPubSub()
    
    # Setup agent registry
    registry = AgentRegistry()
    registry.register("example", ExampleAgent)
    
    # Create services
    agent_service = AgentService(
        repository=repository,
        event_store=event_store,
        notification_service=notification_service,
        task_queue=None,
        pubsub=pubsub
    )
    
    # Create agent worker and task queue
    agent_worker = AgentWorker(agent_service, registry)
    task_queue = InMemoryTaskQueue(agent_worker)
    # TODO: resolve dependencies issue with task_queue
    agent_service.task_queue = task_queue
    
    print(f"Available agent types: {registry.get_registered_types()}")
    
    # Create FastAPI app
    app = create_app(agent_service, notification_service)
    
    import uvicorn
    print("Starting Agent Orchestration System...")
    print("API docs available at: http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    main()