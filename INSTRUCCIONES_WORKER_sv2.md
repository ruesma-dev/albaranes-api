# Piloto: sv2 como WORKER de cola (q-extraccion -> q-persistencia)

Convierte sv2 (hoy servicio HTTP sin estado) en **worker**: consume
`q-extraccion` con `{document_id}`, ejecuta el pipeline de extraccion REAL de
sv2 (fase 1) y publica `q-persistencia`. El servidor HTTP (`main.py`) sigue
intacto; esto es una entrada alternativa (`main_worker.py`).

## Ficheros (todos NUEVOS; no se toca app.py)
- `interface_adapters/composition.py` — `build_pipeline(settings)` (extrae el
  wiring del pipeline; reutilizable por API y worker).
- `interface_adapters/worker/ports.py` — puertos `FuenteDocumento`,
  `GroundingCabecera`, `SumideroEnvelope`.
- `interface_adapters/worker/local_stubs.py` — stubs LOCALES (leen un PDF de
  disco, escriben el envelope a JSON) para probar sin SharePoint ni BBDD.
- `interface_adapters/worker/extraction_worker.py` — el handler de la cola.
- `main_worker.py` — entrypoint del worker.
- `encolar_extraccion.py` — encola un mensaje de prueba.

> Por que stubs: el worker definitivo descargara el PDF de SharePoint y
> persistira el envelope en PostgreSQL, y movera el grounding desde sv3. Esos
> adaptadores son el siguiente paso; los stubs validan YA el plumbing de cola
> con un albaran real y la extraccion real.

## Probarlo

1. **Azurite** en una consola:
   ```powershell
   azurite-queue --silent --location C:\azurite --queuePort 10001 --skipApiVersionCheck
   ```

2. En la carpeta de sv2, con su `.venv` y su `.env` (ENABLE_*, claves IA,
   prompts...), define ademas:
   ```powershell
   $env:COLAS_CONNECTION_STRING = "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
   # En el .env usa comillas SIMPLES y barras / (evita escapes de \\):
   #   WORKER_PDF_PATH='worker_input/mi albaran.pdf'
   #   WORKER_OUT_DIR='worker_out'
   #   WORKER_CON_FASE2=false
   # (main_worker.py carga el .env en os.environ al arrancar)
   ```
   (Necesitas `comun` instalado en el venv: `pip install -e ..\comun`.)

3. Arranca el worker (se queda escuchando):
   ```powershell
   python main_worker.py
   ```

4. En **otra** consola (mismo venv y `COLAS_CONNECTION_STRING`), encola un doc:
   ```powershell
   python encolar_extraccion.py DOC-PRUEBA-1
   ```

5. En el log del worker veras:
   ```
   [sv2-worker] document_id=DOC-PRUEBA-1 START
   [fuente-local] document_id=DOC-PRUEBA-1 -> albaran.pdf (NNNN bytes)
   ... (llamada IA real de fase 1) ...
   [sumidero-fichero] envelope phase_1 -> worker_out\DOC-PRUEBA-1_phase_1.json
   [colas] publicado tipo=persistencia document_id=DOC-PRUEBA-1 -> q-persistencia
   [sv2-worker] document_id=DOC-PRUEBA-1 OK fase=phase_1 -> q-persistencia
   ```

6. Comprueba: existe `worker_out\DOC-PRUEBA-1_phase_1.json` con la extraccion, y
   `q-persistencia` tiene 1 mensaje (Azure Storage Explorer contra el emulador).

7. Para parar el worker: Ctrl+C (parada limpia).

## Que valida esto
- El worker consume de la cola y reintenta/poison igual que el demo (mismo
  `ConsumidorCola`).
- La extraccion real de sv2 funciona disparada por cola, no por HTTP.
- El mensaje `{document_id}` basta (PDF por referencia; aqui via stub local).

## Siguiente paso
Adaptadores de produccion: `FuenteDocumento` -> descarga SharePoint por la ruta
en BBDD; `SumideroEnvelope` -> tabla en PostgreSQL; `GroundingCabecera` ->
mover el grounding determinista de sv3 a sv2 y poner `WORKER_CON_FASE2=true`.
Luego, el worker de sv3 consumiendo `q-persistencia`.
