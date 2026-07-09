# application/services/tipologia_resolver.py
"""Consolida la tipología del albarán a partir de la salida de fase 1.

La IA de fase 1 clasifica y rellena ``contexto_linea`` por línea, pero la
decisión FINAL se toma aquí de forma determinista, con esta prioridad:

  1. Override por CIF (opcional): si el proveedor está en una lista de
     "proveedores de actividad única" (p.ej. un gestor puro de residuos),
     se fuerza su tipología. Es una red de seguridad para casos conocidos;
     depende del CIF leído (que la IA 2 aún puede corregir), así que si el
     CIF no casa, simplemente no aplica.
  2. Regla dura LER → residuos: si CUALQUIER línea trae un código LER
     (en ``contexto_linea.codigo_ler`` o detectado en código/concepto), el
     albarán es de residuos. (Instrucción explícita: "si hay un texto que
     sea LER es de residuos".)
  3. Familia dominante de las líneas: si hay líneas ``residuos`` u
     ``hormigon`` en ``contexto_linea.tipo_familia``, se usa esa.
  4. Por defecto: ``generico``.

Cuando la clasificación de la IA (paso 3) y el override por CIF (paso 1)
DISCREPAN, se marca ``discrepancia=True`` para revisión, en vez de
sobreescribir en silencio (así se cazan CIF mal leídos y albaranes mal
archivados). El override manda solo si no contradice una señal de LER.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping

from domain.models.tipologia import Tipologia, texto_contiene_ler

logger = logging.getLogger(__name__)

_LOG = "[tipologia]"


@dataclass(frozen=True)
class ResultadoTipologia:
    tipologia: Tipologia
    motivo: str
    discrepancia: bool = False
    ler_encontrados: list[str] = field(default_factory=list)


def _lineas(data: Mapping) -> list[dict]:
    lineas = data.get("lineas") if isinstance(data, Mapping) else None
    return [l for l in (lineas or []) if isinstance(l, dict)]


def _cif(data: Mapping) -> str | None:
    cab = data.get("cabecera") if isinstance(data, Mapping) else None
    if not isinstance(cab, dict):
        return None
    cif = cab.get("proveedor_cif")
    return (str(cif).strip().upper().replace(" ", "") or None) if cif else None


def _hay_ler_en_linea(linea: dict) -> bool:
    ctx = linea.get("contexto_linea")
    if isinstance(ctx, dict) and ctx.get("codigo_ler"):
        return True
    # Red de seguridad: LER escrito en el código o el concepto de la línea.
    return (
        texto_contiene_ler(linea.get("codigo"))
        or texto_contiene_ler(linea.get("concepto"))
    )


def _familias(lineas: list[dict]) -> set[str]:
    fams: set[str] = set()
    for l in lineas:
        ctx = l.get("contexto_linea")
        if isinstance(ctx, dict) and ctx.get("tipo_familia"):
            fams.add(str(ctx["tipo_familia"]).strip().lower())
    return fams


def resolver_tipologia(
    data: Mapping,
    *,
    override_por_cif: Mapping[str, Tipologia] | None = None,
) -> ResultadoTipologia:
    """Devuelve la tipología consolidada del documento de fase 1."""
    lineas = _lineas(data)
    cif = _cif(data)

    # --- Señal de LER (regla dura) --------------------------------- #
    lers: list[str] = []
    for l in lineas:
        if _hay_ler_en_linea(l):
            ctx = l.get("contexto_linea") or {}
            marca = (
                ctx.get("codigo_ler")
                or l.get("codigo")
                or l.get("concepto")
                or "?"
            )
            lers.append(str(marca))
    hay_ler = len(lers) > 0

    # --- Override por CIF ------------------------------------------ #
    forzada: Tipologia | None = None
    if override_por_cif and cif and cif in override_por_cif:
        forzada = Tipologia.from_str(str(override_por_cif[cif]))

    # --- Familia dominante de las líneas --------------------------- #
    fams = _familias(lineas)
    if "residuos" in fams:
        por_familia = Tipologia.RESIDUOS
    elif "hormigon" in fams:
        por_familia = Tipologia.HORMIGON
    else:
        por_familia = Tipologia.GENERICO

    # --- Consolidación (prioridad + discrepancia) ------------------ #
    if hay_ler:
        # LER manda sobre todo. Si el override dice algo distinto → aviso.
        discrepa = forzada is not None and forzada != Tipologia.RESIDUOS
        motivo = "codigo LER presente en el documento"
        if discrepa:
            motivo += (
                f" (DISCREPANCIA: override CIF={cif} decía {forzada.value})"
            )
        res = ResultadoTipologia(
            Tipologia.RESIDUOS, motivo, discrepancia=discrepa,
            ler_encontrados=lers,
        )
    elif forzada is not None:
        discrepa = por_familia != Tipologia.GENERICO and por_familia != forzada
        motivo = f"override por CIF={cif}"
        if discrepa:
            motivo += (
                f" (DISCREPANCIA: la IA clasificó {por_familia.value})"
            )
        res = ResultadoTipologia(forzada, motivo, discrepancia=discrepa)
    else:
        res = ResultadoTipologia(
            por_familia, f"familia dominante de las líneas ({por_familia.value})",
        )

    logger.info(
        "%s document cif=%s -> %s | %s | discrepancia=%s ler=%s",
        _LOG, cif, res.tipologia.value, res.motivo, res.discrepancia,
        res.ler_encontrados or "-",
    )
    return res
