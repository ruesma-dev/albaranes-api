# application/services/albaran_extraction_service.py
"""Servicio de extracción y revisión de albaranes (sv2).

Tiene DOS responsabilidades, expuestas como métodos distintos:

  - ``extract_phase_1``: ejecuta UN proveedor LLM contra la imagen
    para producir un ``DocumentoAlbaran`` (extracción inicial).

  - ``review_phase_2``: ejecuta UN proveedor LLM contra la imagen +
    el JSON de fase 1 para producir un ``RevisionAlbaranFase2``
    (patch de cambios sugeridos).

El elegir QUÉ proveedor se usa en cada fase lo gobierna la capa
superior (interface_adapters/api/app.py) leyendo ``IA_PRIMERA_FASE``
e ``IA_SEGUNDA_FASE`` del .env.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Type

from pydantic import BaseModel

from application.services.schema_registry import SchemaRegistry
from domain.models.llm_attachment import LlmAttachment
from domain.ports.llm_client import LlmVisionClient
from domain.ports.prompt_repository import PromptRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderClientSpec:
    """Spec de un proveedor LLM disponible en este servicio."""
    provider: str
    model_name: str
    client: LlmVisionClient
    prompt_supported: bool = True


@dataclass(frozen=True)
class ProviderExtractionResult:
    """Resultado de invocar UN proveedor LLM (fase 1 o fase 2)."""
    provider: str
    model_name: str
    schema_name: str
    prompt_key: str
    parsed: BaseModel
    debug_payload: Dict[str, Any]


class AlbaranExtractionService:
    """Servicio que orquesta la llamada a UN proveedor LLM concreto.

    Expone métodos por fase (extract_phase_1, review_phase_2). Cada
    invocación elige UN proveedor por nombre del catálogo de
    ProviderClientSpec.
    """

    def __init__(
        self,
        *,
        providers: Iterable[ProviderClientSpec],
        prompt_repo: PromptRepository,
        schema_registry: SchemaRegistry,
    ) -> None:
        self._providers_by_name: Dict[str, ProviderClientSpec] = {
            spec.provider: spec for spec in providers
        }
        self._prompts = prompt_repo
        self._schemas = schema_registry

    # ---------------------------------------------------------- #
    # FASE 1 — extracción inicial.
    # ---------------------------------------------------------- #
    def extract_phase_1(
        self,
        *,
        attachment: LlmAttachment,
        provider: str,
        prompt_key: str,
    ) -> ProviderExtractionResult:
        spec = self._require_provider(provider)
        prompt_spec = self._prompts.get(prompt_key)
        response_model = self._schemas.get(prompt_spec.schema)

        instructions = self._build_instructions(prompt_spec)
        user_text = (
            "Documento adjunto. Extrae el albarán siguiendo las reglas "
            "del prompt. Devuelve SOLO JSON válido conforme al schema."
        )

        logger.info(
            "Extracción FASE 1 proveedor=%s prompt_key=%s schema=%s "
            "model=%s filename=%s",
            spec.provider, prompt_key, prompt_spec.schema,
            spec.model_name, attachment.filename,
        )

        return self._invoke_provider(
            spec=spec,
            instructions=instructions,
            user_text=user_text,
            attachment=attachment,
            response_model=response_model,
            schema_name=prompt_spec.schema,
            prompt_key=prompt_key,
            phase_label="phase_1",
        )

    # ---------------------------------------------------------- #
    # FASE 2 — revisión.
    # ---------------------------------------------------------- #
    def review_phase_2(
        self,
        *,
        attachment: LlmAttachment,
        provider: str,
        prompt_key: str,
        phase_1_json: dict,
    ) -> ProviderExtractionResult:
        spec = self._require_provider(provider)
        prompt_spec = self._prompts.get(prompt_key)
        response_model = self._schemas.get(prompt_spec.schema)

        instructions = self._build_instructions(prompt_spec)

        # El user_text de fase 2 lleva el JSON de fase 1 EMBEBIDO
        # como contexto. La imagen del adjunto se pasa por el
        # mecanismo nativo del proveedor (igual que en fase 1).
        json_str = json.dumps(phase_1_json, ensure_ascii=False, indent=2)
        user_text = (
            "Documento adjunto: la imagen / PDF original del albarán.\n\n"
            "JSON producido por la fase 1 (extracción inicial):\n"
            "```json\n"
            f"{json_str}\n"
            "```\n\n"
            "Tu tarea: revisar el JSON contra la imagen siguiendo los "
            "patrones definidos en el prompt y devolver un PATCH con "
            "los cambios que propones."
        )

        logger.info(
            "Revisión FASE 2 proveedor=%s prompt_key=%s schema=%s "
            "model=%s filename=%s json_fase1_chars=%d",
            spec.provider, prompt_key, prompt_spec.schema,
            spec.model_name, attachment.filename, len(json_str),
        )

        return self._invoke_provider(
            spec=spec,
            instructions=instructions,
            user_text=user_text,
            attachment=attachment,
            response_model=response_model,
            schema_name=prompt_spec.schema,
            prompt_key=prompt_key,
            phase_label="phase_2",
            extra_debug={"phase_1_json": phase_1_json},
        )

    # ---------------------------------------------------------- #
    # Helpers internos.
    # ---------------------------------------------------------- #
    def _require_provider(self, name: str) -> ProviderClientSpec:
        spec = self._providers_by_name.get(name)
        if spec is None:
            available = ", ".join(sorted(self._providers_by_name.keys()))
            raise KeyError(
                f"Proveedor LLM '{name}' no instanciado en este servicio. "
                f"Disponibles: {available}. Revisa los flags ENABLE_* en "
                f"el .env."
            )
        return spec

    @staticmethod
    def _build_instructions(prompt_spec) -> str:
        # Concatenamos system + task + schema_hint para el system prompt
        # del proveedor (como hacía la versión anterior). Cada provider
        # client decidirá cómo lo distribuye en su API concreta.
        parts = [prompt_spec.system, prompt_spec.task, prompt_spec.schema_hint]
        return "\n\n".join(p for p in parts if p)

    def _invoke_provider(
        self,
        *,
        spec: ProviderClientSpec,
        instructions: str,
        user_text: str,
        attachment: LlmAttachment,
        response_model: Type[BaseModel],
        schema_name: str,
        prompt_key: str,
        phase_label: str,
        extra_debug: Optional[Dict[str, Any]] = None,
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
                "La API de este proveedor no acepta un prompt arbitrario "
                "por petición; se conserva el mismo prompt para "
                "trazabilidad pero la extracción la gobierna el "
                "modelo/procesador configurado."
            )

        debug_payload: Dict[str, Any] = {
            f"{spec.provider}_request": {
                "provider": spec.provider,
                "model": spec.model_name,
                "prompt_key": prompt_key,
                "phase": phase_label,
                "attachment": self._attachment_debug(attachment),
                "prompt_note": prompt_note,
            },
            f"{spec.provider}_response": {
                "schema": schema_name,
                "parsed_keys": (
                    list(parsed.model_dump().keys())
                    if hasattr(parsed, "model_dump") else []
                ),
            },
        }
        if extra_debug:
            debug_payload.update(extra_debug)

        return ProviderExtractionResult(
            provider=spec.provider,
            model_name=spec.model_name,
            schema_name=schema_name,
            prompt_key=prompt_key,
            parsed=parsed,
            debug_payload=debug_payload,
        )

    @staticmethod
    def _attachment_debug(attachment: LlmAttachment) -> Dict[str, Any]:
        return {
            "kind": attachment.kind,
            "filename": attachment.filename,
            "mime_type": attachment.mime_type,
            "size_bytes": len(attachment.data),
            "sha256": hashlib.sha256(attachment.data).hexdigest(),
        }
