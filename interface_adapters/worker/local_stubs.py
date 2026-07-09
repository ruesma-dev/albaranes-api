# interface_adapters/worker/local_stubs.py
"""Stubs LOCALES de los puertos del worker — para probar el plumbing.

No hablan con SharePoint ni con BBDD: leen un PDF de disco y escriben el
envelope como JSON en una carpeta. Sirven para validar end-to-end el worker
contra Azurite con un albarán real, ANTES de tener los adaptadores de
producción (SharePoint / PostgreSQL / grounding de sv3).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from interface_adapters.worker.ports import (
    DocumentoPdf,
    FuenteDocumento,
    GroundingCabecera,
    SumideroEnvelope,
)

logger = logging.getLogger(__name__)


class FuenteDocumentoLocal(FuenteDocumento):
    """Devuelve SIEMPRE el mismo PDF local (ignora document_id).

    Pensado para el piloto: pones un albarán en ``pdf_path`` y el worker lo
    procesa de verdad por la cola.
    """

    def __init__(self, pdf_path: str) -> None:
        self._pdf_path = Path(pdf_path)

    def obtener(self, document_id: str) -> DocumentoPdf:
        data = self._pdf_path.read_bytes()
        logger.info(
            "[fuente-local] document_id=%s -> %s (%d bytes)",
            document_id,
            self._pdf_path.name,
            len(data),
        )
        return DocumentoPdf(
            filename=self._pdf_path.name,
            mime_type="application/pdf",
            file_bytes=data,
        )


class GroundingNulo(GroundingCabecera):
    """Sin grounding (fase 2 clásica). Placeholder hasta mover el de sv3."""

    def contexto(self, *, document_id: str, phase_1_json: dict) -> dict | None:
        return None


class SumideroEnvelopeFichero(SumideroEnvelope):
    """Escribe el envelope como JSON en ``out_dir`` (en vez de a BBDD)."""

    def __init__(self, out_dir: str) -> None:
        self._dir = Path(out_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def persistir(self, *, document_id: str, envelope: dict, fase: str) -> None:
        path = self._dir / f"{document_id}_{fase}.json"
        path.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("[sumidero-fichero] envelope %s -> %s", fase, path)
