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
from infrastructure.llm.llm_call_logger import LlmCallLogger
from infrastructure.llm.openai_responses_client import (
    OpenAIResponsesVisionClient,
)
from infrastructure.llm.retry_policy import RetryPolicy
from infrastructure.prompts.revision_rules_repository import (
    RevisionRulesRepository,
)
from infrastructure.prompts.yaml_prompt_repository import YamlPromptRepository

logger = logging.getLogger(__name__)


def _parse_origins(value: str) -> List[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def build_app(settings: Settings) -> FastAPI:
    prompt_repo = YamlPromptRepository(settings.prompts_yaml_path)
    schema_registry = SchemaRegistry()

    # Repositorio de reglas de revisión para fase 2.
    # Carga config/revision_rules.yaml. Si el archivo no existe o está
    # vacío, arranca con 0 reglas y la fase 2 sigue funcionando con
    # solo el prompt base (sin checklist).
    revision_rules_repo = RevisionRulesRepository(
        yaml_path=settings.revision_rules_yaml_path,
    )
    logger.info(
        "[svc2][wiring] Reglas de revisión cargadas: %d (%s)",
        revision_rules_repo.count,
        ", ".join(revision_rules_repo.rule_ids) or "(ninguna)",
    )

    retry_policy = RetryPolicy(
        max_retries=settings.llm_max_retries,
        backoff_base_s=settings.llm_backoff_base_s,
        backoff_cap_s=settings.llm_backoff_cap_s,
    )

    # ----------------------------------------------------------- #
    # IA call logger — para tuning de prompts.
    # Si IA_LOGGING_ENABLED=false, base_dir=None y todas las llamadas
    # a log_call() son no-op (cero coste, cero I/O).
    # ----------------------------------------------------------- #
    ia_log_dir = (
        settings.ia_logging_dir
        if settings.ia_logging_enabled else None
    )
    call_logger = LlmCallLogger(base_dir=ia_log_dir)
    logger.info(
        "[svc2][wiring] IA call logger %s (dir=%s)",
        "ACTIVO" if call_logger.enabled else "INACTIVO",
        settings.ia_logging_dir if call_logger.enabled else "n/a",
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
        revision_rules_repo=revision_rules_repo,
        prompt_key_phase_1=settings.prompt_key_fase1,
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
            "ia_logging": {
                "enabled": call_logger.enabled,
                "dir": settings.ia_logging_dir if call_logger.enabled else None,
            },
            "revision_rules": {
                "count": revision_rules_repo.count,
                "ids": revision_rules_repo.rule_ids,
                "yaml_path": settings.revision_rules_yaml_path,
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
    # Multipart: file (PDF/imagen) + Form phase_1_json (string)
    #            [+ Form sigrid_context_json (string, opcional)].
    #
    # sigrid_context_json (jun 2026): grounding determinista de la
    # cabecera contra Sigrid generado por sv3 y reenviado por sv7.
    # Si llega, se inyecta en el prompt de fase 2 para que la IA:
    #   - NO toque los bloques ya validados por CIF/código.
    #   - Use las listas de candidatos del ERP para casar lo no
    #     validado (nombre proveedor / obra leídos con OCR).
    # Si no llega (o es inválido), la fase 2 funciona como siempre.
    # ----------------------------------------------------------- #
    @app.post("/v1/albaranes/extract/phase-2")
    async def extract_phase_2(
        file: UploadFile = File(...),
        phase_1_json: str = Form(...),
        sigrid_context_json: str = Form(""),
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

        sigrid_context: Dict[str, Any] | None = None
        raw_ctx = (sigrid_context_json or "").strip()
        if raw_ctx:
            try:
                parsed_ctx = json.loads(raw_ctx)
                if isinstance(parsed_ctx, dict):
                    sigrid_context = parsed_ctx
                else:
                    logger.warning(
                        "[svc2] sigrid_context_json no es objeto JSON; "
                        "se ignora (fase 2 sin grounding)."
                    )
            except Exception:
                # Best-effort: un contexto malformado NUNCA rompe la
                # fase 2 — solo se pierde el grounding.
                logger.warning(
                    "[svc2] sigrid_context_json inválido; se ignora "
                    "(fase 2 sin grounding)."
                )

        try:
            return pipeline.run_phase_2(
                ReviewAlbaranRequest(
                    filename=file.filename or "document.bin",
                    mime_type=file.content_type or "application/octet-stream",
                    file_bytes=data,
                    phase_1_json=phase_1_payload,
                    sigrid_context=sigrid_context,
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

    # ----------------------------------------------------------- #
    # GET /v1/debug/ia-logs[?date=YYYYMMDD]
    # Lista los archivos JSON guardados por LlmCallLogger.
    # Útil para tuning de prompts: ves qué se mandó y qué respondió
    # cada modelo sin tener que abrir el explorador de archivos.
    # ----------------------------------------------------------- #
    @app.get("/v1/debug/ia-logs")
    def list_ia_logs(date: str | None = None) -> Dict[str, Any]:
        if not call_logger.enabled:
            return {
                "enabled": False,
                "message": (
                    "IA logging desactivado. Pon IA_LOGGING_ENABLED=true "
                    "en el .env para activarlo."
                ),
                "files": [],
            }
        from pathlib import Path as _Path
        base = _Path(settings.ia_logging_dir)
        if not base.exists():
            return {
                "enabled": True,
                "base_dir": str(base),
                "message": "Aún no hay logs (carpeta no creada).",
                "files": [],
            }

        # Si date no viene, listamos todas las subcarpetas (días).
        # Si viene, listamos solo los archivos de ese día.
        files: list[dict[str, Any]] = []
        days_iter = (
            [base / date] if date
            else sorted(
                [p for p in base.iterdir() if p.is_dir()],
                reverse=True,
            )
        )
        for day_dir in days_iter:
            if not day_dir.exists() or not day_dir.is_dir():
                continue
            for f in sorted(day_dir.glob("*.json")):
                stat = f.stat()
                files.append({
                    "filename": f.name,
                    "day": day_dir.name,
                    "size_bytes": stat.st_size,
                    "modified_utc": (
                        f"{stat.st_mtime:.0f}"
                    ),
                    "relative_path": str(f.relative_to(base)),
                })
        return {
            "enabled": True,
            "base_dir": str(base),
            "filter_date": date,
            "count": len(files),
            "files": files,
        }

    @app.get("/v1/debug/ia-logs/{day}/{filename}")
    def read_ia_log(day: str, filename: str) -> Dict[str, Any]:
        """Devuelve el contenido de un archivo de log concreto."""
        if not call_logger.enabled:
            raise HTTPException(
                status_code=404,
                detail="IA logging desactivado.",
            )
        from pathlib import Path as _Path
        # Sanitización básica para evitar path traversal.
        if "/" in filename or "\\" in filename or ".." in filename:
            raise HTTPException(status_code=400, detail="filename inválido")
        if "/" in day or "\\" in day or ".." in day:
            raise HTTPException(status_code=400, detail="day inválido")
        target = _Path(settings.ia_logging_dir) / day / filename
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="no encontrado")
        try:
            import json as _json
            with target.open("r", encoding="utf-8") as fp:
                return _json.load(fp)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"error leyendo log: {exc}",
            ) from exc

    return app
