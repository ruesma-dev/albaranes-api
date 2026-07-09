# interface_adapters/composition.py
"""Composition root del pipeline de extracción de sv2.

Extrae la construcción del ``ExtractAlbaranPipeline`` (antes embebida en
``build_app``) para poder reutilizarla desde DOS entradas:

  - la API HTTP (``interface_adapters/api/app.py``), comportamiento actual.
  - el worker de cola (``main_worker.py``), nuevo: consume ``q-extraccion``.

El wiring es idéntico al de ``build_app`` (mismos flags ENABLE_*, mismos
clientes LLM, mismas reglas de revisión); solo se ha movido aquí.
"""
from __future__ import annotations

import logging
import os

from application.pipelines.extract_albaran_pipeline import ExtractAlbaranPipeline
from application.services.albaran_extraction_service import (
    AlbaranExtractionService,
    ProviderClientSpec,
)
from application.services.schema_registry import SchemaRegistry
from config.settings import Settings
from infrastructure.llm.azure_document_intelligence_client import (
    AzureDocumentIntelligenceVisionClient,
)
from infrastructure.llm.claude_messages_client import ClaudeMessagesVisionClient
from infrastructure.llm.gemini_genai_client import GeminiGenAiVisionClient
from infrastructure.llm.google_document_ai_client import (
    GoogleDocumentAiVisionClient,
)
from infrastructure.llm.llm_call_logger import LlmCallLogger
from infrastructure.llm.openai_responses_client import OpenAIResponsesVisionClient
from infrastructure.llm.retry_policy import RetryPolicy
from infrastructure.prompts.revision_rules_repository import RevisionRulesRepository
from infrastructure.prompts.yaml_prompt_repository import YamlPromptRepository

logger = logging.getLogger(__name__)


def build_pipeline(settings: Settings) -> ExtractAlbaranPipeline:
    """Construye el pipeline de extracción (fase 1 + fase 2)."""
    prompt_repo = YamlPromptRepository(settings.prompts_yaml_path)
    schema_registry = SchemaRegistry()

    revision_rules_repo = RevisionRulesRepository(
        yaml_path=settings.revision_rules_yaml_path,
    )
    logger.info(
        "[svc2][wiring] Reglas de revision cargadas: %d (%s)",
        revision_rules_repo.count,
        ", ".join(revision_rules_repo.rule_ids) or "(ninguna)",
    )

    retry_policy = RetryPolicy(
        max_retries=settings.llm_max_retries,
        backoff_base_s=settings.llm_backoff_base_s,
        backoff_cap_s=settings.llm_backoff_cap_s,
    )

    ia_log_dir = settings.ia_logging_dir if settings.ia_logging_enabled else None
    call_logger = LlmCallLogger(base_dir=ia_log_dir)
    logger.info(
        "[svc2][wiring] IA call logger %s (dir=%s)",
        "ACTIVO" if call_logger.enabled else "INACTIVO",
        settings.ia_logging_dir if call_logger.enabled else "n/a",
    )

    providers: list[ProviderClientSpec] = []

    if settings.openai_enabled:
        providers.append(
            ProviderClientSpec(
                provider="openai",
                model_name=settings.openai_model,
                client=OpenAIResponsesVisionClient(
                    settings.openai_api_key,
                    retry_policy=retry_policy,
                    call_logger=call_logger,
                ),
                prompt_supported=True,
            )
        )

    if settings.gemini_enabled:
        providers.append(
            ProviderClientSpec(
                provider="gemini",
                model_name=settings.gemini_model,
                client=GeminiGenAiVisionClient(
                    settings.gemini_api_key,
                    retry_policy=retry_policy,
                    call_logger=call_logger,
                ),
                prompt_supported=True,
            )
        )

    if settings.claude_enabled:
        providers.append(
            ProviderClientSpec(
                provider="claude",
                model_name=settings.anthropic_model,
                client=ClaudeMessagesVisionClient(
                    api_key=settings.anthropic_api_key,
                    max_tokens=settings.anthropic_max_tokens,
                    timeout_s=settings.anthropic_timeout_s,
                    retry_policy=retry_policy,
                    call_logger=call_logger,
                    tool_name="emit_albaran_extraction",
                    tool_description=(
                        "Devuelve la extraccion estructurada del albaran/factura "
                        "conforme al esquema exigido. Debes llamar SIEMPRE a esta "
                        "herramienta y solo a ella."
                    ),
                ),
                prompt_supported=True,
            )
        )

    if settings.google_document_ai_enabled:
        if settings.google_application_credentials:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = (
                settings.google_application_credentials
            )
        if not settings.google_document_ai_project_id:
            raise RuntimeError(
                "GOOGLE_DOCUMENT_AI_ENABLED=true exige GOOGLE_DOCUMENT_AI_PROJECT_ID."
            )
        if not settings.google_document_ai_processor_id:
            raise RuntimeError(
                "GOOGLE_DOCUMENT_AI_ENABLED=true exige GOOGLE_DOCUMENT_AI_PROCESSOR_ID."
            )
        google_client = GoogleDocumentAiVisionClient(
            project_id=settings.google_document_ai_project_id,
            location=settings.google_document_ai_location,
            processor_id=settings.google_document_ai_processor_id,
            processor_version=settings.google_document_ai_processor_version,
        )
        providers.append(
            ProviderClientSpec(
                provider="google_document_ai",
                model_name=google_client.model_name,
                client=google_client,
                prompt_supported=False,
            )
        )

    if settings.azure_document_intelligence_enabled:
        if not settings.azure_document_intelligence_endpoint:
            raise RuntimeError(
                "AZURE_DOCUMENT_INTELLIGENCE_ENABLED=true exige "
                "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT."
            )
        if not settings.azure_document_intelligence_key:
            raise RuntimeError(
                "AZURE_DOCUMENT_INTELLIGENCE_ENABLED=true exige "
                "AZURE_DOCUMENT_INTELLIGENCE_KEY."
            )
        azure_client = AzureDocumentIntelligenceVisionClient(
            endpoint=settings.azure_document_intelligence_endpoint,
            api_key=settings.azure_document_intelligence_key,
            model_id=settings.azure_document_intelligence_model_id,
            api_version=settings.azure_document_intelligence_api_version,
            timeout_s=settings.azure_document_intelligence_timeout_s,
        )
        providers.append(
            ProviderClientSpec(
                provider="azure_document_intelligence",
                model_name=azure_client.model_name,
                client=azure_client,
                prompt_supported=False,
            )
        )

    logger.info(
        "[svc2][wiring] Proveedores LLM cargados: %s | FASE 1=%s . FASE 2=%s",
        [p.provider for p in providers] or "(ninguno)",
        settings.ia_primera_fase,
        settings.ia_segunda_fase,
    )

    extraction_service = AlbaranExtractionService(
        providers=providers,
        prompt_repo=prompt_repo,
        schema_registry=schema_registry,
        revision_rules_repo=revision_rules_repo,
        prompt_key_phase_1=settings.prompt_key_fase1,
    )
    return ExtractAlbaranPipeline(
        extraction_service=extraction_service,
        max_file_mb=settings.max_file_mb,
        service_version=settings.service_version,
        provider_phase_1=settings.ia_primera_fase,
        provider_phase_2=settings.ia_segunda_fase,
        prompt_key_phase_1=settings.prompt_key_fase1,
        prompt_key_phase_2=settings.prompt_key_fase2,
    )
