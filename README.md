# Grant Intake Copilot (MVP)

Prototipo local que lee una solicitud de grant (PDF, DOCX o TXT), usa un LLM para extraer y clasificar
8 campos contra taxonomías cerradas, muestra la evidencia verificada y un confidence heurístico por campo,
y deja que una persona apruebe, edite o rechace cada valor. Del registro aprobado salen el JSON canónico,
un payload compatible con Salesforce (que **no** se envía) y el audit trail.

## 1. Qué es y qué no es

**Es:** una herramienta interna de revisión asistida: `Upload → AI → Confidence → Evidence → Human Review →
Approved Record → Salesforce Payload → Audit trail`, con métricas del piloto. Ninguna salida de la IA llega a un
registro aprobado sin una decisión humana explícita por campo.

**No es:** un reemplazo de Salesforce ni una integración con él (no hay OAuth, API, Apex ni MuleSoft), ni un
sistema multiusuario (no hay autenticación ni SSO), ni un OCR. Tampoco usa fine-tuning, embeddings ni RAG.
Toda la lógica de IA, confidence y revisión es independiente de Salesforce: solo `SalesforceAdapter` conoce
su formato.

## 2. Requisitos e instalación (Windows)

- Windows 10 con Anaconda en `C:\Users\User\anaconda3`. `conda` no tiene que estar en el PATH.
- Opcional: una API key de **Gemini, Claude (Anthropic), OpenAI o DeepSeek**. Sin key, la app usa el
  proveedor `fake` (offline).

