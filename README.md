# RESL Thermal Battery AI Agent

AI-powered assistant for thermal battery design, testing, and manufacturing analysis. Built for **Renewable Energy Systems Limited (RESL)**.

## Features

- 🔋 **BigQuery Integration** — Query battery/build data from `thermal-battery-agent-ds1.thermal_battery_data`
- 🧪 **Electrochemistry & Physics Analysis** — Discharge curve analysis, thermal profiling, capacity estimation, C-rate calculations
- 📋 **Rulebook Management** — Add, edit, delete rules manually or upload documents (PDF/DOCX/TXT). Rules are stored in PostgreSQL and synced to ChromaDB for semantic search
- 🔍 **Rules Transparency** — Every response shows which rules the agent applied
- 🧠 **Deep Think** — Extended reasoning mode for complex questions
- 🔬 **Deep Research** — Multi-step investigation across multiple batteries and builds
- 💬 **ChatGPT-like UI** — Dark-themed, premium interface with chat history and rulebook panel

## Tech Stack

| Component | Technology |
|-----------|-----------|
| LLM | Vertex AI Gemini 2.0 Flash |
| Agent Framework | Google ADK (Agent Development Kit) |
| Backend | FastAPI (Python) |
| Frontend | React + Vite |
| Data Store | BigQuery |
| App Database | PostgreSQL |
| Vector DB | ChromaDB |
| Deployment | Docker + Docker Compose |

## Quick Start

### Prerequisites

- Python 3.12+
- Node.js 18+
- Docker & Docker Compose
- Google Cloud SDK (`gcloud`)
- GCP project `thermal-battery-agent-ds1` with Vertex AI API enabled

### 1. Authenticate with GCP

```bash
gcloud auth application-default login
```

### 2. Start with Docker Compose

```bash
docker-compose up
```

This starts 4 services:
- **PostgreSQL** (port 5432)
- **ChromaDB** (port 8001)
- **Backend** (port 8000)
- **Frontend** (port 3000)

### 3. Access the App

Open http://localhost:3000

### Local Development (without Docker)

#### Backend

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env  # Edit with your settings
uvicorn main:app --reload
```

#### Frontend

```bash
cd frontend
npm install
npm run dev
```

## API Endpoints

### Chat
- `POST /api/chat/send` — Send message, get AI response
- `POST /api/chat/new` — Create new conversation
- `GET /api/chat/{id}` — Get conversation messages
- `DELETE /api/chat/{id}` — Delete conversation

### History
- `GET /api/history/` — List conversations
- `GET /api/history/search?q=...` — Search history

### Rulebook
- `GET /api/rules/` — List rules
- `POST /api/rules/` — Add rule (→ PG + ChromaDB)
- `PUT /api/rules/{id}` — Update rule (→ PG + ChromaDB re-sync)
- `DELETE /api/rules/{id}` — Delete rule (→ PG + ChromaDB)
- `POST /api/rules/upload` — Upload document to extract rules
- `POST /api/rules/rebuild-vectors` — Full PG → ChromaDB re-sync

## BigQuery Schema

Project: `thermal-battery-agent-ds1` | Dataset: `thermal_battery_data`

| Table | Description |
|-------|-------------|
| `customer_specs` | Battery specifications per battery |
| `design_parameters` | Design parameters per build per battery |
| `discharge_data` | Time-series discharge test data (large!) |
| `temperature_data` | 3-sensor temperature readings |

## License

Proprietary — RESL Internal Use Only
