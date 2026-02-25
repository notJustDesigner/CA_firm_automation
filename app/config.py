"""
app/config.py
"""
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Dict
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = Field(..., env="DATABASE_URL")

    # JWT
    JWT_SECRET: str = Field(..., env="JWT_SECRET")
    JWT_EXPIRE_MINUTES: int = Field(480, env="JWT_EXPIRE_MINUTES")

    # Auth (single CA user)
    CA_USERNAME: str = Field(..., env="CA_USERNAME")
    CA_PASSWORD: str = Field(..., env="CA_PASSWORD")

    # Redis
    REDIS_URL: str = Field("redis://localhost:6379", env="REDIS_URL")

    # Ollama
    OLLAMA_BASE_URL: str = Field("http://localhost:11434", env="OLLAMA_BASE_URL")
    OLLAMA_MODEL: str = Field("qwen2.5-coder:3b", env="OLLAMA_MODEL")
    OLLAMA_FALLBACK_MODEL: str = Field("qwen2.5:3b", env="OLLAMA_FALLBACK_MODEL")

    # SMTP
    SMTP_HOST: str = Field("smtp.gmail.com", env="SMTP_HOST")
    SMTP_PORT: int = Field(465, env="SMTP_PORT")
    SMTP_USER: str = Field(..., env="SMTP_USER")
    SMTP_PASS: str = Field(..., env="SMTP_PASS")

    # File storage
    UPLOAD_DIR: str = Field("./uploads", env="UPLOAD_DIR")

    # Task-specific Ollama model routing
    OLLAMA_MODELS: Dict[str, str] = {
        "reasoning": "qwen2.5-coder:3b",
        "json_extraction": "qwen2.5:3b",
        "email_drafting": "qwen2.5:3b",
        "summarization": "qwen2.5:3b",
    }

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()