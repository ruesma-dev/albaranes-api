# infrastructure/llm/azure_document_intelligence_client.py
from __future__ import annotations

import logging
import time
from typing import Any, Optional, Sequence, Type

import httpx
from pydantic import BaseModel

from domain.models.llm_attachment import LlmAttachment
from domain.ports.llm_client import LlmVisionClient
from ruesma_comun.llm.llm_client import normalizar_adjuntos

logger = logging.getLogger(__name__)


class AzureDocumentIntelligenceVisionClient(LlmVisionClient):
    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model_id: str,
        api_version: str,
        timeout_s: int,
        poll_interval_s: float = 1.0,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._model_id = model_id
        self._api_version = api_version
        self._timeout_s = int(timeout_s)
        self._poll_interval_s = float(poll_interval_s)
        self._client = httpx.Client(timeout=self._timeout_s)

    @property
    def model_name(self) -> str:
        return f"azure-document-intelligence:{self._model_id}"

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
                "Azure Document Intelligence requiere un adjunto (no soporta texto puro)."
            )
        if len(adjuntos) > 1:
            logger.warning(
                "Azure Document Intelligence: %s adjuntos recibidos; solo se analiza "
                "el primero (%s)",
                len(adjuntos),
                adjuntos[0].filename,
            )
        adjunto = adjuntos[0]
        logger.info(
            "Azure Document Intelligence call. model=%s kind=%s filename=%s mime=%s size=%s schema=%s",
            model,
            adjunto.kind,
            adjunto.filename,
            adjunto.mime_type,
            len(adjunto.data),
            response_model.__name__,
        )
        result = self._analyze_document(adjunto)
        payload = self._map_result(result)
        return response_model.model_validate(payload)

    def _analyze_document(self, attachment: LlmAttachment) -> dict[str, Any]:
        url = (
            f"{self._endpoint}/documentintelligence/documentModels/"
            f"{self._model_id}:analyze"
        )
        headers = {
            "Ocp-Apim-Subscription-Key": self._api_key,
            "Content-Type": attachment.mime_type,
        }
        params = {"api-version": self._api_version}
        response = self._client.post(
            url,
            params=params,
            headers=headers,
            content=attachment.data,
        )
        if response.status_code == 200:
            return response.json()
        if response.status_code not in {201, 202}:
            raise RuntimeError(
                f"Azure Document Intelligence analyze {response.status_code}: "
                f"{response.text[:800]}"
            )

        operation_url = (
            response.headers.get("operation-location")
            or response.headers.get("Operation-Location")
            or ""
        ).strip()
        if not operation_url:
            raise RuntimeError(
                "Azure Document Intelligence no devolvió Operation-Location."
            )

        deadline = time.monotonic() + self._timeout_s
        while True:
            poll = self._client.get(
                operation_url,
                headers={"Ocp-Apim-Subscription-Key": self._api_key},
            )
            if poll.status_code >= 300:
                raise RuntimeError(
                    f"Azure Document Intelligence poll {poll.status_code}: "
                    f"{poll.text[:800]}"
                )
            payload = poll.json()
            status = str(payload.get("status") or "").lower()
            if status == "succeeded":
                return payload
            if status == "failed":
                raise RuntimeError(
                    f"Azure Document Intelligence analysis failed: {payload}"
                )
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Azure Document Intelligence polling timed out."
                )
            time.sleep(self._poll_interval_s)

    def _map_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        analyze_result = payload.get("analyzeResult") or payload
        documents = analyze_result.get("documents") or []
        document = documents[0] if documents else {}
        fields = document.get("fields") or {}

        header = {
            "proveedor_nombre": self._field_text(
                fields,
                "VendorName",
                "SellerName",
                "SupplierName",
            ),
            "proveedor_cif": self._field_text(
                fields,
                "VendorTaxId",
                "SellerTaxId",
                "SupplierTaxId",
            ),
            "fecha": self._field_text(
                fields,
                "InvoiceDate",
                "IssueDate",
                "Date",
            ),
            "numero_albaran": self._field_text(
                fields,
                "InvoiceId",
                "InvoiceNumber",
                "ReceiptId",
                "DocumentNumber",
            ),
            "forma_pago": self._field_text(
                fields,
                "PaymentTerms",
                "PaymentTerm",
                "PaymentMethod",
            ),
            "obra_codigo": self._field_text(
                fields,
                "ProjectCode",
                "JobCode",
                "WorksiteCode",
            ),
            "obra_nombre": self._field_text(
                fields,
                "CustomerName",
                "ProjectName",
                "JobName",
                "ShipToName",
            ),
            "obra_direccion": self._field_text(
                fields,
                "CustomerAddress",
                "ProjectAddress",
                "JobAddress",
                "ShipToAddress",
                "DeliveryAddress",
            ),
        }

        items = self._field_array(fields, "Items", "LineItems")
        lines: list[dict[str, Any]] = []
        for index, item in enumerate(items, start=1):
            value_object = item.get("valueObject") or {}
            line = {
                "id": str(index),
                "codigo": self._field_text(value_object, "ProductCode", "ItemCode", "Code"),
                "cantidad": self._field_number(value_object, "Quantity", "Qty"),
                "concepto": self._field_text(value_object, "Description", "ItemDescription", "Name"),
                "precio": self._field_number(value_object, "UnitPrice", "Price", "UnitCost"),
                "descuento": self._field_number(value_object, "Discount", "DiscountAmount", "DiscountRate"),
                "precio_neto": self._field_number(value_object, "Amount", "LineAmount", "TotalPrice"),
                "codigo_imputacion": self._field_text(value_object, "CostCode", "AllocationCode", "ImputationCode"),
                "confianza_pct": self._confidence_pct(item),
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
    def _field_text(fields: dict[str, Any], *names: str) -> str | None:
        for name in names:
            field = fields.get(name)
            if not isinstance(field, dict):
                continue
            for key in (
                "valueString",
                "valueDate",
                "valuePhoneNumber",
                "valueCurrency",
                "content",
            ):
                value = field.get(key)
                if key == "valueCurrency" and isinstance(value, dict):
                    amount = value.get("amount")
                    if amount not in (None, ""):
                        return str(amount)
                elif value not in (None, ""):
                    return str(value)
        return None

    @staticmethod
    def _field_number(fields: dict[str, Any], *names: str) -> float | None:
        for name in names:
            field = fields.get(name)
            if not isinstance(field, dict):
                continue
            value = field.get("valueNumber")
            if value not in (None, ""):
                try:
                    return float(value)
                except Exception:
                    pass
            currency = field.get("valueCurrency")
            if isinstance(currency, dict):
                amount = currency.get("amount")
                if amount not in (None, ""):
                    try:
                        return float(amount)
                    except Exception:
                        pass
            content = field.get("content")
            if content not in (None, ""):
                try:
                    return float(str(content).replace(",", "."))
                except Exception:
                    continue
        return None

    @staticmethod
    def _field_array(fields: dict[str, Any], *names: str) -> list[dict[str, Any]]:
        for name in names:
            field = fields.get(name)
            if not isinstance(field, dict):
                continue
            array_value = field.get("valueArray")
            if isinstance(array_value, list):
                return [item for item in array_value if isinstance(item, dict)]
        return []

    @staticmethod
    def _confidence_pct(field: dict[str, Any]) -> float | None:
        raw = field.get("confidence")
        if raw is None:
            return None
        try:
            return round(max(0.0, min(100.0, float(raw) * 100.0)), 2)
        except Exception:
            return None
