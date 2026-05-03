# albaranes-extractor-api (Servicio 2 / sv2)

> **Microservicio de extracción multi-proveedor** del ecosistema Construcciones Ruesma.
> Expone una API HTTP (FastAPI + uvicorn) que recibe un albarán/factura
> (PDF o imagen) y devuelve un envelope JSON con la **misma extracción
> estructurada hecha en paralelo por hasta 5 proveedores LLM / OCR**
> (OpenAI, Gemini, Claude, Google Document AI, Azure Document Intelligence),
> para que el Servicio 3 pueda comparar/mergear/votar entre ellos.

---

## 1. ¿Qué hace exactamente?

`albaranes-extractor-api` es un **servicio HTTP stateless** cuya única
responsabilidad es:

1. Aceptar un fichero (`.pdf` / `.jpg` / `.jpeg` / `.png` / `.webp`) por `POST` multipart.
2. Cargar de YAML un *prompt* parametrizado (`system` + `task` + `schema_hint` + `schema`).
3. Cargar el modelo Pydantic `DocumentoAlbaran` desde un registro de schemas.
4. Enviar el fichero **a cada proveedor habilitado** vía su SDK propio, exigiendo
   respuesta estructurada conforme a ese schema.
5. Validar cada respuesta contra el schema (Pydantic `extra='forbid'` en cabecera/líneas).
6. Devolver un único envelope JSON con la extracción de cada proveedor por separado,
   *sin* mergear nada (el merge es trabajo del sv3).

> Es un servicio puramente **CPU/red-bound**, sin estado y sin persistencia. Cada
> petición es independiente.

---

## 2. Lugar dentro del ecosistema (6 microservicios)

```
   ┌───────────────────────────┐
   │ sv1 · email-albaranes-    │
   │      ingestor             │
   └────────────┬──────────────┘
                │  POST /v1/albaranes/extract  (multipart: file)
                ▼
   ┌──────────────────────────────────────────────┐
   │  sv2 · albaranes-extractor-api               │  ← ESTE SERVICIO
   │  (FastAPI + uvicorn, stateless)              │
   │                                              │
   │   ┌──────────────────────────────────────┐   │
   │   │ Pipeline:                            │   │
   │   │  request → attachment → extract con  │   │
   │   │  N proveedores → envelope JSON       │   │
   │   └──────────────────────────────────────┘   │
   └────────┬──────────┬───────────┬──────────────┘
            │          │           │
            ▼          ▼           ▼
       ┌────────┐ ┌────────┐ ┌────────┐
       │ OpenAI │ │ Gemini │ │ Claude │  · LLMs (vision + structured output)
       └────────┘ └────────┘ └────────┘
            │          │
            ▼          ▼
       ┌──────────────┐ ┌──────────────────┐
       │ Google Doc AI│ │ Azure Doc Intel. │  · OCR puro (opcionales, off por defecto)
       └──────────────┘ └──────────────────┘

       envelope JSON  ───►   sv3 · persister (merge & store)
```

`sv2` es la **única entrada de datos** al stack de proveedores externos: el resto
de servicios solo hablan con sv2 vía HTTP.

---

## 3. Arquitectura interna (Hexagonal / Clean)

