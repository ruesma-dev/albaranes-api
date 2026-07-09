# application/services/phase_merge.py
"""Fusión de fases (antes en sv7, disuelto) — ahora en sv2.

Construye el envelope FINAL que consume sv3 a partir de:
  - el envelope de fase 1 (extracción), y
  - opcionalmente el de fase 2 (revisión).

Contrato de datos (recordatorio):
  - Fase 1 devuelve ``{meta, data, debug}`` donde ``data`` es un
    ``DocumentoAlbaran`` (``{cabecera, lineas}``).
  - Fase 2 devuelve ``{meta, data, debug}`` donde ``data`` es un
    ``RevisionAlbaranFase2`` (``{documento_revisado: DocumentoAlbaran,
    razonamientos: [...]}``).

Por eso el merge, cuando hay fase 2, EXTRAE ``data.documento_revisado``
como el documento final (sv3 espera la forma DocumentoAlbaran en
``data``), conserva la traza de ambas fases en ``debug`` y arrastra los
razonamientos. Sin fase 2, el final es la fase 1 tal cual.

Además SELLA la tipología consolidada en ``meta.tipologia`` (nivel
documento). No se toca ``data`` con campos nuevos: sv3 parsea ``data``
con un schema estricto (``extra='forbid'``), así que la tipología viaja
en ``meta`` (que sv3 lee de forma laxa) y el detalle por línea (LER, m³,
Tn, familia) viaja dentro de ``contexto_linea``, que sv3 guarda como JSON.

Función PURA: sin I/O, fácil de testear.
"""
from __future__ import annotations

from typing import Any, Mapping

from domain.models.tipologia import Tipologia


def _doc_final(
    env_fase1: Mapping[str, Any],
    env_fase2: Mapping[str, Any] | None,
) -> tuple[dict, list, str]:
    """Devuelve (data_documento, razonamientos, fase_efectiva)."""
    if env_fase2 is not None:
        data2 = env_fase2.get("data") or {}
        documento = data2.get("documento_revisado")
        if isinstance(documento, dict):
            razonamientos = data2.get("razonamientos") or []
            return documento, list(razonamientos), "phase_2"
        # Salvaguarda: si la fase 2 no trajo documento_revisado utilizable,
        # NO perdemos la extracción: caemos a fase 1.
    data1 = env_fase1.get("data") or {}
    return dict(data1), [], "phase_1"


def construir_envelope_final(
    *,
    env_fase1: Mapping[str, Any],
    env_fase2: Mapping[str, Any] | None = None,
    tipologia: Tipologia | str | None = None,
) -> dict:
    """Envelope final ``{meta, data, debug}`` para sv3."""
    documento, razonamientos, fase = _doc_final(env_fase1, env_fase2)

    tip = tipologia.value if isinstance(tipologia, Tipologia) else (
        str(tipologia) if tipologia else None
    )

    meta_base = dict(env_fase1.get("meta") or {})
    if env_fase2 is not None:
        # La revisión es la fase efectiva: reflejamos su meta por encima.
        meta_base.update(env_fase2.get("meta") or {})
    meta_base["phase"] = fase
    meta_base["merged"] = env_fase2 is not None
    if tip is not None:
        meta_base["tipologia"] = tip

    debug: dict[str, Any] = {
        "phase_1": {
            "meta": env_fase1.get("meta"),
            "debug": env_fase1.get("debug"),
        },
    }
    if env_fase2 is not None:
        debug["phase_2"] = {
            "meta": env_fase2.get("meta"),
            "debug": env_fase2.get("debug"),
            "razonamientos": razonamientos,
        }

    return {"meta": meta_base, "data": documento, "debug": debug}