```
cd D:\PROYECTOS_PERSONALES\granter
C:\Users\User\anaconda3\Scripts\conda.exe create -n granter python=3.12 -y
C:\Users\User\anaconda3\envs\granter\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Usa siempre `C:\Users\User\anaconda3\envs\granter\python.exe`. El `python` del PATH es el alias de la
Microsoft Store.

**API keys, dos opciones:**
- **En la app (recomendado para demos):** en la barra lateral, elige el proveedor en **LLM provider**,
  pega la key en **API key** (campo de contraseña) y elige el modelo. La key vive solo en la memoria de esa
  sesión del navegador: no se escribe en disco, ni en la base de datos, ni en logs, y se borra al cerrar la
  pestaña o cambiar de proveedor.
- **En `.env`** (nunca se versiona): `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` y
  `DEEPSEEK_API_KEY`. También son opcionales `GEMINI_MODEL`, `CLAUDE_MODEL`, `OPENAI_MODEL` y
  `DEEPSEEK_MODEL`, y `LLM_PROVIDER` (el proveedor preseleccionado al abrir). Una key escrita en la app tiene
  prioridad sobre la de `.env`.

| Proveedor | Modelos sugeridos (el primero es el default) | Salida estructurada |
|---|---|---|
| Google Gemini | `gemini-3.8-flash`, `gemini-3.1-flash-lite`, `gemini-2.5-flash-lite` (el más barato) | `response_schema` (enums cerrados) |
| Anthropic Claude | `claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5` | structured outputs (`messages.parse`) |
| OpenAI | `gpt-5.6-terra`, `gpt-5.6-luna` (el más barato) | `response_format: json_schema` |
| DeepSeek | `deepseek-flash`, `deepseek-v4-pro` | solo modo JSON: el schema va en el prompt |

El selector de modelo también acepta cualquier otro nombre. Precios de Gemini a septiembre de 2026 (pago
estándar, USD por 1M tokens de entrada/salida): 3.8-flash $0.75/$3.75, 3.1-flash-lite $0.25/$1.50 y
2.5-flash-lite $0.10/$0.40. Los tres tienen nivel gratuito. Con cualquier proveedor, la salida se valida
siempre con el mismo schema Pydantic, con un reintento.

Tests: `C:\Users\User\anaconda3\envs\granter\python.exe -m pytest -q`. Cada proveedor tiene un test offline
que usa su SDK real con la red simulada, y un test en vivo que solo corre si su key está en `.env`.

## 3. Cómo correr la demo

```
C:\Users\User\anaconda3\envs\granter\python.exe -m streamlit run app.py
```

Abre http://localhost:8501. La barra lateral indica el proveedor activo (en verde, con su modelo) o Fake
(en amarillo). Escribe tu nombre en **Reviewer**.

**Sin key (Fake, determinista).** En *Review*, elige un documento en **Load sample document** y pulsa **Run AI extraction**:

1. `01_clear_housing_alliance.pdf` (caso claro): los 8 campos salen **High**. Pulsa **Approve all
   High-confidence fields** y luego **Finalize review**. En las pestañas finales, pulsa **Generate
   Salesforce Payload** y descarga el JSON canónico, el payload y el audit trail (CSV).
2. `02_ambiguous_youth_center.docx` (ambiguo): el DOCX no tiene páginas, así que se muestran *Sections*.
   Resultado: `grant_type` y `primary_strategy` en Low; `issues`, `geography`, `target_population`,
   `amount_requested` y `project_summary` en Medium; `organization_name` en High. En `amount_requested`
   hay una cita inventada que aparece como **Unverified quote** (el documento menciona $1.2M de
   presupuesto total y $180k solicitados). El aprobado masivo solo toca `organization_name`; el resto
   exige una decisión individual. Prueba *Save edit* con otra categoría y *Reject*.
3. `03_multi_strategy_coalition.txt` (varias opciones plausibles): `primary_strategy` en Low (Policy
   Advocacy, Community Organizing y Coalition Building están igual de presentes), `issues` con 5 valores y
   `target_population` en Low.
4. `battered-womens-shelter-of-summit-medina-2023-general-supportpacket-1.pdf` (caso real, no ficticio):
   una solicitud de grant real y públicamente disponible, incluida para probar el pipeline contra un
   documento real en lugar de uno sintético. Fake no tiene respuesta para él (deja los 8 campos en *Needs
   review*); pruébalo con un proveedor real de **pago** — ver el aviso de privacidad en la sección 12, que
   no aplica al nivel gratuito para este documento.

Botón **Go** junto a cada cita: salta a la página o sección donde se verificó; la cita queda resaltada.
Para regenerar los ejemplos y sus respuestas fake:
`C:\Users\User\anaconda3\envs\granter\python.exe sample_data\make_samples.py`. El script comprueba que
cada cita fake se verifique contra el documento generado.

**Con key.** Elige el proveedor, pega la key en la barra lateral y repite los pasos con los mismos
samples. Los niveles de confidence dependerán de lo que devuelva el modelo. Con niveles gratuitos, usa
**solo los documentos ficticios** (ver sección 12). Sin key, cualquier documento subido que no sea un sample deja los
8 campos en *Needs review* (Fake no tiene respuesta para él).

*Audit trail* muestra todas las decisiones (filtrables por documento y campo) y permite exportarlas a CSV.
*Pilot metrics* muestra las métricas de los reviews finalizados.

## 4. Arquitectura

```mermaid
flowchart LR
    U[Upload PDF/DOCX/TXT<br/>o sample] --> I[ingestion.py<br/>validación + texto por página]
    I --> P[pipeline.py<br/>schema dinámico + 1 llamada LLM]
    P --> L{{LLMProvider}}
    L --> G[Gemini · Claude ·<br/>OpenAI · DeepSeek]
    L --> F[FakeLLMProvider]
    P --> E[Verificación de evidencia<br/>página real, verified]
    E --> C[confidence.py<br/>heuristic-v1]
    C --> R[review.py<br/>Human Review en Streamlit]
    R --> A[ApprovedGrantApplication<br/>JSON canónico]
    A --> S[SalesforceAdapter<br/>salesforce_mapping.yaml]
    S --> PL[Salesforce payload]
    PL -.-> SF[Future Salesforce API]
    R --> DB[(SQLite / PostgreSQL<br/>audit trail)]
    A --> DB
    style S stroke-dasharray: 5 5
    style SF stroke-dasharray: 5 5