```
albaranes-extractor-api/
├─ main.py                                      # uvicorn.run(build_app(settings))
├─ config/
│  ├─ settings.py                               # pydantic-settings + validators cruzados
│  ├─ logging_config.py                         # RotatingFileHandler + consola
│  ├─ prompts.yaml                              # Prompts en producción (cargado por defecto)
│  └─ prompts/
│     └─ svc2_prompt_albaran_factura_es.yaml    # Versión alternativa / histórica
├─ domain/
│  ├─ models/
│  │  ├─ schema_base.py                         # StrictSchemaModel (extra='forbid')
│  │  ├─ albaran_models.py                      # CabeceraAlbaran, LineaAlbaran, DocumentoAlbaran
│  │  ├─ contexto_linea.py                      # Bloque opcional para hormigón/combustible/alquiler
│  │  └─ llm_attachment.py                      # LlmAttachment (kind=image|pdf, bytes)
│  └─ ports/
│     ├─ llm_client.py                          # LlmVisionClient (interfaz ABC)
│     └─ prompt_repository.py                   # PromptRepository + PromptSpec
├─ application/
│  ├─ pipelines/
│  │  └─ extract_albaran_pipeline.py            # Pipeline: request → envelope
│  └─ services/
│     ├─ albaran_extraction_service.py          # Orquesta los N proveedores
│     └─ schema_registry.py                     # Mapa nombre_schema → modelo Pydantic
├─ infrastructure/
│  ├─ llm/
│  │  ├─ retry_policy.py                        # Backoff exponencial + jitter, comp. SDKs
│  │  ├─ openai_responses_client.py             # OpenAI Responses API (responses.parse)
│  │  ├─ openai_sdk_compat.py                   # Monkey-patch openai._compat.model_dump
│  │  ├─ gemini_genai_client.py                 # Google GenAI (response_json_schema)
│  │  ├─ claude_messages_client.py              # Anthropic Messages API + tool_use forzado
│  │  ├─ google_document_ai_client.py           # Google Document AI (OCR + mapeo aliases)
│  │  └─ azure_document_intelligence_client.py  # Azure Doc Intelligence (LRO via REST)
│  └─ prompts/
│     └─ yaml_prompt_repository.py              # Carga prompts.yaml a memoria
└─ interface_adapters/
   └─ api/
      └─ app.py                                 # FastAPI: build_app() + /health + /v1/albaranes/extract
```

### Patrones aplicados

| Patrón                                         | Dónde                                                | Por qué                                                                                       |
|------------------------------------------------|------------------------------------------------------|-----------------------------------------------------------------------------------------------|
| **Hexagonal / Ports & Adapters**               | `domain/ports` ↔ `infrastructure/llm/*`              | Cambiar de proveedor, añadir uno nuevo o desactivar uno = añadir/quitar un adaptador.         |
| **Strategy**                                   | `LlmVisionClient` con 5 implementaciones             | Cada proveedor implementa el mismo contrato (`extract_document`).                             |
| **Factory condicional**                        | `build_app()` en `interface_adapters/api/app.py`     | Construye solo los clientes habilitados por las flags `ENABLE_*`.                             |
| **Pipeline**                                   | `ExtractAlbaranPipeline.run`                         | Pasos lineales: validar → construir attachment → extraer N proveedores → ensamblar envelope.  |
| **Repository**                                 | `YamlPromptRepository`                               | Aísla la carga de prompts del resto del código.                                               |
| **Registry**                                   | `SchemaRegistry`                                     | Permite asociar `schema: documento_albaran` (string YAML) con el modelo Pydantic real.        |
| **Composition root**                           | `build_app(settings)`                                | Único sitio donde se cablean dependencias.                                                    |
| **Retry con backoff exponencial + jitter**     | `RetryPolicy` + `run_with_retry` en `retry_policy.py`| Resiliencia ante 429/5xx y errores transitorios de transporte (`httpx`, SDKs).                |
| **Monkey-patch defensivo**                     | `openai_sdk_compat.patch_openai_pydantic_compat`     | Mitiga incompatibilidad puntual SDK OpenAI ↔ Pydantic v2 sin tocar el SDK.                    |
| **Frozen dataclasses**                         | `LlmAttachment`, `ProviderClientSpec`, `PromptSpec`  | Inmutabilidad → razonamiento sencillo, hashable.                                              |

---

## 4. Endpoints HTTP

### 4.1 `GET /health`

Devuelve estado y configuración runtime efectiva. Útil para liveness/readiness probes.

**Respuesta `200 OK`:**

```json
{
  "ok": true,
  "service": "albaranes-extractor-api",
  "version": "1.0.0",
  "providers": [
    {"provider": "openai",  "model": "gpt-5",            "prompt_supported": true},
    {"provider": "gemini",  "model": "gemini-2.5-flash", "prompt_supported": true},
    {"provider": "claude",  "model": "claude-sonnet-4-5","prompt_supported": true}
  ],
  "enabled_llm_providers": ["openai", "gemini", "claude"],
  "retry_policy": {
    "max_retries": 2,
    "backoff_base_s": 2.0,
    "backoff_cap_s": 30.0
  }
}
```

