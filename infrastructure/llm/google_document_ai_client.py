# infrastructure/llm/google_document_ai_client.py
from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Optional, Sequence, Type

from google.api_core.client_options import ClientOptions
from google.cloud import documentai
from pydantic import BaseModel

from domain.models.llm_attachment import LlmAttachment
from domain.ports.llm_client import LlmVisionClient
from ruesma_comun.llm.llm_client import normalizar_adjuntos

logger = logging.getLogger(__name__)

_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "proveedor_nombre": (
        "supplier_name",
        "vendor_name",
        "seller_name",
        "merchant_name",
        "supplier",
        "vendor",
    ),
    "proveedor_cif": (
        "supplier_tax_id",
        "vendor_tax_id",
        "seller_tax_id",
        "supplier_vat_id",
        "vendor_vat_id",
        "tax_id",
        "vat_id",
        "supplier_cif",
        "vendor_cif",
        "supplier_nif",
        "vendor_nif",
    ),
    "fecha": (
        "invoice_date",
        "issue_date",
        "document_date",
        "date",
    ),
    "numero_albaran": (
        "invoice_id",
        "invoice_number",
        "invoice_no",
        "document_id",
        "document_number",
        "delivery_note_number",
        "delivery_note_id",
        "reference_number",
    ),
    "forma_pago": (
        "payment_terms",
        "payment_term",
        "payment_method",
        "terms",
    ),
    "obra_codigo": (
        "project_code",
        "job_code",
        "worksite_code",
        "site_code",
    ),
    "obra_nombre": (
        "project_name",
        "job_name",
        "worksite_name",
        "site_name",
        "buyer_name",
        "ship_to_name",
    ),
    "obra_direccion": (
        "project_address",
        "job_address",
        "worksite_address",
        "site_address",
        "ship_to_address",
        "delivery_address",
    ),
}

_LINE_ALIASES: dict[str, tuple[str, ...]] = {
    "codigo": (
        "product_code",
        "item_code",
        "sku",
        "code",
    ),
    "cantidad": ("quantity", "qty"),
    "concepto": (
        "description",
        "item_description",
        "product_name",
        "name",
    ),
    "precio": ("unit_price", "price", "unit_cost"),
    "descuento": (
        "discount",
        "discount_amount",
        "discount_rate",
        "discount_percent",
    ),
    "precio_neto": (
        "amount",
        "line_item_amount",
        "total_amount",
        "item_amount",
        "net_amount",
    ),
    "codigo_imputacion": (
        "cost_code",
        "project_item_code",
        "allocation_code",
        "imputation_code",
    ),
}


