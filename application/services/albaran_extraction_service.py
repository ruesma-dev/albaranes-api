# application/services/albaran_extraction_service.py
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, Type

from pydantic import BaseModel

from application.services.schema_registry import SchemaRegistry
from domain.models.llm_attachment import LlmAttachment
from domain.ports.llm_client import LlmVisionClient
from domain.ports.prompt_repository import PromptRepository

logger = logging.getLogger(__name__)


class AlbaranExtractionService:
    def __init__(
        self,
        *,
        llm_client: LlmVisionClient,
        prompt_repo: PromptRepository,
        schema_registry: SchemaRegistry,
        model: str,
        prompt_key: str,
    ) -> None:
        self._llm = llm_client
        self._prompts = prompt_repo
        self._schemas = schema_registry
        self._model = model
        self._prompt_key = prompt_key

    def extract(
        self,
        attachment: LlmAttachment,
    ) -> tuple[BaseModel, str, str, Dict[str, Any]]:
        spec = self._prompts.get(self._prompt_key)
        response_model: Type[BaseModel] = self._schemas.get(spec.schema)
        logger.info(
            "Extracción albarán. prompt_key=%s schema=%s model=%s filename=%s",
            self._prompt_key,
            spec.schema,
            self._model,
            attachment.filename,
        )
        user_text = "\n\n".join(
            part for part in [spec.task, spec.schema_hint] if part
        ).strip()

        parsed = self._llm.extract_document(
            model=self._model,
            instructions=spec.system,
            user_text=user_text,
            attachment=attachment,
            response_model=response_model,
        )

        debug_payload: Dict[str, Any] = {
            "openai_request": {
                "provider": "openai",
                "api": "responses.parse",
                "model": self._model,
                "prompt_key": self._prompt_key,
                "instructions": spec.system,
                "user_text": user_text,
                "response_schema_name": spec.schema,
                "response_model_name": response_model.__name__,
                "response_schema_json": response_model.model_json_schema(),
                "attachment": {
                    "kind": attachment.kind,
                    "filename": attachment.filename,
                    "mime_type": attachment.mime_type,
                    "size_bytes": len(attachment.data),
                    "sha256": hashlib.sha256(attachment.data).hexdigest(),
                },
            },
            "openai_response": {
                "parsed": parsed.model_dump(),
            },
        }
        return parsed, spec.schema, self._prompt_key, debug_payload