> El campo `prompt_supported=false` aparece en proveedores OCR puro (Google Document AI,
> Azure Document Intelligence): el prompt no afecta a su procesamiento, se conserva
> únicamente para trazabilidad.

### 4.2 `POST /v1/albaranes/extract`

Endpoint principal. Recibe el documento y devuelve el envelope.

**Request:**

```http
POST /v1/albaranes/extract HTTP/1.1
Content-Type: multipart/form-data; boundary=...

(form-field "file": archivo binario .pdf | .jpg | .jpeg | .png | .webp)
```

**Validaciones de entrada:**

| Validación                              | Error                                                                          |
|-----------------------------------------|--------------------------------------------------------------------------------|
| `file` vacío                            | `400 Archivo vacío.`                                                           |
| Tamaño > `MAX_FILE_MB` (default 25)     | `400 Archivo demasiado grande (X.YZ MB) > MAX_FILE_MB=25`                      |
| `prompt_key` desconocido                | `400 prompt_key '...' no existe. Disponibles: ...`                             |
| `schema` no registrado                  | `400 Schema '...' no registrado. Disponibles: ...`                             |
| Cualquier otra excepción                | `500 Error extrayendo albarán: ...`                                            |

**Respuesta `200 OK`:** envelope JSON cuyo cuerpo *no está envuelto* en una raíz como
`{"data": ...}`. La extracción de OpenAI ocupa el nivel raíz del envelope, y los
demás proveedores se cuelgan como sub-objetos por nombre. **Esto es deliberado** y
es como sv3 espera leerlo.

```json
{
  "meta": {
    "prompt_key": "albaran_factura_es",
    "schema": "documento_albaran",
    "source_filename": "albaran__page_001_of_003.pdf",
    "source_mime_type": "application/pdf",
    "source_sha256": "f3e1...",
    "model": "gpt-5",
    "processed_at_utc": "2026-05-03T12:34:56.789012+00:00",
    "service": "albaranes-extractor-api",
    "service_version": "1.0.0"
  },
  "data": {
    "cabecera": {
      "proveedor_nombre": "Hormigones del Norte SL",
      "proveedor_cif": "B12345678",
      "fecha": "2026-05-02",
      "numero_albaran": "A-12345",
      "forma_pago": "30 días",
      "obra_codigo": "OBRA-2026-014",
      "obra_nombre": "Edificio Castilla",
      "obra_direccion": "C/ Mayor 3, Madrid",
      "id": null
    },
    "lineas": [
      {
        "id": "1",
        "cabecera_id": null,
        "codigo": "HM-25",
        "cantidad": 7.5,
        "concepto": "Hormigón HM-25/B/20 con aditivo plastificante",
        "precio": 87.50,
        "descuento": null,
        "precio_neto": 656.25,
        "codigo_imputacion": "OBRA-2026-014.04",
        "confianza_pct": 92.0,
        "contexto_linea": {
          "tipo_familia": "hormigon",
          "rol_linea": "base",
          "descripcion_extendida": "HM-25/B/20 con aditivo plastificante y ambiente IIa",
          "notas_tiempo": "tiempo libre 45min, reales 38min",
          "ref_linea_base": null
        }
      }
    ]
  },
  "debug": {
    "openai_request":  { "...": "..." },
    "openai_response": { "...": "..." }
  },

  "gemini": {
    "meta": { "...": "..." },
    "data": { "cabecera": "...", "lineas": "..." },
    "debug": { "gemini_request": "...", "gemini_response": "..." }
  },
  "claude": {
    "meta": { "...": "..." },
    "data": { "cabecera": "...", "lineas": "..." },
    "debug": { "claude_request": "...", "claude_response": "..." }
  },
  "google_document_ai":          { "meta": "...", "data": "...", "debug": "..." },
  "azure_document_intelligence": { "meta": "...", "data": "...", "debug": "..." }
}
```

> Las claves `gemini`, `claude`, `google_document_ai` y `azure_document_intelligence`
> **solo aparecen si el proveedor está habilitado**. La clave OpenAI siempre aparece
> a nivel raíz (es obligatoria — ver §8).

