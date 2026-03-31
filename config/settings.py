# config/settings.py
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    openai_model: str = Field("gpt-5", alias="OPENAI_MODEL")
    prompt_key: str = Field("albaran_factura_es", alias="PROMPT_KEY")
    prompts_yaml_path: str = Field(
        "config/prompts.yaml",
        alias="PROMPTS_YAML_PATH",
    )
    api_host: str = Field("127.0.0.1", alias="API_HOST")
    api_port: int = Field(8000, alias="API_PORT")
    max_file_mb: int = Field(25, alias="MAX_FILE_MB")
    cors_allow_origins: str | None = Field(None, alias="CORS_ALLOW_ORIGINS")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_dir: str = Field("logs", alias="LOG_DIR")
    service_version: str = Field("1.0.0", alias="SERVICE_VERSION")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
