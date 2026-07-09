# interface_adapters/worker/blob_adapters.py
"""Adaptadores de PRODUCCIÓN de los puertos del worker de sv2 sobre Blob.

Sustituyen a los stubs de disco (``local_stubs.py``):
- :class:`FuenteDocumentoBlob`  lee ``input/{document_id}.pdf`` (lo dejó sv1).
- :class:`SumideroEnvelopeBlob` escribe ``envelopes/{document_id}_{fase}.json``
  para que el worker de sv3 lo recoja por ``document_id``.

El hand-off es por referencia: el mensaje de cola solo lleva ``document_id``.
Los blobs son EFÍMEROS (una lifecycle policy los purga); el dato durable vive
en SharePoint (PDF, lo sube sv3) y PostgreSQL (datos extraídos).
"""
from __future__ import annotations

import logging

from interface_adapters.worker.ports import (
    DocumentoPdf,
    FuenteDocumento,
    SumideroEnvelope,
)
from ruesma_comun.blobs import (
    CONTENEDOR_ENVELOPES,
    CONTENEDOR_INPUT,
    AlmacenBlobs,
)

logger = logging.getLogger(__name__)


class FuenteDocumentoBlob(FuenteDocumento):
    """Lee el PDF de entrada de ``input/{document_id}.pdf``.

    El nombre original del fichero y el mime se leen de la metadata del blob
    (los pone sv1 al subirlo). Si faltan, se asume ``{document_id}.pdf`` y
    ``application/pdf``.
    """

    def __init__(
        self,
        almacen: AlmacenBlobs,
        *,
        contenedor: str = CONTENEDOR_INPUT,
        sufijo: str = ".pdf",
    ) -> None:
        self._almacen = almacen
        self._contenedor = contenedor
        self._sufijo = sufijo

    def obtener(self, document_id: str) -> DocumentoPdf:
        nombre = f"{document_id}{self._sufijo}"
        data, metadata, content_type = self._almacen.get_con_metadata(
            self._contenedor, nombre
        )
        filename = metadata.get("filename") or nombre
        mime_type = content_type or metadata.get("mime_type") or "application/pdf"
        logger.info(
            "[fuente-doc-blob] document_id=%s <- %s/%s (%d bytes, file=%s)",
            document_id, self._contenedor, nombre, len(data), filename,
        )
        return DocumentoPdf(
            filename=filename,
            mime_type=mime_type,
            file_bytes=data,
        )


class SumideroEnvelopeBlob(SumideroEnvelope):
    """Escribe el envelope en ``envelopes/{document_id}_{fase}.json``."""

    def __init__(
        self,
        almacen: AlmacenBlobs,
        *,
        contenedor: str = CONTENEDOR_ENVELOPES,
    ) -> None:
        self._almacen = almacen
        self._contenedor = contenedor

    def persistir(self, *, document_id: str, envelope: dict, fase: str) -> None:
        nombre = f"{document_id}_{fase}.json"
        self._almacen.put_json(
            self._contenedor,
            nombre,
            envelope,
            metadata={"document_id": document_id, "fase": fase},
        )
        logger.info(
            "[sumidero-blob] envelope %s -> %s/%s",
            fase, self._contenedor, nombre,
        )
