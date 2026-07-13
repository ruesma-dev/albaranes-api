# interface_adapters/worker/extraction_worker.py
"""Handler del worker de extracción: consume q-extraccion, publica q-persistencia.

Sustituye al endpoint HTTP como entrada de sv2 en el modelo de colas. Recibe
``{document_id}``, resuelve el PDF (FuenteDocumento), ejecuta el pipeline de
extracción REAL de sv2 y publica el disparador de persistencia. El envelope se
persiste (SumideroEnvelope) para que sv3 lo recupere por document_id.

Ejecuta SIEMPRE fase 1 + fase 2 (extraccion + revision/extraccion especial).
grounding se activan cuando exista el adaptador de grounding (se mueve aquí
desde sv3); el esqueleto ya está listo.
"""
from __future__ import annotations

import logging
from typing import Callable

from application.pipelines.extract_albaran_pipeline import (
    ExtractAlbaranPipeline,
    ExtractAlbaranRequest,
    ReviewAlbaranRequest,
)
from application.services.phase_merge import construir_envelope_final
from application.services.tipologia_resolver import resolver_tipologia
from interface_adapters.worker.ports import (
    FuenteDocumento,
    GroundingCabecera,
    SumideroEnvelope,
)
from ruesma_comun.colas import (
    COLA_PERSISTENCIA,
    MensajeBase,
    MensajePersistencia,
    PublicadorColas,
)

logger = logging.getLogger(__name__)


def construir_handler_extraccion(
    *,
    pipeline: ExtractAlbaranPipeline,
    fuente: FuenteDocumento,
    grounding: GroundingCabecera,
    sumidero: SumideroEnvelope,
    publicador: PublicadorColas,
) -> Callable[[MensajeBase], None]:
    """Crea el handler (closure) que consume q-extraccion."""

    def handler(mensaje: MensajeBase) -> None:
        document_id = mensaje.document_id
        logger.info("[sv2-worker] document_id=%s START", document_id)

        # 1) PDF del documento (SharePoint en produccion).
        doc = fuente.obtener(document_id)

        # 2) Fase 1 — extraccion.
        env1 = pipeline.run_phase_1(
            ExtractAlbaranRequest(
                filename=doc.filename,
                mime_type=doc.mime_type,
                file_bytes=doc.file_bytes,
            )
        )
        sumidero.persistir(document_id=document_id, envelope=env1, fase="phase_1")

        # Tipologia consolidada (nivel documento) a partir de fase 1:
        # regla dura LER -> residuos, familia dominante, u override por CIF.
        tip = resolver_tipologia(env1.get("data") or {})

        # 3) Fase 2 — revision + extraccion particular (SIEMPRE). El esquema
        #    de 4 IAs deja la extraccion especial (hormigon/residuos) en la
        #    fase 2, asi que ya no es opcional.
        ctx = grounding.contexto(
            document_id=document_id, phase_1_json=env1
        )
        env2 = pipeline.run_phase_2(
            ReviewAlbaranRequest(
                filename=doc.filename,
                mime_type=doc.mime_type,
                file_bytes=doc.file_bytes,
                phase_1_json=env1,
                sigrid_context=ctx,
                # Fase 2 por tipología: el pipeline usa este prompt si
                # existe (albaran_revision_fase2_{generico|hormigon|
                # residuos}); si no, cae al genérico configurado.
                prompt_key=f"albaran_revision_fase2_{tip.tipologia.value}",
            )
        )
        sumidero.persistir(
            document_id=document_id, envelope=env2, fase="phase_2"
        )

        # 4) Envelope FINAL: fusiona fase 2 y sella la tipologia. Es el que
        #    consume sv3 (fase logica "phase_1"). La tipologia va en meta (sv3
        #    la lee de forma laxa); el detalle de residuos/hormigon
        #    (contexto_linea) viaja dentro de data, y sv3 lo guarda como JSON.
        envelope_final = construir_envelope_final(
            env_fase1=env1, env_fase2=env2, tipologia=tip.tipologia,
        )
        sumidero.persistir(
            document_id=document_id, envelope=envelope_final, fase="phase_1"
        )

        # 5) Disparador de persistencia (el envelope ya esta guardado).
        publicador.publicar(
            COLA_PERSISTENCIA,
            MensajePersistencia(
                document_id=document_id,
                correlation_key=mensaje.correlation_key,
            ),
        )
        logger.info(
            "[sv2-worker] document_id=%s OK tipologia=%s fase=%s -> q-persistencia",
            document_id,
            tip.tipologia.value,
            "phase_2",
        )
        # Si el handler lanza, el mensaje NO se borra: reaparece por
        # visibilidad y se reintenta (lo gestiona ConsumidorCola).
        _ = envelope_final

    return handler
