# application/services/albaran_extraction_service.py
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Dict, Type

from pydantic import BaseModel

from application.services.schema_registry import SchemaRegistry
from domain.models.llm_attachment import LlmAttachment
from domain.ports.llm_client import LlmVisionClient
from domain.ports.prompt_repository import PromptRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderExtractionResult:
    provider: str
    model_name: str
    schema_name: str
    prompt_key: str
    parsed: BaseModel
    debug_payload: Dict[str, Any]


class AlbaranExtractionService:
    def __init__(
        self,
        *,
        openai_client: LlmVisionClient,
        gemini_client: LlmVisionClient,
        prompt_repo: PromptRepository,
        schema_registry: SchemaRegistry,
        openai_model: str,
        gemini_model: str,
        prompt_key: str,
    ) -> None:
        self._openai = openai_client
        self._gemini = gemini_client
        self._prompts = prompt_repo
        self._schemas = schema_registry
        self._openai_model = openai_model
        self._gemini_model = gemini_model
        self._prompt_key = prompt_key

    @staticmethod
    def _attachment_debug(attachment: LlmAttachment) -> Dict[str, Any]:
        return {
            "kind": attachment.kind,
            "filename": attachment.filename,
            "mime_type": attachment.mime_type,
            "size_bytes": len(attachment.data),
            "sha256": hashlib.sha256(attachment.data).hexdigest(),
        }

    def _extract_with_provider(
        self,
        *,
        provider: str,
        client: LlmVisionClient,
        model_name: str,
        instructions: str,
        user_text: str,
        attachment: LlmAttachment,
        response_model: Type[BaseModel],
        schema_name: str,
    ) -> ProviderExtractionResult:
        parsed = client.extract_document(
            model=model_name,
            instructions=instructions,
            user_text=user_text,
            attachment=attachment,
            response_model=response_model,
        )
        debug_payload: Dict[str, Any] = {
            f"{provider}_request": {
                "provider": provider,
                "model": model_name,
                "prompt_key": self._prompt_key,
                "instructions": instructions,
                "user_text": user_text,
                "response_schema_name": schema_name,
                "response_model_name": response_model.__name__,
                "response_schema_json": response_model.model_json_schema(),
                "attachment": self._attachment_debug(attachment),
            },
            f"{provider}_response": {
                "provider": provider,
                "model": model_name,
                "parsed": parsed.model_dump(),
            },
        }
        return ProviderExtractionResult(
            provider=provider,
            model_name=model_name,
            schema_name=schema_name,
            prompt_key=self._prompt_key,
            parsed=parsed,
            debug_payload=debug_payload,
        )

    def extract(
        self,
        attachment: LlmAttachment,
    ) -> tuple[ProviderExtractionResult, ProviderExtractionResult]:
        spec = self._prompts.get(self._prompt_key)
        response_model: Type[BaseModel] = self._schemas.get(spec.schema)
        logger.info(
            "Extracción albarán dual. prompt_key=%s schema=%s openai_model=%s gemini_model=%s filename=%s",
            self._prompt_key,
            spec.schema,
            self._openai_model,
            self._gemini_model,
            attachment.filename,
        )
        user_text = "\n\n".join(
            part for part in [spec.task, spec.schema_hint] if part
        ).strip()

        openai_result = self._extract_with_provider(
            provider="openai",
            client=self._openai,
            model_name=self._openai_model,
            instructions=spec.system,
            user_text=user_text,
            attachment=attachment,
            response_model=response_model,
            schema_name=spec.schema,
        )
        gemini_result = self._extract_with_provider(
            provider="gemini",
            client=self._gemini,
            model_name=self._gemini_model,
            instructions=spec.system,
            user_text=user_text,
            attachment=attachment,
            response_model=response_model,
            schema_name=spec.schema,
        )
        return openai_result, gemini_result
