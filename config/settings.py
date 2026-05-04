# config/settings.py
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Proveedores válidos para fase 1 / fase 2. Cualquier valor fuera de
# esta lista hace fallar el arranque con mensaje claro.
PhaseProvider = Literal["openai", "gemini", "claude"]


class Settings(BaseSettings):
    # ------------------------------------------------------------
    # Flags de habilitación por proveedor LLM.
    # ------------------------------------------------------------
    openai_enabled: bool = Field(True, alias="ENABLE_OPENAI")
    gemini_enabled: bool = Field(True, alias="ENABLE_GEMINI")
    claude_enabled: bool = Field(True, alias="ENABLE_CLAUDE")

    openai_api_key: str | None = Field(None, alias="OPENAI_API_KEY")
    openai_model: str = Field("gpt-5", alias="OPENAI_MODEL")
    gemini_api_key: str | None = Field(None, alias="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-2.5-flash", alias="GEMINI_MODEL")

    anthropic_api_key: str | None = Field(None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(
        "claude-sonnet-4-5",
        alias="ANTHROPIC_MODEL",
    )
    anthropic_max_tokens: int = Field(8192, alias="ANTHROPIC_MAX_TOKENS")
    anthropic_timeout_s: int = Field(120, alias="ANTHROPIC_TIMEOUT_S")

    google_document_ai_enabled: bool = Field(False, alias="GOOGLE_DOCUMENT_AI_ENABLED")
    google_document_ai_project_id: str | None = Field(None, alias="GOOGLE_DOCUMENT_AI_PROJECT_ID")
    google_document_ai_location: str = Field("eu", alias="GOOGLE_DOCUMENT_AI_LOCATION")
    google_document_ai_processor_id: str | None = Field(None, alias="GOOGLE_DOCUMENT_AI_PROCESSOR_ID")
    google_document_ai_processor_version: str | None = Field(None, alias="GOOGLE_DOCUMENT_AI_PROCESSOR_VERSION")
    google_application_credentials: str | None = Field(None, alias="GOOGLE_APPLICATION_CREDENTIALS")

    azure_document_intelligence_enabled: bool = Field(False, alias="AZURE_DOCUMENT_INTELLIGENCE_ENABLED")
    azure_document_intelligence_endpoint: str | None = Field(None, alias="AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
    azure_document_intelligence_key: str | None = Field(None, alias="AZURE_DOCUMENT_INTELLIGENCE_KEY")
    azure_document_intelligence_model_id: str = Field("prebuilt-invoice", alias="AZURE_DOCUMENT_INTELLIGENCE_MODEL_ID")
    azure_document_intelligence_api_version: str = Field("2024-11-30", alias="AZURE_DOCUMENT_INTELLIGENCE_API_VERSION")
    azure_document_intelligence_timeout_s: int = Field(120, alias="AZURE_DOCUMENT_INTELLIGENCE_TIMEOUT_S")

    # --------------------------------------------------------------
    # Política de reintentos común para los clientes LLM.
    # --------------------------------------------------------------
    llm_max_retries: int = Field(2, alias="LLM_MAX_RETRIES")
    llm_backoff_base_s: float = Field(2.0, alias="LLM_BACKOFF_BASE_S")
    llm_backoff_cap_s: float = Field(30.0, alias="LLM_BACKOFF_CAP_S")

    # --------------------------------------------------------------
    # FLOW de extracción de albarán.
    #
    # Hoy soportamos dos endpoints DIFERENCIADOS en el API:
    #   POST /v1/albaranes/extract/phase-1   → llama UN solo proveedor
    #   POST /v1/albaranes/extract/phase-2   → revisión con UN solo
    #                                          proveedor + JSON fase 1
    #
    # Y un endpoint LEGACY:
    #   POST /v1/albaranes/extract           → equivalente a phase-1
    #                                          (compatibilidad).
    #
    # Las variables IA_PRIMERA_FASE / IA_SEGUNDA_FASE definen QUÉ
    # proveedor se usa en cada fase. Cada uno DEBE estar habilitado
    # (ENABLE_*=true) y tener su API key. La validación cruzada de
    # abajo lo comprueba.
    # --------------------------------------------------------------
    ia_primera_fase: PhaseProvider = Field(
        "gemini",
        alias="IA_PRIMERA_FASE",
        description="Proveedor LLM que ejecuta la extracción inicial.",
    )
    ia_segunda_fase: PhaseProvider = Field(
        "openai",
        alias="IA_SEGUNDA_FASE",
        description="Proveedor LLM que revisa la extracción y propone correcciones.",
    )

    # Prompts (uno por fase). Ambos viven en config/prompts.yaml.
    prompt_key_fase1: str = Field(
        "albaran_factura_es",
        alias="PROMPT_KEY_FASE1",
    )
    prompt_key_fase2: str = Field(
        "albaran_revision_fase2_es",
        alias="PROMPT_KEY_FASE2",
    )

    # Backwards-compat: si alguien tenía PROMPT_KEY=... (antes de las dos
    # fases), lo seguimos leyendo para forzar el de fase 1. Si está
    # presente y no coincide con prompt_key_fase1, ganan los nuevos.
    prompt_key: str = Field("albaran_factura_es", alias="PROMPT_KEY")
    prompts_yaml_path: str = Field("config/prompts.yaml", alias="PROMPTS_YAML_PATH")
    api_host: str = Field("127.0.0.1", alias="API_HOST")
    api_port: int = Field(8000, alias="API_PORT")
    max_file_mb: int = Field(25, alias="MAX_FILE_MB")
    cors_allow_origins: str | None = Field(None, alias="CORS_ALLOW_ORIGINS")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_dir: str = Field("logs", alias="LOG_DIR")
    service_version: str = Field("1.0.0", alias="SERVICE_VERSION")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --------------------------------------------------------------
    # Validaciones cruzadas.
    # --------------------------------------------------------------
    @model_validator(mode="after")
    def _ensure_at_least_one_provider_enabled(self) -> "Settings":
        enabled = {
            "openai": self.openai_enabled,
            "gemini": self.gemini_enabled,
            "claude": self.claude_enabled,
        }
        if not any(enabled.values()):
            raise ValueError(
                "Al menos un proveedor LLM debe estar habilitado. "
                "Revisa ENABLE_OPENAI / ENABLE_GEMINI / ENABLE_CLAUDE en el .env."
            )
        return self

    @model_validator(mode="after")
    def _ensure_api_keys_for_enabled_providers(self) -> "Settings":
        missing: list[str] = []
        if self.openai_enabled and not (self.openai_api_key or "").strip():
            missing.append("OPENAI_API_KEY (ENABLE_OPENAI=true)")
        if self.gemini_enabled and not (self.gemini_api_key or "").strip():
            missing.append("GEMINI_API_KEY (ENABLE_GEMINI=true)")
        if self.claude_enabled and not (self.anthropic_api_key or "").strip():
            missing.append("ANTHROPIC_API_KEY (ENABLE_CLAUDE=true)")
        if missing:
            raise ValueError(
                "Faltan API keys para los proveedores habilitados: "
                + ", ".join(missing)
            )
        return self

    @model_validator(mode="after")
    def _ensure_phase_providers_enabled(self) -> "Settings":
        """Cada fase exige que su proveedor esté habilitado."""
        flag_by_provider = {
            "openai": self.openai_enabled,
            "gemini": self.gemini_enabled,
            "claude": self.claude_enabled,
        }
        if not flag_by_provider.get(self.ia_primera_fase, False):
            raise ValueError(
                f"IA_PRIMERA_FASE={self.ia_primera_fase} pero su flag "
                f"ENABLE_{self.ia_primera_fase.upper()} no está a true. "
                "Habilítalo o cambia IA_PRIMERA_FASE."
            )
        if not flag_by_provider.get(self.ia_segunda_fase, False):
            raise ValueError(
                f"IA_SEGUNDA_FASE={self.ia_segunda_fase} pero su flag "
                f"ENABLE_{self.ia_segunda_fase.upper()} no está a true. "
                "Habilítalo o cambia IA_SEGUNDA_FASE."
            )
        return self

    @property
    def enabled_llm_providers(self) -> list[str]:
        out: list[str] = []
        if self.openai_enabled:
            out.append("openai")
        if self.gemini_enabled:
            out.append("gemini")
        if self.claude_enabled:
            out.append("claude")
        return out
