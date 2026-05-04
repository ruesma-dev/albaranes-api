# interface_adapters/api/app.py
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from application.pipelines.extract_albaran_pipeline import (
    ExtractAlbaranPipeline,
    ExtractAlbaranRequest,
    ReviewAlbaranRequest,
)
from application.services.albaran_extraction_service import (
    AlbaranExtractionService,
    ProviderClientSpec,
)
from application.services.schema_registry import SchemaRegistry
from config.settings import Settings
from infrastructure.llm.azure_document_intelligence_client import (
    AzureDocumentIntelligenceVisionClient,
)
from infrastructure.llm.claude_messages_client import (
    ClaudeMessagesVisionClient,
)
from infrastructure.llm.gemini_genai_client import GeminiGenAiVisionClient
from infrastructure.llm.google_document_ai_client import (
    GoogleDocumentAiVisionClient,
)
from infrastructure.llm.openai_responses_client import (
    OpenAIResponsesVisionClient,
)
from infrastructure.llm.retry_policy import RetryPolicy
from infrastructure.prompts.yaml_prompt_repository import YamlPromptRepository

logger = logging.getLogger(__name__)


def _parse_origins(value: str) -> List[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def build_app(settings: Settings) -> FastAPI:
    prompt_repo = YamlPromptRepository(settings.prompts_yaml_path)
    schema_registry = SchemaRegistry()

    retry_policy = RetryPolicy(
        max_retries=settings.llm_max_retries,
        backoff_base_s=settings.llm_backoff_base_s,
        backoff_cap_s=settings.llm_backoff_cap_s,
    )

    # ----------------------------------------------------------- #
    # Construcción de proveedores LLM (igual que antes — solo se
    # instancian los habilitados con flags ENABLE_*).
    # ----------------------------------------------------------- #
    providers: list[ProviderClientSpec] = []

    if settings.openai_enabled:
        providers.append(
            ProviderClientSpec(
                provider="openai",
                model_name=settings.openai_model,
                client=OpenAIResponsesVisionClient(
                    settings.openai_api_key,
                    retry_policy=retry_policy,
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
        "[svc2][wiring] Proveedores LLM cargados: %s | "
        "FASE 1=%s · FASE 2=%s",
        [p.provider for p in providers] or "(ninguno)",
        settings.ia_primera_fase,
        settings.ia_segunda_fase,
    )

    extraction_service = AlbaranExtractionService(
        providers=providers,
        prompt_repo=prompt_repo,
        schema_registry=schema_registry,
    )
    pipeline = ExtractAlbaranPipeline(
        extraction_service=extraction_service,
        max_file_mb=settings.max_file_mb,
        service_version=settings.service_version,
        provider_phase_1=settings.ia_primera_fase,
        provider_phase_2=settings.ia_segunda_fase,
        prompt_key_phase_1=settings.prompt_key_fase1,
        prompt_key_phase_2=settings.prompt_key_fase2,
    )

    app = FastAPI(
        title="Albaranes Extractor API (2-fase)",
        version=settings.service_version,
    )

    if settings.cors_allow_origins:
        origins = _parse_origins(settings.cors_allow_origins)
        if origins:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return {
            "ok": True,
            "service": "albaranes-extractor-api",
            "version": settings.service_version,
            "providers_loaded": [
                {
                    "provider": p.provider,
                    "model": p.model_name,
                    "prompt_supported": p.prompt_supported,
                }
                for p in providers
            ],
            "phase_routing": {
                "phase_1": {
                    "provider": settings.ia_primera_fase,
                    "prompt_key": settings.prompt_key_fase1,
                },
                "phase_2": {
                    "provider": settings.ia_segunda_fase,
                    "prompt_key": settings.prompt_key_fase2,
                },
            },
            "retry_policy": {
                "max_retries": settings.llm_max_retries,
                "backoff_base_s": settings.llm_backoff_base_s,
                "backoff_cap_s": settings.llm_backoff_cap_s,
            },
        }

    # ----------------------------------------------------------- #
    # POST /v1/albaranes/extract/phase-1
    # ----------------------------------------------------------- #
    @app.post("/v1/albaranes/extract/phase-1")
    async def extract_phase_1(file: UploadFile = File(...)) -> Dict[str, Any]:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="Archivo vacío.")

        try:
            return pipeline.run_phase_1(
                ExtractAlbaranRequest(
                    filename=file.filename or "document.bin",
                    mime_type=file.content_type or "application/octet-stream",
                    file_bytes=data,
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Error en extract_phase_1")
            raise HTTPException(
                status_code=500,
                detail=f"Error en fase 1: {exc}",
            ) from exc

    # ----------------------------------------------------------- #
    # POST /v1/albaranes/extract/phase-2
    # Multipart: file (PDF/imagen) + Form phase_1_json (string).
    # ----------------------------------------------------------- #
    @app.post("/v1/albaranes/extract/phase-2")
    async def extract_phase_2(
        file: UploadFile = File(...),
        phase_1_json: str = Form(...),
    ) -> Dict[str, Any]:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="Archivo vacío.")

        try:
            phase_1_payload = json.loads(phase_1_json)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"phase_1_json inválido: {exc}",
            ) from exc
        if not isinstance(phase_1_payload, dict):
            raise HTTPException(
                status_code=400,
                detail="phase_1_json debe ser un objeto JSON.",
            )

        try:
            return pipeline.run_phase_2(
                ReviewAlbaranRequest(
                    filename=file.filename or "document.bin",
                    mime_type=file.content_type or "application/octet-stream",
                    file_bytes=data,
                    phase_1_json=phase_1_payload,
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Error en extract_phase_2")
            raise HTTPException(
                status_code=500,
                detail=f"Error en fase 2: {exc}",
            ) from exc

    # ----------------------------------------------------------- #
    # POST /v1/albaranes/extract  (LEGACY — alias de phase-1).
    # Mantiene la compatibilidad con clientes que aún no se han
    # migrado al endpoint /phase-1. Recomendable retirarlo una vez
    # toda la cadena esté actualizada.
    # ----------------------------------------------------------- #
    @app.post("/v1/albaranes/extract")
    async def extract_legacy(file: UploadFile = File(...)) -> Dict[str, Any]:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="Archivo vacío.")

        logger.info(
            "[svc2][legacy] /v1/albaranes/extract → redirigido a phase_1"
        )
        try:
            return pipeline.run_phase_1(
                ExtractAlbaranRequest(
                    filename=file.filename or "document.bin",
                    mime_type=file.content_type or "application/octet-stream",
                    file_bytes=data,
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Error: {exc}",
            ) from exc

    return app
