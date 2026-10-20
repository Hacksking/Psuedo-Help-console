"""Configuration via environment variables."""
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Anthropic / Claude configuration
    anthropic_api_key: str = ""
    anthropic_base_url: Optional[str] = None
    claude_model: str = "claude-sonnet-5-5"

    # GitHub integration for LIVE mode
    github_token: str = ""
    github_webhook_secret: str = ""

    # Public URL for webhook & commit status details link (e.g. ngrok HTTPS URL)
    public_base_url: str = "http://localhost:8000"

    # Mode: "simulate" (local fixtures) or "live" (real GitHub)
    demo_mode: str = "simulate"

    # Gemini API configuration for deep code analysis & remediation reports
    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-flash"
    smee_url: str = "https://smee.io/p7UW8GZbphsNnqU3"
    ngrok_url: str = ""
    custom_repo: str = ""

    # Cache AI results instead of calling Claude
    replay_ai: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