```

| Archivo | Responsabilidad |
|---|---|
| `app.py` | UI Streamlit (Review, Audit trail, Pilot metrics). Solo renderiza y llama a `src/`. |
| `src/config.py` | Carga los YAML y `.env`; calcula `prompt_version` y `taxonomy_version` (sha256, 12 caracteres). |
| `src/models.py` | Enums y modelos Pydantic: `GrantApplication` (predicción IA) y `ApprovedGrantApplication` (aprobado). |
| `src/ingestion.py` | Validación del upload y extracción de texto por página o sección. |
| `src/llm.py` | Protocolo `LLMProvider`: `GeminiProvider`, `ClaudeProvider`, `OpenAIProvider`, `DeepSeekProvider` y `FakeLLMProvider`. |
| `src/pipeline.py` | Schema dinámico, prompt, llamada, verificación de citas y armado de `GrantApplication`. |
| `src/confidence.py` | Scorer heurístico puro y reemplazable. |
| `src/review.py` | Decisiones, reglas de bloqueo, finalización y generación del payload. |
| `src/salesforce_adapter.py` | Único módulo que conoce el formato de Salesforce. |
| `src/db.py` | Tablas SQLAlchemy y consultas de audit trail y métricas. |

## 5. Flujo de datos

**Qué se envía al LLM** (una sola llamada por documento, **solo al proveedor elegido**):
- *System instruction*: `prompts/grant_intake.md`, con las taxonomías vigentes insertadas, las reglas y el
  aviso anti prompt injection. En DeepSeek también incluye el JSON schema, porque no acepta un schema de
  respuesta.
- *Contenido*: el texto extraído del documento, con marcadores `[[PAGE n]]`.
- *Schema de respuesta*: el modelo Pydantic dinámico (enums cerrados). No se envía ni el nombre del archivo
  ni metadatos. El archivo original nunca se guarda.
- La **API key** solo viaja al proveedor. En la tabla `runs` se guardan el proveedor y el modelo, nunca la
  key.

**Qué se guarda** (el contenido, la salida del modelo y la auditoría van en tablas separadas):

| Tabla | Contenido |
|---|---|
| `documents` | id (sha256 de los bytes), nombre, tipo, nº de páginas, fecha |
| `document_pages` | **contenido**: texto por página o sección |
| `runs` | proveedor, modelo, versión del modelo, `prompt_version`, `taxonomy_version`, `app_version`, `confidence_method`, estado, inicio y fin del review, revisor |
| `model_outputs` | **salida cruda del modelo** (JSON tal cual) |
| `predictions` | valor predicho, confidence, nivel y evidencia (con página recalculada y `verified`) por campo |
| `review_decisions` | **auditoría**: una fila por decisión (approve/edit/reject, valor, `bulk`, revisor, timestamp). Solo se agregan filas y la última por campo es la vigente. |
| `approved_records` | JSON canónico aprobado y payload de Salesforce |

El audit trail es un join (`db.audit_trail`) y no duplica datos: document_id, field_name, predicted_value,
confidence, reviewed_value, review_action, evidence, timestamp, model_name, model_version, prompt_version y
taxonomy_version.

Nada del contenido del documento ni de la salida del modelo se escribe en logs. Los errores muestran solo el
tipo (p. ej. `LLM extraction failed (ValidationError)`).

## 6. Cómo editar taxonomías, thresholds y mapping

Todo está en `config/`. Los archivos se leen en cada operación, así que no hace falta tocar código (en la app,
basta con recargar).

- **Taxonomías**: `grant_types.yaml`, `strategies.yaml`, `issues.yaml`, `geographies.yaml` y
  `populations.yaml` son listas YAML simples. Mantén `Other` en todas. Al cambiarlas cambia
  `taxonomy_version`, que queda registrada en cada run. El schema del LLM y la validación del registro
  aprobado se construyen desde estos archivos.
- **Thresholds y pesos**: `settings.yaml` → `confidence.levels` (High ≥ 0.90, Medium ≥ 0.70), `weights`,
  `quote_scores`, `caps`, `synonyms` (términos que cuentan como "el valor aparece en la cita"),
  `evidence.fuzzy_ratio` (0.85) e `ingestion.*` (20 MB, mínimo de 200 caracteres en PDF, 40 párrafos por
  sección DOCX).
- **Mapping Salesforce**: `salesforce_mapping.yaml` → `object`, `field`, `type` y, de forma opcional,
  `max_length` y `value_map: {"Valor taxonomía": "API_Value"}`. Los textos truncados y los valores sin
  `value_map` generan entradas en `_warnings`.
- **Prompt**: `prompts/grant_intake.md` (`{taxonomies}` se reemplaza en tiempo de ejecución). Cambiarlo
  cambia `prompt_version`.

## 7. Confidence heurístico

**No es una probabilidad calibrada.** La UI siempre muestra "(heuristic)". `confidence_method = heuristic-v1`.

```
confidence = 0.50·calidad + 0.15·cantidad + 0.20·coherencia + 0.15·self_confidence
```

- **Calidad**: promedio por cita. Explícita verificada = 1.0, inferida verificada = 0.6, no verificada = 0.1.
  Sin citas = 0.
- **Cantidad**: min(nº de citas verificadas, 2) / 2.
- **Coherencia**: 1.0 si el valor (o un sinónimo de `settings.yaml`) aparece en una cita verificada; 0.5 si
  no. En multi-label, promedio por valor. En el monto, se comparan números normalizados (`$250,000`, `$180k`,
  `$1.2 million`). En `project_summary`, que es una síntesis y nunca aparece literal: 1.0 si al menos el 60 %
  de sus palabras de 4 letras o más aparece en las citas verificadas.
- **Self-confidence**: el valor que reporta el LLM (señal débil).
- **Topes**: campo clasificado sin evidencia verificada ≤ 0.60; valor `Other` ≤ 0.60; valor nulo o lista
  vacía = 0 y estado *Needs review*.

**Verificación de evidencia:** se normaliza (minúsculas, espacios colapsados, comillas tipográficas a rectas)
y se busca la cita exacta en cada página. Si no aparece, se prueba `difflib` con ventanas del mismo largo y se
exige un ratio ≥ 0.85. La página que se guarda es la **encontrada por el código**; la que dice el modelo se
ignora.

**Limitaciones:** los pesos se pusieron a mano. Un modelo muy seguro y con citas literales puede estar
equivocado igual (p. ej. elegir la categoría incorrecta citando texto real). Los niveles solo sirven para
priorizar la revisión y nunca deciden nada por sí mismos.

**Cómo reemplazarlo:** `confidence.score(field, prediction, evidence, settings) -> float` es una función pura.
Con suficientes filas en `review_decisions` (aprobado = correcto, editado o rechazado = incorrecto), se
entrena un calibrador (regresión logística o isotónica) sobre las mismas señales, se sustituye `score` y se
cambia `confidence.method` (p. ej. `calibrated-v1`) para que quede registrado en cada run.

## 8. Métricas del piloto y su sesgo

Se calculan sobre los **reviews finalizados**, con la **última decisión por campo** (`db.pilot_metrics`):

- **Métrica principal: % de campos poblados por la IA aceptados sin modificación** = approved / (approved +
  edited + rejected), sobre campos con predicción no nula. Se muestra con y sin aprobaciones masivas
  (`bulk=True`).
- Aceptación y corrección por campo; "accuracy by field" = aceptación (proxy: aprobado = correcto).
- Tasa de manual review (campos sin predicción usable).
- Error rate en Low confidence = (edited + rejected) / decididos entre los Low.
- Confidence promedio de los aceptados frente a los corregidos (si el heurístico sirve, el primero debe ser
  mayor).
- Tiempo promedio de revisión (desde que se muestran las predicciones hasta que se finaliza).

**Sesgo:** la "verdad" es la decisión humana. Si el revisor acepta por inercia lo que propone la IA (sesgo de
automatización, que el botón de aprobado masivo facilita), las métricas sobreestiman la calidad. Por eso se
separan las aprobaciones masivas, y conviene auditar una muestra con doble revisión ciega.

## 9. Qué cambiaría para integrarse de verdad con Salesforce

1. **Autenticación**: una Connected App con OAuth 2.0 **JWT Bearer flow** (servidor a servidor, sin
   contraseña; certificado en un vault) y un usuario de integración con permisos mínimos.
2. **Escritura transaccional**: la **Composite API** (`/composite` con `allOrNone: true`) o la **sObject Tree
   API** para crear `Account` + `Grant_Request__c` en una sola llamada, referenciando el Account creado
   (`@{refAccount.id}`).
3. **No duplicar Accounts**: **upsert por External ID** (p. ej. `Account.EIN__c`) en lugar de insertar
   siempre; también se puede consultar antes con SOQL o usar Duplicate Rules.
4. **Validar picklists contra `describe()`**: al iniciar, leer los valores activos de cada picklist o
   multipicklist y compararlos con la taxonomía y el `value_map`. Si no coinciden, se bloquea el envío.
5. **Errores parciales y reintentos**: interpretar los errores por registro de la respuesta compuesta,
   reintentar con backoff los 5xx y `REQUEST_LIMIT_EXCEEDED`, no reintentar los errores de validación, y
   registrar el `Id` de Salesforce y el resultado en una tabla nueva (p. ej. `salesforce_syncs`) con
   idempotencia por `run_id`.
6. **Sandbox antes que producción**: probar en un sandbox con los mismos campos custom y pasar a producción
   con un checklist de permisos y límites de API.

**Qué no cambia:** ingesta, pipeline, LLM, confidence, revisión, modelos, base de datos y audit trail. Solo
cambian `salesforce_adapter.py` (si cambia el mapping) y se agrega un **cliente** nuevo (p. ej.
`salesforce_client.py`) que recibe el payload ya generado y lo envía después de `finalize`.

## 10. Extensiones previstas

- **OCR**: en `src/ingestion.py` hay un único punto marcado con `# ponytail:` donde hoy se lanza
  "OCR required". Ahí se conecta un extractor (p. ej. Tesseract vía `page.get_textpage_ocr()` de PyMuPDF, o
  un servicio de Document AI) que devuelva texto por página. El resto del flujo no cambia.