---

## 5. Schema de salida (`DocumentoAlbaran`)

Modelo definido en `domain/models/albaran_models.py`. Hereda de `StrictSchemaModel`
(Pydantic, `extra='forbid'`): cualquier campo no declarado en cabecera o línea
provoca error de validación.

### `CabeceraAlbaran`

| Campo               | Tipo            | Notas                                                    |
|---------------------|-----------------|----------------------------------------------------------|
| `proveedor_nombre`  | `str \| None`   | Razón social del proveedor.                              |
| `proveedor_cif`     | `str \| None`   | CIF/NIF/NIE.                                             |
| `fecha`             | `str \| None`   | Como string libre (sin formato impuesto en el modelo).   |
| `numero_albaran`    | `str \| None`   |                                                          |
| `forma_pago`        | `str \| None`   |                                                          |
| `obra_codigo`       | `str \| None`   | Imputación a nivel cabecera (algunos proveedores la usan). |
| `obra_nombre`       | `str \| None`   |                                                          |
| `obra_direccion`    | `str \| None`   |                                                          |
| `id`                | `str \| None`   | Reservado (lo asigna sv3 al persistir).                  |

### `LineaAlbaran`

| Campo                | Tipo                    | Notas                                                      |
|----------------------|-------------------------|------------------------------------------------------------|
| `id`                 | `str \| None`           | Identificador local de línea dentro del documento.         |
| `cabecera_id`        | `str \| None`           | FK lógica a la cabecera.                                   |
| `codigo`             | `str \| None`           | SKU / referencia.                                          |
| `cantidad`           | `float \| None`         |                                                            |
| `concepto`           | `str \| None`           | Descripción tal como aparece.                              |
| `precio`             | `float \| None`         | Precio unitario.                                           |
| `descuento`          | `float \| None`         |                                                            |
| `precio_neto`        | `float \| None`         | Total línea.                                               |
| `codigo_imputacion`  | `str \| None`           | Código de partida (anotación manual del jefe de obra).     |
| `confianza_pct`      | `float \| None` (`0..100`) | Solo Document AI lo rellena (vía `entity.confidence`).  |
| `contexto_linea`     | `ContextoLinea \| None` | Bloque opcional — ver abajo.                               |

### `ContextoLinea` (opcional, **NO** estricto: `extra='ignore'`)

Solo aparece si la línea pertenece a una familia compleja (hormigón, combustible,
alquiler de maquinaria). Diseñada para tolerar evoluciones del prompt sin romper
la validación.

| Campo                    | Tipo / Literal                                                                                 |
|--------------------------|------------------------------------------------------------------------------------------------|
| `tipo_familia`           | `'hormigon' \| 'combustible' \| 'alquiler_maquinaria' \| 'otro' \| None`                        |
| `rol_linea`              | `'base' \| 'extra_tiempo' \| 'transporte' \| 'recargo_horario' \| 'desplazamiento' \| 'operario' \| 'otro' \| None` |
| `descripcion_extendida`  | `str \| None` — descripción técnica completa, la usa el valorador downstream.                  |
| `notas_tiempo`           | `str \| None` — info temporal (horas de carga/descarga, minutos netos…).                       |
| `ref_linea_base`         | `int \| None` — `line_index` de la línea base si esta es complementaria.                       |

> **Decisión de diseño explícita**: este sub-modelo **NO** es estricto (`extra='ignore'`).
> Si añadimos campos al prompt y el LLM los devuelve, no rompemos extracciones existentes.
> El resto del schema sí es estricto (`extra='forbid'`) para detectar derivas del LLM.

---

## 6. Configuración (variables de entorno)

Todas se cargan desde `.env` vía `pydantic-settings`. Hay **validators cruzados** en
`Settings` que abortan el arranque si la combinación no tiene sentido (ver §6.3).

### 6.1 Generales del servicio

