# application/services/albaran_extraction_service.py
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Type

from pydantic import BaseModel

from application.services.schema_registry import SchemaRegistry
from domain.models.llm_attachment import LlmAttachment
from domain.ports.llm_client import LlmVisionClient
from domain.ports.prompt_repository import PromptRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderClientSpec:
    provider: str
    model_name: str
    client: LlmVisionClient
    prompt_supported: bool = True


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
        providers: Iterable[ProviderClientSpec],
        prompt_repo: PromptRepository,
        schema_registry: SchemaRegistry,
        prompt_key: str,
    ) -> None:
        self._providers = list(providers)
        self._prompts = prompt_repo
        self._schemas = schema_registry
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
        spec: ProviderClientSpec,
        instructions: str,
        user_text: str,
        attachment: LlmAttachment,
        response_model: Type[BaseModel],
        schema_name: str,
    ) -> ProviderExtractionResult:
        parsed = spec.client.extract_document(
            model=spec.model_name,
            instructions=instructions,
            user_text=user_text,
            attachment=attachment,
            response_model=response_model,
        )
        prompt_note = None
        if not spec.prompt_supported:
            prompt_note = (
                "La API de este proveedor no acepta un prompt arbitrario por petición; "
                "se conserva el mismo prompt para trazabilidad, pero la extracción la "
                "gobierna el modelo/procesador configurado."
            )

        debug_payload: Dict[str, Any] = {
            f"{spec.provider}_request": {
                "provider": spec.provider,
                "model": spec.model_name,
                "prompt_key": self._prompt_key,
                "instructions": instructions,
                "user_text": user_text,
                "response_schema_name": schema_name,
                "response_model_name": response_model.__name__,
                "response_schema_json": response_model.model_json_schema(),
                "attachment": self._attachment_debug(attachment),
                "prompt_supported": spec.prompt_supported,
                "prompt_note": prompt_note,
            },
            f"{spec.provider}_response": {
                "provider": spec.provider,
                "model": spec.model_name,
                "parsed": parsed.model_dump(),
            },
        }
        return ProviderExtractionResult(
            provider=spec.provider,
            model_name=spec.model_name,
            schema_name=schema_name,
            prompt_key=self._prompt_key,
            parsed=parsed,
            debug_payload=debug_payload,
        )

    def extract(self, attachment: LlmAttachment) -> Dict[str, ProviderExtractionResult]:
        spec = self._prompts.get(self._prompt_key)
        response_model: Type[BaseModel] = self._schemas.get(spec.schema)
        user_text = "\n\n".join(
            part for part in [spec.task, spec.schema_hint] if part
        ).strip()

        results: Dict[str, ProviderExtractionResult] = {}
        for provider_spec in self._providers:
            logger.info(
                "Extracción albarán proveedor=%s prompt_key=%s schema=%s model=%s filename=%s",
                provider_spec.provider,
                self._prompt_key,
                spec.schema,
                provider_spec.model_name,
                attachment.filename,
            )
            results[provider_spec.provider] = self._extract_with_provider(
                spec=provider_spec,
                instructions=spec.system,
                user_text=user_text,
                attachment=attachment,
                response_model=response_model,
                schema_name=spec.schema,
            )
        return results
