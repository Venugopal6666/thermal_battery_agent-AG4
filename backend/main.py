"""FastAPI main entry point for the RESL Thermal Battery AI Agent."""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from database import init_db
from routers import chat_router, history_router, rulebook_router

# Load environment variables
load_dotenv()

# Set Vertex AI env vars before any Google imports
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", get_settings().google_cloud_project)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", get_settings().google_cloud_location)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    # Startup
    print("RESL Thermal Battery AI Agent starting up...")
    print(f"   Environment: {settings.app_env}")
    print(f"   GCP Project: {settings.google_cloud_project}")
    print(f"   GCP Location: {settings.google_cloud_location}")
    print(f"   BigQuery Dataset: {settings.bq_full_dataset}")

    # Initialize database tables
    init_db()
    print("   PostgreSQL tables initialized")

    # Load existing rules into vector search index
    try:
        from database import SessionLocal
        from services.rulebook_service import rebuild_vector_store
        db = SessionLocal()
        try:
            count = rebuild_vector_store(db)
            print(f"   Loaded {count} rules into search index")
        finally:
            db.close()
    except Exception as e:
        print(f"   Warning: Could not load rules into search index: {e}")

    print("Agent is ready!")
    yield
    # Shutdown
    print("Agent shutting down...")


app = FastAPI(
    title="RESL Thermal Battery AI Agent",
    description="AI-powered assistant for thermal battery design, testing, and manufacturing analysis. Powered by Google Vertex AI (Gemini) and Agent Development Kit (ADK).",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(chat_router.router)
app.include_router(history_router.router)
app.include_router(rulebook_router.router)


@app.get("/")
async def root():
    return {
        "name": "RESL Thermal Battery AI Agent",
        "version": "1.0.0",
        "status": "running",
        "gcp_project": settings.google_cloud_project,
        "gcp_location": settings.google_cloud_location,
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}