| Variable               | Default                | Descripción                                                                |
|------------------------|------------------------|----------------------------------------------------------------------------|
| `API_HOST`             | `127.0.0.1`            | Host de uvicorn.                                                           |
| `API_PORT`             | `8000`                 | Puerto.                                                                    |
| `MAX_FILE_MB`          | `25`                   | Límite de tamaño del fichero entrante.                                     |
| `CORS_ALLOW_ORIGINS`   | *(vacío)*              | Lista CSV de orígenes; si está vacío, no se monta middleware CORS.         |
| `LOG_LEVEL`            | `INFO`                 |                                                                            |
| `LOG_DIR`              | `logs`                 |                                                                            |
| `SERVICE_VERSION`      | `1.0.0`                | Aparece en `/health` y en `meta.service_version` del envelope.             |
| `PROMPT_KEY`           | `albaran_factura_es`   | Clave dentro de `prompts.yaml` que se usará en cada extracción.            |
| `PROMPTS_YAML_PATH`    | `config/prompts.yaml`  | Ruta al fichero YAML de prompts.                                           |

### 6.2 Por proveedor

#### LLMs (los tres tienen *flag* + API key)

| Variable                | Default                                        | Notas                                                              |
|-------------------------|------------------------------------------------|--------------------------------------------------------------------|
| `ENABLE_OPENAI`         | `true`                                         | Si se desactiva, **el pipeline rompe** (ver §8). Mantener en `true`. |
| `OPENAI_API_KEY`        | *(obligatorio si enabled)*                     |                                                                    |
| `OPENAI_MODEL`          | `gpt-5`                                        | Tu cuenta debe tener acceso al modelo elegido.                     |
| `ENABLE_GEMINI`         | `true`                                         |                                                                    |
| `GEMINI_API_KEY`        | *(obligatorio si enabled)*                     |                                                                    |
| `GEMINI_MODEL`          | `gemini-2.5-flash`                             |                                                                    |
| `ENABLE_CLAUDE`         | `true`                                         |                                                                    |
| `ANTHROPIC_API_KEY`     | *(obligatorio si enabled)*                     |                                                                    |
| `ANTHROPIC_MODEL`       | `claude-sonnet-4-5`                            |                                                                    |
| `ANTHROPIC_MAX_TOKENS`  | `8192`                                         | Límite de output del modelo (no del total context).                |
| `ANTHROPIC_TIMEOUT_S`   | `120`                                          | Timeout HTTP del SDK de Anthropic.                                 |

#### OCR puros (deshabilitados por defecto)

| Variable                                          | Default          | Notas                                              |
|---------------------------------------------------|------------------|----------------------------------------------------|
| `GOOGLE_DOCUMENT_AI_ENABLED`                      | `false`          |                                                    |
| `GOOGLE_DOCUMENT_AI_PROJECT_ID`                   | *(req. si on)*   | GCP project con Document AI activado.              |
| `GOOGLE_DOCUMENT_AI_LOCATION`                     | `eu`             | Región (`eu`, `us`...).                            |
| `GOOGLE_DOCUMENT_AI_PROCESSOR_ID`                 | *(req. si on)*   | ID del processor (típicamente *Invoice Parser*).   |
| `GOOGLE_DOCUMENT_AI_PROCESSOR_VERSION`            | *(opcional)*     | Si quieres pinchar a una versión concreta.         |
| `GOOGLE_APPLICATION_CREDENTIALS`                  | *(opcional)*     | Path al JSON de service account (si lo usas).      |
| `AZURE_DOCUMENT_INTELLIGENCE_ENABLED`             | `false`          |                                                    |
| `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT`            | *(req. si on)*   | `https://<recurso>.cognitiveservices.azure.com`    |
| `AZURE_DOCUMENT_INTELLIGENCE_KEY`                 | *(req. si on)*   | Cognitive Services key.                            |
| `AZURE_DOCUMENT_INTELLIGENCE_MODEL_ID`            | `prebuilt-invoice` |                                                  |
| `AZURE_DOCUMENT_INTELLIGENCE_API_VERSION`         | `2024-11-30`     |                                                    |
| `AZURE_DOCUMENT_INTELLIGENCE_TIMEOUT_S`           | `120`            |                                                    |

### 6.3 Política de reintentos (compartida por OpenAI/Gemini/Claude)

