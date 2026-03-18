"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Vertex AI
    google_cloud_project: str = "thermal-battery-agent-ds1"
    google_cloud_location: str = "us-central1"

    # BigQuery
    bq_project: str = "thermal-battery-agent-ds1"
    bq_dataset: str = "thermal_battery_data"

    # PostgreSQL
    database_url: str = "postgresql://resl_user:resl_password@localhost:5432/resl_agent"

    # ChromaDB
    chroma_host: str = "localhost"
    chroma_port: int = 8001

    # App
    app_env: str = "development"
    cors_origins: str = "http://localhost:5173,http://localhost:5174,http://localhost:3000"

    class Config:
        env_file = ".env"
        extra = "ignore"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]

    @property
    def bq_full_dataset(self) -> str:
        return f"{self.bq_project}.{self.bq_dataset}"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
