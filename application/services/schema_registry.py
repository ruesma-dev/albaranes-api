# application/services/schema_registry.py
from __future__ import annotations

from typing import Dict, Type

from pydantic import BaseModel

from domain.models.albaran_models import DocumentoAlbaran
from domain.models.revision_models import RevisionAlbaranFase2


class SchemaRegistry:
    def __init__(self) -> None:
        self._schemas: Dict[str, Type[BaseModel]] = {
            "documento_albaran": DocumentoAlbaran,
            # NUEVO: schema de respuesta de la fase 2 de revisión.
            "revision_albaran_fase2": RevisionAlbaranFase2,
        }

    def get(self, schema_name: str) -> Type[BaseModel]:
        if schema_name in self._schemas:
            return self._schemas[schema_name]

        available = ", ".join(sorted(self._schemas.keys()))
        raise KeyError(
            f"Schema '{schema_name}' no registrado. Disponibles: {available}"
        )
