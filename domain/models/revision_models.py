# domain/models/revision_models.py
"""Schema de respuesta de la fase 2 (revisión).

NOTA TÉCNICA SOBRE EL TIPADO DE valor_anterior / valor_propuesto:

OpenAI Structured Outputs (Responses API + response_format) requiere
que CADA propiedad del schema tenga un ``type`` explícito. No acepta
``Any`` (que se traduce a un schema vacío {}). Por ese motivo no
podemos declarar ``valor_anterior: Any``.

Para que la API lo acepte, declaramos ``valor_anterior`` y
``valor_propuesto`` como un union explícito de tipos primitivos
JSON: ``str | int | float | bool | None``. Esto cubre el 100% de
los casos reales:

  - Strings: fechas, CIFs, códigos de obra, descripciones, unidades.
  - Números (int/float): cantidades, precios, importes.
  - Booleanos: rara vez, pero posibles en flags futuros.
  - None: dos usos:
      a) Valor "no presente" en valor_anterior (la IA no encontró
         valor previo en fase 1, por ejemplo si propone añadir una
         línea o un campo nuevo).
      b) "eliminar" el valor en valor_propuesto. Para eliminar una
         línea entera, ver la nota más abajo.

ELIMINAR / AÑADIR LÍNEAS ENTERAS:

Como el schema es escalar (no admite objetos anidados), los cambios
estructurales se hacen GRANULARMENTE:

  - Añadir línea nueva en posición N → la fase 2 emite varios
    cambios con paths "lines[N].cantidad", "lines[N].precio",
    "lines[N].concepto", etc. La utilidad apply_patch_to_envelope
    detecta la primera ocurrencia de "lines[N]" con índice nuevo
    y crea el dict; las siguientes ocurrencias rellenan campos.

  - Eliminar línea entera N → la fase 2 emite UN cambio con path
    "lines[N]" (sin sub-campo) y valor_propuesto=null. La utilidad
    detecta el path "lines[N]" sin sub-segmento y aplica un pop().

Esta política está descrita también en el prompt fase 2 (patrones
8 y 9).
"""
from __future__ import annotations

from typing import List, Literal, Optional, Union

from pydantic import Field

from domain.models.schema_base import StrictSchemaModel


ReviewStatus = Literal["ok", "ok_with_changes", "inconsistent"]

# Valor escalar JSON (cubre todos los tipos serializables del
# ``DocumentoAlbaran``). Usar un Union explícito permite a OpenAI
# generar un schema con ``anyOf`` válido (cada rama con ``type``).
ScalarValue = Union[str, int, float, bool, None]


class CambioPropuesto(StrictSchemaModel):
    """Un cambio puntual propuesto por la fase 2.

    Ejemplos válidos de ``campo``:
        "fecha"
        "proveedor_cif"
        "obra_codigo"
        "lines[0].cantidad"
        "lines[2].codigo_imputacion"
        "lines[3].importe"
        "lines[5]"            ← eliminar línea (con valor_propuesto=null)

    El sistema aplicará el patch sobre el JSON de fase 1 antes de
    enviarlo a sv3 para persistir.
    """

    campo: str = Field(
        ...,
        description=(
            "Ruta del campo en notación dot/bracket. Ej: "
            "'lines[2].cantidad'. Para eliminar una línea entera "
            "usar 'lines[N]' (sin sub-campo) con valor_propuesto=null."
        ),
    )
    valor_anterior: ScalarValue = Field(
        default=None,
        description=(
            "Valor que tiene fase 1 en ese campo. Sirve de check de "
            "versión. Si fase 2 propone añadir una línea/campo nuevo, "
            "puede dejar este campo a null."
        ),
    )
    valor_propuesto: ScalarValue = Field(
        default=None,
        description=(
            "Valor que la fase 2 propone. null tiene dos significados: "
            "(a) si el path es 'lines[N]' sin sub-campo, eliminar esa "
            "línea; (b) en otro caso, dejar el valor sin definir."
        ),
    )
    razon: Optional[str] = Field(
        default=None,
        description="Por qué se propone el cambio. Específica, no genérica.",
    )
    patron_aplicado: Optional[str] = Field(
        default=None,
        description=(
            "Identificador corto del patrón disparado, p.ej. 'importe_minimo', "
            "'logica_fisica_unidades', 'coherencia_aritmetica_linea'. "
            "Opcional: la IA puede omitirlo si el cambio no encaja en un patrón."
        ),
    )


class RevisionAlbaranFase2(StrictSchemaModel):
    """Respuesta completa de la fase 2."""

    review_status: ReviewStatus = Field(
        ...,
        description="Resultado global de la revisión.",
    )
    explicacion_global: Optional[str] = Field(
        default=None,
        description="Resumen 1-3 frases del diagnóstico de la fase 2.",
    )
    cambios: List[CambioPropuesto] = Field(
        default_factory=list,
        description="Lista de cambios puntuales (vacía si review_status == 'ok').",
    )