| Variable               | Default | Descripción                                                                        |
|------------------------|---------|------------------------------------------------------------------------------------|
| `LLM_MAX_RETRIES`      | `2`     | Reintentos *adicionales* tras el primer intento (total = 1 + retries).             |
| `LLM_BACKOFF_BASE_S`   | `2.0`   | Base del backoff exponencial.                                                      |
| `LLM_BACKOFF_CAP_S`    | `30.0`  | Tope superior del sleep entre reintentos.                                          |

> **No aplica** a Google Document AI ni Azure Document Intelligence: ambos SDK/REST traen
> retry interno y aplicar otra capa duplicaría tiempos sin beneficio (decisión documentada).

### 6.4 Validators cruzados en `Settings` (abortan el arranque)

1. **Al menos un proveedor LLM debe estar habilitado**. Si los tres `ENABLE_*` están a `false`,
   el servicio no puede extraer nada y aborta con mensaje explícito. Mejor que descubrirlo
   en la primera extracción.
2. **Si un proveedor LLM está habilitado, su API key debe existir**. Evita arrancar con
   `ENABLE_OPENAI=true` y `OPENAI_API_KEY=""` (lo que provocaría 401 al primer fichero).

### 6.5 Ejemplo de `.env`

```dotenv
# --- Generales ---
API_HOST=0.0.0.0
API_PORT=8000
MAX_FILE_MB=25
LOG_LEVEL=INFO
LOG_DIR=logs
SERVICE_VERSION=1.0.0
PROMPT_KEY=albaran_factura_es
PROMPTS_YAML_PATH=config/prompts.yaml

# --- LLMs ---
ENABLE_OPENAI=true
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5

ENABLE_GEMINI=true
GEMINI_API_KEY=AI...
GEMINI_MODEL=gemini-2.5-flash

ENABLE_CLAUDE=true
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-5
ANTHROPIC_MAX_TOKENS=8192
ANTHROPIC_TIMEOUT_S=120

# --- Retry compartida ---
LLM_MAX_RETRIES=2
LLM_BACKOFF_BASE_S=2.0
LLM_BACKOFF_CAP_S=30.0

# --- OCR puros (off por defecto) ---
GOOGLE_DOCUMENT_AI_ENABLED=false
AZURE_DOCUMENT_INTELLIGENCE_ENABLED=false
```

---

## 7. Política de reintentos (`retry_policy.py`)

Pieza compartida por los 3 clientes LLM. Diseño:

- **Sólo se reintenta la llamada HTTP** al SDK. El parseo posterior (`output_parsed`,
  `tool_use.input`, `response.parsed`) NO se reintenta — un fallo de parseo sobre
  la misma respuesta dará siempre el mismo error, reintentar gasta tokens.
- Detecta retryable mediante 4 mecanismos en cascada:
  1. Tipo de excepción `httpx.*` (`ConnectError`, `ReadTimeout`, etc.).
  2. Status code si está expuesto en el error (`429`, `500`, `502`, `503`, `504`).
  3. Nombre de clase del SDK (`APITimeoutError`, `RateLimitError`, `ServerError`,
     `DeadlineExceeded`, …) — sin imports duros, navegando MRO.
  4. Substring en el mensaje (`"server disconnected"`, `"connection reset"`,
     `"broken pipe"`, …) — para casos de errores genéricos del SDK.
- Backoff: `min(cap, base * 2^(n-1)) * (1 + uniform(0, 0.25))` (jitter para evitar
  thundering herd entre instancias).
- Respeta `Retry-After` si lo encuentra en el error, topado por `cap_s`.
- Diferencia explícita entre **agotar reintentos** (log `error`) y **error no retryable**
  (log `warning`, propaga al instante).

---

## 8. Cómo se invoca este servicio

### 8.1 Quién lo llama hoy

- **`sv1` (email-albaranes-ingestor)** vía su `Service2HttpClient`, una vez por documento
  lógico (1 página de PDF o 1 imagen). El timeout en sv1 es **300 s** porque la extracción
  de varios proveedores en serie puede tardar.

### 8.2 Llamada local de prueba

```bash
curl -F "file=@/ruta/al/albaran.pdf" http://127.0.0.1:8000/v1/albaranes/extract
```

