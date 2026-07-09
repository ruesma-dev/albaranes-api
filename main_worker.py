# main_worker.py
"""Entrypoint del WORKER de sv2 (consumidor de q-extraccion).

Alternativa a ``main.py`` (servidor HTTP). En Azure Container Apps este es el
comando del contenedor del worker; en local lo lanzas para probar contra
Azurite + Azure Blob.

Hand-off por Blob (adaptadores de producción, ya NO disco):
  - Lee el PDF de entrada de ``input/{document_id}.pdf`` (lo deja sv1; en el
    piloto, ``seed_input.py``).
  - Escribe el envelope en ``envelopes/{document_id}_{fase}.json`` para sv3.

Variables de entorno relevantes:
  - COLAS_CONNECTION_STRING (local/Azurite) o COLAS_ACCOUNT_URL (nube) para
    las colas.
  - BLOBS_CONNECTION_STRING (o, en su defecto, COLAS_CONNECTION_STRING) para
    el Blob; en la nube, BLOBS_ACCOUNT_URL. En local con Azurite basta con
    COLAS_CONNECTION_STRING: el BlobEndpoint se deriva solo.
  - WORKER_CON_FASE2 : "true" para ejecutar también fase 2 (default false).
  + todas las de sv2 (ENABLE_*, claves IA, prompts...) que usa build_pipeline.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from config.logging_config import configure_logging
from config.settings import Settings
from interface_adapters.composition import build_pipeline
from interface_adapters.worker.blob_adapters import (
    FuenteDocumentoBlob,
    SumideroEnvelopeBlob,
)
from interface_adapters.worker.extraction_worker import (
    construir_handler_extraccion,
)
from interface_adapters.worker.local_stubs import GroundingNulo
from ruesma_comun.blobs import construir_almacen_desde_entorno
from ruesma_comun.colas import COLA_EXTRACCION
from ruesma_comun.colas.arranque import construir_publicador, ejecutar_worker


def main() -> int:
    # Carga el .env en os.environ para que las WORKER_*/BLOBS_* sean visibles
    # (pydantic-settings lee el .env para Settings, pero NO lo vuelca a
    # os.environ).
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    settings = Settings()
    configure_logging(Path(settings.log_dir), settings.log_level)
    # El SDK de azure-storage-* loguea cada petición HTTP a INFO: a WARNING.
    logging.getLogger("azure").setLevel(logging.WARNING)

    con_fase2 = os.environ.get("WORKER_CON_FASE2", "false").lower() == "true"

    almacen = construir_almacen_desde_entorno()
    pipeline = build_pipeline(settings)
    publicador = construir_publicador(emitido_por="ca-sv2-extraccion")
    handler = construir_handler_extraccion(
        pipeline=pipeline,
        fuente=FuenteDocumentoBlob(almacen),
        grounding=GroundingNulo(),
        sumidero=SumideroEnvelopeBlob(almacen),
        publicador=publicador,
        con_fase2=con_fase2,
    )
    return ejecutar_worker(
        nombre_cola=COLA_EXTRACCION,
        tipo_mensaje="extraccion",
        handler=handler,
        emitido_por="ca-sv2-extraccion",
    )


if __name__ == "__main__":
    raise SystemExit(main())
