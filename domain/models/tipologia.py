# domain/models/tipologia.py
"""Tipología del albarán (nivel documento).

Un albarán es de UNA sola tipología (no mezcla actividades). La tipología
enruta la extracción particular (fase 2) y la valoración (sv5):

  - ``generico``  : caso por defecto (línea-a-contrato).
  - ``hormigon``  : hormigón preparado; genera líneas sintéticas M1–M7.
  - ``residuos``  : gestión de residuos (RCD); trae LER + m³ + Tn por línea
                    y se valora por CONTENEDOR según contrato.

La tipología la propone la IA de fase 1, pero se CONSOLIDA de forma
determinista en ``tipologia_resolver`` (regla dura: si hay un código LER
en el documento → residuos). El enum es cerrado a propósito: la IA no
puede inventar tipos y, ante la duda, cae en ``generico``.
"""
from __future__ import annotations

import re
from enum import Enum


class Tipologia(str, Enum):
    GENERICO = "generico"
    HORMIGON = "hormigon"
    MORTERO = "mortero"
    RESIDUOS = "residuos"

    @classmethod
    def from_str(cls, value: str | None) -> "Tipologia":
        """Normaliza un string a Tipologia; desconocido/None → GENERICO."""
        if not value:
            return cls.GENERICO
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return cls.GENERICO


# Un código LER es de 6 dígitos, habitualmente escrito en pares
# ("17 05 04", "170504", "17.05.04", "17-05-04"). Este patrón captura las
# variantes con separadores opcionales de espacio/punto/guion entre pares.
_LER_REGEX = re.compile(r"\b\d{2}[ .\-]?\d{2}[ .\-]?\d{2}\b")


def normalizar_ler(texto: str | None) -> str | None:
    """Devuelve el LER en formato canónico de 6 dígitos, o None."""
    if not texto:
        return None
    m = _LER_REGEX.search(str(texto))
    if not m:
        return None
    solo_digitos = re.sub(r"\D", "", m.group(0))
    return solo_digitos if len(solo_digitos) == 6 else None


# Designaciones de hormigón estructural/no estructural según EHE:
# HA (armado), HM (masa), HL (limpieza), HNE (no estructural), seguidas
# de la resistencia (HA-25, HM20, HL-150, HNE-15...). Sirve para detectar
# la tipología 'hormigon' desde el código/concepto de la línea sin
# depender de que la IA rellene contexto_linea.
_HORMIGON_REGEX = re.compile(r"\bH[ALMN]E?\s?-?\s?\d{2,3}\b", re.IGNORECASE)


# Designaciones de MORTERO segun resistencia (M-5, M-7,5, M-10, M-12,5,
# M-15, M-20...). El prefijo es 'M-' + resistencia; se distingue del
# hormigon (H*) por la letra. Tambien vale la palabra "mortero".
_MORTERO_REGEX = re.compile(r"\bM-\s?\d{1,2}(?:[.,]\d)?\b", re.IGNORECASE)


def texto_contiene_mortero(texto: str | None) -> bool:
    """True si el texto contiene una designacion de mortero (M-5, M-7,5)
    o la palabra 'mortero'."""
    if not texto:
        return False
    t = str(texto)
    return bool(_MORTERO_REGEX.search(t)) or "mortero" in t.lower()


def texto_contiene_hormigon(texto: str | None) -> bool:
    """True si el texto contiene una designación de hormigón (HA-25, HM20...)."""
    return bool(_HORMIGON_REGEX.search(str(texto))) if texto else False


def texto_contiene_ler(texto: str | None) -> bool:
    """True si el texto contiene un código LER de 6 dígitos."""
    return normalizar_ler(texto) is not None
