# domain/models/contexto_linea.py
"""Modelo compartido del bloque ``contexto_linea``.

Representa la información estructural de una línea de albarán cuando
pertenece a una familia compleja (hormigón, combustible, alquiler de
maquinaria). Este fichero se copia IDÉNTICO en los servicios 2, 3 y 5
que manipulan el envelope de extracción.

Campos (ver prompts V2 para semántica completa):
  - tipo_familia: 'hormigon' | 'combustible' | 'alquiler_maquinaria'
                  | 'otro' | null
  - rol_linea: 'base' | 'extra_tiempo' | 'transporte' | 'recargo_horario'
               | 'desplazamiento' | 'operario' | 'otro' | null
  - descripcion_extendida: string con la descripción técnica completa
    (incluye modificadores del producto base). La usa el valorador para
    buscar recargos en el PDF del contrato.
  - notas_tiempo: string libre con info temporal del albarán (horas
    de carga/descarga, minutos netos, tiempo máximo libre declarado).
  - ref_linea_base: int con el ``line_index`` de la línea base asociada
    cuando esta línea es complementaria (extra_tiempo, transporte,
    operario...). Null para líneas base o sin contexto.

Por construcción el bloque es OPCIONAL — si la línea no es de una
familia especial, el OCR lo omite y los servicios downstream lo
tratan como None. Decisión explícita: usamos ``extra="ignore"`` aquí
para que el LLM pueda devolver campos adicionales sin romper la
validación (mayor robustez frente a versiones evolutivas del prompt).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

TipoFamilia = Literal[
    "hormigon",
    "combustible",
    "alquiler_maquinaria",
    "otro",
]

RolLinea = Literal[
    "base",
    "extra_tiempo",
    "transporte",
    "recargo_horario",
    "desplazamiento",
    "operario",
    "otro",
]


class ContextoLinea(BaseModel):
    """Bloque opcional con info estructural de la línea.

    A diferencia de ``StrictSchemaModel`` (que usa ``extra='forbid'``),
    este modelo tolera campos extra para que variaciones del prompt o
    respuestas "creativas" del LLM no invaliden toda la línea.
    """

    model_config = ConfigDict(extra="ignore")

    tipo_familia: Optional[TipoFamilia] = Field(default=None)
    rol_linea: Optional[RolLinea] = Field(default=None)
    descripcion_extendida: Optional[str] = Field(default=None)
    notas_tiempo: Optional[str] = Field(default=None)
    ref_linea_base: Optional[int] = Field(default=None)