- **Otro proveedor LLM** (p. ej. Mistral): se crea en `src/llm.py` una clase con `name` y
  `extract(document_text, schema, prompt, filename="")`. Envía `prompt` como mensaje de sistema y
  `document_text` como mensaje de usuario, y devuelve `_validated(call, schema, model)`, que valida con el
  schema Pydantic y reintenta una vez. Después se registra la clase en `PROVIDERS` (`llm.py`), se agrega su
  fila en `config.PROVIDERS` (variable de la key, variable del modelo y modelos sugeridos) y su etiqueta en
  `PROVIDER_LABELS` (`app.py`). Si su API es compatible con la de OpenAI, basta con heredar de
  `OpenAIProvider` y cambiar `base_url`, como hace `DeepSeekProvider`. Pipeline, confidence y revisión no
  cambian.
- **PostgreSQL**: `pip install "psycopg[binary]"` y
  `DATABASE_URL=postgresql+psycopg://usuario:clave@host:5432/granter`. Las tablas usan el tipo `JSON`
  portable y se crean con `create_all`. Para evolucionar el esquema en producción, se agrega Alembic en ese
  momento.

## 11. Decisiones tomadas

- **FakeLLMProvider busca por nombre de archivo** (`fake_responses/<stem>.json`), no por `document_id`: los
  bytes de PDF/DOCX generados cambian en cada ejecución (timestamps internos). Por eso `extract` recibe un
  argumento opcional `filename`, que los proveedores reales ignoran (desviación mínima de la firma del
  protocolo).
