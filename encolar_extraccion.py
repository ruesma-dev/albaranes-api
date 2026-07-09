# encolar_extraccion.py
"""Encola a mano un mensaje de extraccion en q-extraccion (para probar el worker).

Uso:  python encolar_extraccion.py DOC-PRUEBA-1
"""
from __future__ import annotations

import sys

from ruesma_comun.colas import COLA_EXTRACCION, MensajeExtraccion
from ruesma_comun.colas.arranque import construir_publicador


def main() -> int:
    # Carga el .env en os.environ (igual que main_worker.py): asi
    # COLAS_CONNECTION_STRING esta disponible al ejecutar en otra consola.
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    document_id = sys.argv[1] if len(sys.argv) > 1 else "DOC-PRUEBA-1"
    pub = construir_publicador(emitido_por="encolar-manual")
    pub.publicar(
        COLA_EXTRACCION,
        MensajeExtraccion(document_id=document_id, correlation_key=f"corr-{document_id}"),
    )
    print(f"Encolado document_id={document_id} en {COLA_EXTRACCION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