```python
import httpx, pathlib

with open("albaran.pdf", "rb") as f:
    response = httpx.post(
        "http://127.0.0.1:8000/v1/albaranes/extract",
        files={"file": ("albaran.pdf", f, "application/pdf")},
        timeout=300,
    )
response.raise_for_status()
envelope = response.json()
```

### 8.3 Arranque local (PyCharm / Windows)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env       # rellenar API keys
python main.py
```

> El servicio levanta uvicorn directamente desde `main.py`. Si prefieres `uvicorn` por CLI:
> `uvicorn interface_adapters.api.app:build_app --factory --host 0.0.0.0 --port 8000`
> (la app se construye con `build_app(Settings())`).

### 8.4 Decisión de despliegue Azure

Por las dependencias pesadas (SDKs de OpenAI, Anthropic, Google GenAI, Google Cloud
Document AI, Azure Document Intelligence) y por ser un servicio HTTP de larga duración
con peticiones que pueden tardar minutos, el destino natural es:

**Azure Container App** en el spoke DEV, con:
- `minReplicas = 0` o `1` (según necesidad de cold start)
- `maxReplicas` = 3-5 según volumen
- *Concurrency* moderada (cada petición consume CPU y memoria por la lectura del PDF)
- *Ingress* externo si lo llama sv1 desde fuera del spoke; *internal* si todo está en el mismo spoke

> **No** es buen candidato para Azure Functions: las peticiones pueden superar los 230 s
> de un Function HTTP Trigger en planes consumo, y el cold start de los SDKs pesados
> es notable.

---

## 9. Inputs / Outputs del servicio

### Inputs
| Origen     | Naturaleza                  | Detalle                                                       |
|------------|-----------------------------|---------------------------------------------------------------|
| HTTP       | `multipart/form-data`        | Único campo `file` con un PDF o imagen.                       |
| `.env`     | Configuración estática       | Flags + API keys + modelos + límites + política retry.        |
| Filesystem | `config/prompts.yaml`        | Cargado al arranque y mantenido en memoria.                   |

### Outputs
| Destino    | Naturaleza        | Detalle                                                                              |
|------------|-------------------|--------------------------------------------------------------------------------------|
| HTTP       | `application/json`| Envelope con la extracción de OpenAI a nivel raíz + sub-objetos para los demás.      |
| Filesystem | Logs rotados      | `logs/<fichero>.log` (config en `logging_config.py`).                                |
| Externos   | Llamadas API      | A OpenAI, Gemini, Claude, Google DocAI, Azure Doc Intelligence (cada una con creds). |

> **Importante:** el servicio NO almacena ficheros. El bytestream del documento vive
> en memoria mientras dura la petición.

---

## 10. Decisiones técnicas relevantes

1. **Extracción multi-proveedor en el MISMO request.** No hay un solo "ganador":
   el sv2 simplemente devuelve lo que cada proveedor produce, y el sv3 decide.
   Esto cuesta tokens y latencia, pero da robustez al merge downstream.
2. **Cada proveedor recibe el mismo prompt y el mismo schema Pydantic.** Los SDKs
   los traducen a su respectiva mecánica de structured output:
   - **OpenAI:** `responses.parse(text_format=Pydantic)` — soporte nativo.
   - **Gemini:** `generate_content(response_json_schema=...)`.
   - **Claude:** `messages.create(tools=[{...}], tool_choice="emit_albaran_extraction")` — fuerza el uso de la herramienta cuyo `input_schema` es el JSON Schema del Pydantic.
   - **Google Document AI / Azure DI:** ignoran el prompt (procesador entrenado), pero el adaptador mapea sus salidas (`entities`, `documents.fields`) al mismo schema.
3. **OpenAI es obligatorio en el envelope.** En `ExtractAlbaranPipeline.run` hay un
   `if openai_result is None: raise RuntimeError`. Hoy es una limitación: aunque
   `Settings` permita `ENABLE_OPENAI=false`, el pipeline rompe. **Mantener `ENABLE_OPENAI=true`**.
4. **Estricto en cabecera/líneas, laxo en `contexto_linea`.** `extra='forbid'` en el schema
   principal detecta derivas del prompt; `extra='ignore'` en el sub-bloque opcional
   permite evolución sin romper la API.
5. **Reintentos solo en LLMs.** Document AI y Azure DI ya tienen retry en su SDK/REST.
6. **OCR puros con mapeo de aliases.** `google_document_ai_client.py` mapea `supplier_name → proveedor_nombre`,
   `invoice_id → numero_albaran`, etc., y `mention_text` o `normalized_value` según disponibilidad.
   El score de matching prioriza alias exacto > suffix > prefix > confidence.
7. **`debug_payload` siempre presente.** Cada bloque de proveedor incluye `debug.<provider>_request`
   con el prompt, schema JSON y metadatos del attachment, y `debug.<provider>_response` con el
   `parsed`. Esto pesa en disco/red pero es vital para diagnosticar discrepancias entre proveedores
   en el sv3.
8. **`SHA-256` del fichero recibido viaja en `meta.source_sha256`.** Permite que sv3 verifique
   que el fichero que persiste es el mismo que se extrajo y correlacionar con el SHA que ya
   envió sv1.

---

## 11. Limitaciones conocidas y mejoras propuestas

| # | Limitación                                                                                              | Mejora propuesta                                                                                            |
|---|---------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| 1 | OpenAI obligatorio (raíz del envelope). Ver §10.3.                                                      | Refactor: que el envelope NO embeba uno de los proveedores en raíz, sino que todos vivan bajo `providers.{nombre}`. Coordinado con sv3. |
| 2 | Llamadas a proveedores **secuenciales**, no en paralelo.                                                | Ejecutar los N clientes en un `ThreadPoolExecutor` (cada SDK es síncrono y bloquea I/O); reduce latencia significativamente. |
| 3 | Sin auth en endpoints (`/health`, `/v1/albaranes/extract` son anónimos).                                | Añadir `APIKeyHeader` (`x-api-key`) o pasar a Container App con Easy Auth (Entra ID).                      |
| 4 | No hay rate limiting ni protección frente a clientes que envían PDFs gigantes en paralelo.              | `slowapi` o el limit de Container Apps Ingress.                                                            |
| 5 | El `prompt_key` es global (`Settings.prompt_key`); no se puede cambiar por petición.                    | Aceptar `prompt_key` opcional en el form multipart.                                                        |
| 6 | Cargar **todos** los proveedores OCR en cada petición (los habilitados) gasta tokens aunque no aporten. | Endpoint alternativo `/v1/albaranes/extract?providers=openai,claude` para subset on-demand.                |
| 7 | Logs solo en disco.                                                                                     | Handler adicional a Application Insights cuando se despliegue en Azure.                                    |
| 8 | El `debug_payload` puede pesar mucho (incluye el JSON Schema completo).                                 | Flag `?debug=false` para omitirlo en clientes que solo quieren el `data`.                                  |
| 9 | El YAML de prompts es estático (cambio requiere reinicio).                                              | Hot-reload con `watchdog`, o cargarlo desde Blob Storage / App Configuration.                              |

---

## 12. Resumen de un vistazo

| Característica         | Valor                                                                            |
|------------------------|----------------------------------------------------------------------------------|
| Tipo                   | API HTTP (FastAPI + uvicorn)                                                     |
| Lenguaje               | Python 3.12                                                                      |
| Entrada                | `POST /v1/albaranes/extract` (multipart `file`: PDF o imagen)                    |
| Salida                 | Envelope JSON multi-proveedor + `/health` con introspección                      |
| Persistencia propia    | Ninguna (sólo logs)                                                              |
| Concurrencia           | Una request por proceso uvicorn worker; proveedores secuenciales por request     |
| Despliegue objetivo    | Azure Container App (recomendado)                                                |
| Punto de entrada       | `python main.py` (o `uvicorn interface_adapters.api.app:build_app --factory`)    |
| Dependencias clave     | `fastapi`, `uvicorn`, `pydantic-settings`, `openai`, `anthropic`, `google-genai`, `google-cloud-documentai`, `httpx`, `PyYAML` |

---

*Documento generado a partir del análisis del código del paquete `sv2.zip` aportado.*