- **Prompt como mensaje de sistema y documento como mensaje de usuario** en los 4 proveedores: separa
  instrucciones de datos no confiables (primera barrera contra prompt injection; la segunda es el schema
  estricto).
- **Proveedores y keys en la UI**: se agregaron Claude, OpenAI y DeepSeek a pedido del usuario (antes
  `OpenAIProvider` estaba fuera de alcance). La key escrita en la barra lateral solo vive en la sesión del
  navegador; sin key, se usa Fake y la barra lateral lo indica.
- **Por proveedor**: Claude usa structured outputs sin `temperature` (los modelos actuales rechazan
  parámetros de sampling). OpenAI usa `json_schema` no estricto (el modo estricto rechaza parte de nuestras
  restricciones). DeepSeek usa el modo JSON con el schema en el prompt. En todos los casos la validación
  Pydantic propia es la barrera real. No se activó el `fallbacks` de Anthropic ante rechazos: un rechazo deja
  los campos en *Needs review*.
- **Modelos sugeridos** según la documentación de cada proveedor a septiembre de 2026. **Ninguno se probó en
  vivo** (no había keys); los tests offline sí ejercitan cada SDK real con la red simulada.
- **Cualquier fallo del LLM** (validación tras el reintento, red, autenticación) deja los 8 campos en *Needs
  review*, con el run en estado `llm_failed` y un mensaje con el tipo de error. Nunca se cae la app.
