# Agent Orchestration Framework POC

A production-ready system for serving stateful agents with real-time communication, session persistence, and conversation memory.

## Features

- **Real-time bidirectional communication** via WebSockets + pub/sub
- **Persistent sessions** that survive server restarts with full conversation history  
- **Event-driven architecture** with complete audit trails
- **Hexagonal design** - swap databases, message queues, or notification systems
- **Automatic conversation context** - agents remember previous interactions
- **Clean agent lifecycle** with proper resource management
- **Framework-agnostic** - wrap any AI framework (LangChain, CrewAI, custom)

## Quick Start

```bash
pip install fastapi uvicorn sqlalchemy websockets
python agent_system.py
```

Open `test_client.html` in your browser. Create an agent, chat with it, disconnect and reconnect - your conversation persists.

## Architecture

- **Domain Layer**: Agent interfaces, event models
- **Application Layer**: AgentService orchestration  
- **Infrastructure Layer**: SQL, Redis, WebSocket adapters
- **Transport Layer**: FastAPI REST + WebSocket APIs
