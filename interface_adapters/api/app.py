# interface_adapters/api/app.py
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from application.pipelines.extract_albaran_pipeline import (
    ExtractAlbaranPipeline,
    ExtractAlbaranRequest,
)
from application.services.albaran_extraction_service import (
    AlbaranExtractionService,
)
from application.services.schema_registry import SchemaRegistry
from config.settings import Settings
from infrastructure.llm.gemini_genai_client import GeminiGenAiVisionClient
from infrastructure.llm.openai_responses_client import (
    OpenAIResponsesVisionClient,
)
from infrastructure.prompts.yaml_prompt_repository import YamlPromptRepository


def _parse_origins(value: str) -> List[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def build_app(settings: Settings) -> FastAPI:
    prompt_repo = YamlPromptRepository(settings.prompts_yaml_path)
    schema_registry = SchemaRegistry()
    openai_client = OpenAIResponsesVisionClient(settings.openai_api_key)
    gemini_client = GeminiGenAiVisionClient(settings.gemini_api_key)

    extraction_service = AlbaranExtractionService(
        openai_client=openai_client,
        gemini_client=gemini_client,
        prompt_repo=prompt_repo,
        schema_registry=schema_registry,
        openai_model=settings.openai_model,
        gemini_model=settings.gemini_model,
        prompt_key=settings.prompt_key,
    )
    pipeline = ExtractAlbaranPipeline(
        extraction_service=extraction_service,
        openai_model_name=settings.openai_model,
        gemini_model_name=settings.gemini_model,
        max_file_mb=settings.max_file_mb,
        service_version=settings.service_version,
    )

    app = FastAPI(
        title="Albaranes Extractor API",
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
            "openai_model": settings.openai_model,
            "gemini_model": settings.gemini_model,
        }

    @app.post("/v1/albaranes/extract")
    async def extract(file: UploadFile = File(...)) -> Dict[str, Any]:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="Archivo vacío.")

        try:
            return pipeline.run(
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
                detail=f"Error extrayendo albarán: {exc}",
            ) from exc

    return app