- **El schema valida longitudes**: resumen ≤ 600, citas ≤ 300, 1–3 citas y `self_confidence` en [0, 1]. El SDK
  de Gemini las traduce a `maxLength`/`enum`/`nullable` (comprobado offline).
- **Lista vacía en multi-label = sin predicción** → *Needs review*, igual que `null`.
- **Coherencia de `project_summary`** por solapamiento de palabras (≥ 60 %), porque una síntesis nunca
  aparece literal. Los **sinónimos** están en `settings.yaml`.
- **Edit igual a la predicción** se guarda como `approve`. **Edit vacío** se rechaza ("usa Reject").
  **Approve** está deshabilitado si no hay predicción.
- **Tras finalizar**, las decisiones quedan bloqueadas (el registro aprobado no puede desincronizarse).
- **El payload de Salesforce** se genera al pulsar el botón y se guarda en
  `approved_records.salesforce_payload`.
- **Métricas solo de reviews finalizados.** El tiempo de revisión va desde la creación del run (las
  predicciones se muestran justo después) hasta `finalize`.
- **Run activo** en `st.session_state` y también en el query param `?run=`, para que recargar la página
  retome el mismo run desde la base de datos.
- **DOCX**: los estilos `Heading*` y `Title` abren sección nueva; se ignoran párrafos vacíos y tablas.
- **Si cambia la taxonomía**, las predicciones antiguas conservan sus valores. El widget solo ofrece los
  valores vigentes y el registro aprobado se valida contra la taxonomía vigente.
- **Nombres de archivo, texto del documento y citas** se escapan con `html.escape` antes de renderizarse.

### Ledger de `ponytail-debt`

Atajos deliberados, cada uno con su techo y su disparador:

- `src/confidence.py:6`: pesos del confidence puestos a mano. Techo: no está calibrado. Mejora: calibrar con
  las decisiones humanas acumuladas en `review_decisions`.
- `src/ingestion.py:39`: sin OCR. Techo: los PDFs escaneados se detienen con "OCR required". Mejora:
  conectar un extractor OCR cuando haya PDFs escaneados.
- `src/ingestion.py:69`: DOCX solo lee párrafos. Techo: las tablas se ignoran. Mejora: leer `doc.tables` si
  los presupuestos vienen en tablas.
- `src/pipeline.py:2`: una sola llamada LLM para los 8 campos. Techo: calidad por campo. Mejora: separar
  extracción y clasificación si la calidad por campo lo exige.
- `src/pipeline.py:61`: verificación fuzzy O(n·m) con difflib. Techo: documentos grandes. Mejora: búsqueda
  indexada si hay documentos de más de 100 páginas.

5 marcadores, ninguno sin disparador.

**No construido a propósito:** FastAPI o capa HTTP (la UI solo llama a `src/`), Alembic, patrón repository,
inyección de dependencias, logging estructurado, caché, colas, autenticación y OCR.

## 12. Aviso de privacidad

En el **nivel gratuito** de Google AI Studio, Google puede usar el contenido enviado para mejorar sus
productos. Con el nivel gratuito, **procesa solo los documentos ficticios** de `sample_data/`
(`01_clear_housing_alliance.pdf`, `02_ambiguous_youth_center.docx`, `03_multi_strategy_coalition.txt`).
El cuarto sample (`battered-womens-shelter-of-summit-medina-2023-general-supportpacket-1.pdf`) es una
solicitud real y públicamente disponible: solo procésalo con un proveedor de **pago** con términos de
tratamiento de datos adecuados. Revisa también
los términos de datos de Anthropic, OpenAI y DeepSeek antes de enviar solicitudes reales (DeepSeek procesa
los datos en servidores ubicados en China). Para solicitudes reales, usa planes de pago con términos de
tratamiento de datos adecuados, o el modo `fake`.

El texto completo del documento se envía al proveedor elegido (sección 5). La base de datos local guarda ese
texto en `document_pages`: protege `data/` y no la compartas.

**Keys en la UI:** se guardan solo en la memoria del servidor de Streamlit, dentro de esa sesión, y nunca
se persisten. En esta app local no salen de tu máquina salvo hacia el proveedor. Si algún día se despliega
para otras personas, hay que servirla por HTTPS; aun así, cada persona debería usar su propia key.
