"""Application configuration via Pydantic Settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Atlas application settings.

    Required fields have no defaults and will cause a validation error
    on startup if the corresponding env var is missing.
    """

    # --- App ---
    app_name: str = "atlas"
    app_version: str = "0.1.0"

    # --- GoHighLevel (required) ---
    ghl_api_key: str
    ghl_location_id: str = "l39XXt9HcdLTsuqTind6"
    ghl_pipeline_id: str = "V6mwUqamI0tGUm1GDvKD"

    # --- Calendly (required) ---
    calendly_api_key: str

    # --- Slack (required) ---
    slack_bot_token: str
    slack_signing_secret: str
    slack_webhook_url: str = ""

    # --- Anthropic (required) ---
    anthropic_api_key: str

    # --- Database ---
    database_path: str = "/app/data/atlas.db"

    # --- Logging ---
    log_json_format: bool = True
    log_level: str = "INFO"

    # --- Calendly webhook (optional for Phase 1) ---
    calendly_webhook_secret: str = ""

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance. Fails fast if required env vars missing."""
    return Settings()