class GoogleDocumentAiVisionClient(LlmVisionClient):
    def __init__(
        self,
        *,
        project_id: str,
        location: str,
        processor_id: str,
        processor_version: str | None = None,
    ) -> None:
        self._project_id = project_id
        self._location = location
        self._processor_id = processor_id
        self._processor_version = processor_version
        self._client = documentai.DocumentProcessorServiceClient(
            client_options=ClientOptions(
                api_endpoint=f"{location}-documentai.googleapis.com"
            )
        )

    @property
    def model_name(self) -> str:
        if self._processor_version:
            return (
                "google-document-ai:"
                f"processors/{self._processor_id}/versions/{self._processor_version}"
            )
        return f"google-document-ai:processors/{self._processor_id}"

    def _processor_name(self) -> str:
        if self._processor_version:
            return (
                f"projects/{self._project_id}/locations/{self._location}/"
                f"processors/{self._processor_id}/processorVersions/"
                f"{self._processor_version}"
            )
        return (
            f"projects/{self._project_id}/locations/{self._location}/"
            f"processors/{self._processor_id}"
        )

    def extract_document(
        self,
        *,
        model: str,
        instructions: str,
        user_text: str,
        attachment: Optional[LlmAttachment] = None,
        attachments: Optional[Sequence[LlmAttachment]] = None,
        response_model: Type[BaseModel],
    ) -> BaseModel:
        # (jul 2026) Este proveedor OCR analiza UN documento por
        # peticion: si llegan varias paginas (imagen por pagina del
        # preprocesado), se usa la PRIMERA y se avisa. Los proveedores
        # LLM (claude/openai/gemini) si aprovechan todas las paginas.
        adjuntos = normalizar_adjuntos(attachment, attachments)
        if not adjuntos:
            raise ValueError(
                "Google Document AI requiere un adjunto (no soporta texto puro)."
            )
        if len(adjuntos) > 1:
            logger.warning(
                "Google Document AI: %s adjuntos recibidos; solo se analiza "
                "el primero (%s)",
                len(adjuntos),
                adjuntos[0].filename,
            )
        adjunto = adjuntos[0]
        logger.info(
            "Google Document AI call. model=%s kind=%s filename=%s mime=%s size=%s schema=%s",
            model,
            adjunto.kind,
            adjunto.filename,
            adjunto.mime_type,
            len(adjunto.data),
            response_model.__name__,
        )
        request = documentai.ProcessRequest(
            name=self._processor_name(),
            raw_document=documentai.RawDocument(
                content=adjunto.data,
                mime_type=adjunto.mime_type,
            ),
        )
        result = self._client.process_document(request=request)
        payload = self._map_document(result.document)
        return response_model.model_validate(payload)

    def _map_document(self, document: documentai.Document) -> dict[str, Any]:
        text = str(getattr(document, "text", "") or "")
        entities = list(getattr(document, "entities", []) or [])

        header: dict[str, Any] = {}
        for field_name, aliases in _HEADER_ALIASES.items():
            header[field_name] = self._best_entity_value(
                entities=entities,
                aliases=aliases,
                full_text=text,
            )

        line_items = [
            entity
            for entity in entities
            if self._is_line_item_entity(entity)
        ]
        lines: list[dict[str, Any]] = []
        for index, entity in enumerate(line_items, start=1):
            props = list(getattr(entity, "properties", []) or [])
            line = {
                "id": str(index),
                "codigo": self._best_entity_value(
                    entities=props,
                    aliases=_LINE_ALIASES["codigo"],
                    full_text=text,
                ),
                "cantidad": self._to_float(
                    self._best_entity_value(
                        entities=props,
                        aliases=_LINE_ALIASES["cantidad"],
                        full_text=text,
                    )
                ),
                "concepto": self._best_entity_value(
                    entities=props,
                    aliases=_LINE_ALIASES["concepto"],
                    full_text=text,
                ),
                "precio": self._to_float(
                    self._best_entity_value(
                        entities=props,
                        aliases=_LINE_ALIASES["precio"],
                        full_text=text,
                    )
                ),
                "descuento": self._to_float(
                    self._best_entity_value(
                        entities=props,
                        aliases=_LINE_ALIASES["descuento"],
                        full_text=text,
                    )
                ),
                "precio_neto": self._to_float(
                    self._best_entity_value(
                        entities=props,
                        aliases=_LINE_ALIASES["precio_neto"],
                        full_text=text,
                    )
                ),
                "codigo_imputacion": self._best_entity_value(
                    entities=props,
                    aliases=_LINE_ALIASES["codigo_imputacion"],
                    full_text=text,
                ),
                "confianza_pct": self._confidence_pct(entity),
            }
            if any(
                line[key] is not None
                for key in (
                    "codigo",
                    "cantidad",
                    "concepto",
                    "precio",
                    "descuento",
                    "precio_neto",
                    "codigo_imputacion",
                )
            ):
                lines.append(line)

        return {"cabecera": header, "lineas": lines}

    @staticmethod
    def _normalize_type(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")

    def _entity_type(self, entity: Any) -> str:
        return str(getattr(entity, "type_", "") or getattr(entity, "type", "") or "")

    def _is_line_item_entity(self, entity: Any) -> bool:
        entity_type = self._normalize_type(self._entity_type(entity))
        return entity_type in {"line_item", "item", "items"} or "line_item" in entity_type

    def _best_entity_value(
        self,
        *,
        entities: Iterable[Any],
        aliases: Iterable[str],
        full_text: str,
    ) -> Any:
        alias_set = {self._normalize_type(alias) for alias in aliases}
        best_score = -1.0
        best_value: Any = None

        for entity in entities:
            entity_type = self._normalize_type(self._entity_type(entity))
            score = self._type_score(entity_type, alias_set)
            if score <= 0:
                continue
            value = self._entity_value(entity, full_text)
            if value in (None, ""):
                continue
            confidence = self._confidence_pct(entity) or 0.0
            total = score * 1000.0 + confidence
            if total > best_score:
                best_score = total
                best_value = value

        return best_value

    @staticmethod
    def _type_score(entity_type: str, alias_set: set[str]) -> float:
        if entity_type in alias_set:
            return 3.0
        for alias in alias_set:
            if alias and (entity_type.endswith(alias) or alias in entity_type):
                return 2.0
            if entity_type and alias.startswith(entity_type):
                return 1.0
        return 0.0

    def _entity_value(self, entity: Any, full_text: str) -> Any:
        mention_text = str(getattr(entity, "mention_text", "") or "").strip()
        if mention_text:
            return mention_text

        normalized_value = getattr(entity, "normalized_value", None)
        if normalized_value is not None:
            text_value = str(getattr(normalized_value, "text", "") or "").strip()
            if text_value:
                return text_value
            for attr_name in (
                "float_value",
                "integer_value",
                "boolean_value",
            ):
                attr_value = getattr(normalized_value, attr_name, None)
                if attr_value not in (None, ""):
                    return attr_value
            money_value = getattr(normalized_value, "money_value", None)
            if money_value is not None:
                units = getattr(money_value, "units", None)
                nanos = getattr(money_value, "nanos", None)
                try:
                    return float(units or 0) + float(nanos or 0) / 1_000_000_000.0
                except Exception:
                    pass

        text_anchor = getattr(entity, "text_anchor", None)
        segments = getattr(text_anchor, "text_segments", None) or []
        if not segments:
            return None
        parts: list[str] = []
        for segment in segments:
            start_index = int(getattr(segment, "start_index", 0) or 0)
            end_index = int(getattr(segment, "end_index", 0) or 0)
            if 0 <= start_index < end_index <= len(full_text):
                part = full_text[start_index:end_index].strip()
                if part:
                    parts.append(part)
        return " ".join(parts).strip() or None

    @staticmethod
    def _confidence_pct(entity: Any) -> float | None:
        raw = getattr(entity, "confidence", None)
        if raw is None:
            return None
        try:
            return round(max(0.0, min(100.0, float(raw) * 100.0)), 2)
        except Exception:
            return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None or value == "":
            return None
        if isinstance(value, (int, float)):
            return float(value)
        candidate = str(value).strip().replace(" ", "")
        if not candidate:
            return None
        if "," in candidate and "." in candidate:
            if candidate.rfind(",") > candidate.rfind("."):
                candidate = candidate.replace(".", "").replace(",", ".")
            else:
                candidate = candidate.replace(",", "")
        elif "," in candidate:
            candidate = candidate.replace(",", ".")
        try:
            return float(candidate)
        except ValueError:
            return None
