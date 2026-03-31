# application/pipelines/extract_albaran_pipeline.py
from __future__ import annotations

import hashlib
import logging
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict

from application.services.albaran_extraction_service import (
    AlbaranExtractionService,
)
from domain.models.llm_attachment import LlmAttachment

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractAlbaranRequest:
    filename: str
    mime_type: str
    file_bytes: bytes


class ExtractAlbaranPipeline:
    def __init__(
        self,
        *,
        extraction_service: AlbaranExtractionService,
        model_name: str,
        max_file_mb: int,
        service_version: str,
    ) -> None:
        self._service = extraction_service
        self._model_name = model_name
        self._max_file_mb = max_file_mb
        self._service_version = service_version

    @staticmethod
    def _utc_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _build_attachment(
        filename: str,
        mime_type: str,
        file_bytes: bytes,
    ) -> LlmAttachment:
        filename = filename or "document.bin"
        is_pdf = (
            mime_type == "application/pdf"
            or filename.lower().endswith(".pdf")
        )
        if is_pdf:
            return LlmAttachment(
                kind="pdf",
                filename=filename,
                mime_type="application/pdf",
                data=file_bytes,
            )

        guessed_mime, _ = mimetypes.guess_type(filename)
        final_mime = mime_type or guessed_mime or "image/jpeg"
        return LlmAttachment(
            kind="image",
            filename=filename,
            mime_type=final_mime,
            data=file_bytes,
        )

    def run(self, request: ExtractAlbaranRequest) -> Dict[str, Any]:
        if not request.file_bytes:
            raise ValueError("Archivo vacío.")

        size_mb = len(request.file_bytes) / (1024 * 1024)
        if size_mb > self._max_file_mb:
            raise ValueError(
                f"Archivo demasiado grande ({size_mb:.2f} MB) > "
                f"MAX_FILE_MB={self._max_file_mb}"
            )

        attachment = self._build_attachment(
            filename=request.filename,
            mime_type=request.mime_type,
            file_bytes=request.file_bytes,
        )
        parsed, schema_name, prompt_key, debug_payload = self._service.extract(
            attachment
        )
        sha256 = hashlib.sha256(request.file_bytes).hexdigest()

        envelope = {
            "meta": {
                "prompt_key": prompt_key,
                "schema": schema_name,
                "source_filename": attachment.filename,
                "source_mime_type": attachment.mime_type,
                "source_sha256": sha256,
                "model": self._model_name,
                "processed_at_utc": self._utc_iso(),
                "service": "albaranes-extractor-api",
                "service_version": self._service_version,
            },
            "data": parsed.model_dump(),
            "debug": debug_payload,
        }
        return envelope
