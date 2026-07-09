# seed_input.py
"""Utilidad de prueba: sube un PDF local a ``input/{document_id}.pdf``.

Hace de sv1 hasta que exista: deja el PDF de entrada en Blob para que el
worker de sv2 lo lea por ``document_id``. Guarda el nombre original del
fichero como metadata ``filename`` (lo usará la extracción como
``source_filename``).

Uso (PowerShell):
    python seed_input.py <document_id> <ruta_pdf_local>

Ejemplo:
    python seed_input.py DOC-PRUEBA-1 "worker_input/0695 - Albaranes ....pdf"

Después:  python encolar_extraccion.py DOC-PRUEBA-1

Requiere COLAS_CONNECTION_STRING (o BLOBS_CONNECTION_STRING) en el .env.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ruesma_comun.blobs import CONTENEDOR_INPUT, construir_almacen_desde_entorno


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    document_id = sys.argv[1]
    pdf_path = Path(sys.argv[2])
    if not pdf_path.exists():
        print(f"No existe el PDF: {pdf_path}")
        return 2

    data = pdf_path.read_bytes()
    almacen = construir_almacen_desde_entorno()
    nombre = f"{document_id}.pdf"
    almacen.put_bytes(
        CONTENEDOR_INPUT,
        nombre,
        data,
        content_type="application/pdf",
        metadata={"filename": pdf_path.name, "document_id": document_id},
    )
    print(
        f"[seed] {CONTENEDOR_INPUT}/{nombre} <- {pdf_path.name} "
        f"({len(data)} bytes). Ahora: python encolar_extraccion.py {document_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
