# config/settings.py
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    openai_model: str = Field("gpt-5", alias="OPENAI_MODEL")
    gemini_api_key: str = Field(..., alias="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-2.5-flash", alias="GEMINI_MODEL")

    anthropic_api_key: str = Field(..., alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(
        "claude-sonnet-4-5",
        alias="ANTHROPIC_MODEL",
    )
    anthropic_max_tokens: int = Field(
        8192,
        alias="ANTHROPIC_MAX_TOKENS",
    )
    anthropic_timeout_s: int = Field(
        120,
        alias="ANTHROPIC_TIMEOUT_S",
    )

    google_document_ai_enabled: bool = Field(
        False,
        alias="GOOGLE_DOCUMENT_AI_ENABLED",
    )
    google_document_ai_project_id: str | None = Field(
        None,
        alias="GOOGLE_DOCUMENT_AI_PROJECT_ID",
    )
    google_document_ai_location: str = Field(
        "eu",
        alias="GOOGLE_DOCUMENT_AI_LOCATION",
    )
    google_document_ai_processor_id: str | None = Field(
        None,
        alias="GOOGLE_DOCUMENT_AI_PROCESSOR_ID",
    )
    google_document_ai_processor_version: str | None = Field(
        None,
        alias="GOOGLE_DOCUMENT_AI_PROCESSOR_VERSION",
    )
    google_application_credentials: str | None = Field(
        None,
        alias="GOOGLE_APPLICATION_CREDENTIALS",
    )

    azure_document_intelligence_enabled: bool = Field(
        False,
        alias="AZURE_DOCUMENT_INTELLIGENCE_ENABLED",
    )
    azure_document_intelligence_endpoint: str | None = Field(
        None,
        alias="AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT",
    )
    azure_document_intelligence_key: str | None = Field(
        None,
        alias="AZURE_DOCUMENT_INTELLIGENCE_KEY",
    )
    azure_document_intelligence_model_id: str = Field(
        "prebuilt-invoice",
        alias="AZURE_DOCUMENT_INTELLIGENCE_MODEL_ID",
    )
    azure_document_intelligence_api_version: str = Field(
        "2024-11-30",
        alias="AZURE_DOCUMENT_INTELLIGENCE_API_VERSION",
    )
    azure_document_intelligence_timeout_s: int = Field(
        120,
        alias="AZURE_DOCUMENT_INTELLIGENCE_TIMEOUT_S",
    )

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
