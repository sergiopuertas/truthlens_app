# TruthLens — Estudio de viabilidad técnica

Fecha: 2026-10-13 · Estado: prototipo local (Python)

## 1. Veredicto

**Viable como prototipo local.** Todas las piezas existen como componentes abiertos o con capa gratuita. La dificultad no es técnica (captura, VLM, búsqueda), sino la **fiabilidad del veredicto**: la mayoría de noticias nuevas no tienen aún un fact-check publicado, así que el sistema debe decir "no verificable" con frecuencia y alertar solo cuando hay evidencia sólida de fuentes de confianza.

## 2. Pipeline

```
Captura (mss) → detector de cambio + dwell → VLM local (Ollama) → noticias/posts estructurados
  → dedupe (SQLite) → extracción de afirmaciones verificables
  → evidencia: Google Fact Check API + búsqueda web (SearXNG/Tavily/DDGS) + fetch (trafilatura)
  → credibilidad de dominio (listas configurables) → verificador LLM (solo con la evidencia aportada)
  → vectores de riesgo (falso, engañoso, sesgo, falacia, odio; ampliables por YAML)
  → alerta (Tk / consola / notificación nativa) con explicación y fuentes
```

## 3. Componentes elegidos

| Función | Elección | Alternativa | Notas (de la investigación) |
|---|---|---|---|
| Captura | `mss` | `dxcam` (solo Windows, más rápido) | Multiplataforma, sin dependencias; usa APIs nativas |
| Lectura de pantalla (VLM) | `qwen3-vl:8b` vía Ollama | `qwen3-vl:2b/4b/30b/32b`, `llama3.2-vision:11b`, MiniCPM-V | En Ollama hay 2b, 4b, 8b, 30b, 32b y 235b; el 8b pesa ~6 GB y tiene contexto de 256K. Soporta salida estructurada con imágenes |
| OCR dedicado (fase 2) | PaddleOCR-VL-1.6 | MinerU2.5, GLM-OCR | Según Roboflow (ago-2026) lideran OmniDocBench v1.6 con 0.9–1.2B parámetros, por encima de Qwen3-VL-235B en OCR de documentos. Útil para filtrar antes del VLM |
| Fact-checks existentes | Google Fact Check Tools API (`claims:search`) | Repositorio EDMO (web), Maldita (BD con licencia de pago), EUvsDisinfo | API gratuita dentro de cuota; devuelve rating, publisher y URL (ClaimReview). **Hay que comprobar con tu clave la cobertura de Maldita/Newtral** |
| Búsqueda web | SearXNG autoalojado | Tavily (1.000 créditos/mes gratis), DDGS | SearXNG: sin coste por consulta, requiere activar formato JSON. Según un comparativo de Parallel, Brave ya no tiene capa gratuita |
| Extracción de páginas | `trafilatura` | `readability-lxml` | Mejor F-score en benchmarks de extracción de artículos |
| Credibilidad de fuentes | Lista propia (tiers) + CSV de baja credibilidad | Iffy.news (MIT, ~2.040 dominios), CRED-1, MBFC (API solo en RapidAPI, de pago) | CRED-1 e Iffy son señal **negativa** únicamente: un dominio ausente = desconocido, no fiable |
| Odio (señal extra, opcional) | `cardiffnlp/twitter-xlm-roberta-base-hate-spanish` | RoBERTuito (modelos es), LLM zero-shot | Entrenado con HaterNet + SemEval-2019 (es). **No usar Perspective API: cierra el 31-dic-2026** |
| Check-worthiness | LLM zero-shot (v1) | XLM-R fine-tuned con MultiCW (123.722 muestras, 16 idiomas) | Los modelos afinados superan al zero-shot entre dominios (Hyben et al.) → mejora para fase 2 |
| Diseño del verificador | Pipeline propio inspirado en Loki/OpenFactVerification (MIT) | Usar Loki directamente | Loki: decomposer → checkworthy → queries → evidence → verify. Por defecto pide OpenAI+Serper, por eso se reimplementa ligero con LLM local |
| Salida estructurada | Ollama `format=` + Pydantic | — | Esquema JSON forzado también con visión |
| Alertas | Tkinter (stdlib) + `desktop-notifier` opcional | PySide6 | En macOS las notificaciones nativas exigen un Python firmado |

## 4. Riesgos y mitigaciones

| Riesgo | Mitigación implementada |
|---|---|
| Alucinación del verificador | El LLM solo ve la evidencia recuperada y cita por ID (no inventa URLs); `refuted`/`misleading` se degradan a `unverifiable` si no cita ≥1 fuente de confianza |
| Falsos positivos que erosionan confianza | Umbrales de confianza por vector; citas textuales validadas contra el texto original; botón "falso positivo" que guarda feedback |
| Cobertura baja de fact-checks | "No verificable" es el resultado por defecto; no se alerta sin evidencia |
| "Sesgo" y "falacia" son subjetivos | Se presentan como indicadores con cita textual, nunca como veredicto de verdad |
| Privacidad (captura continua; RGPD) | VLM y verificador locales; solo salen a Internet los textos de las afirmaciones; lista de ventanas bloqueadas; botón de pausa |
| Latencia/consumo | Detector de cambio (dhash) + tiempo de permanencia ("dwell") + caché SQLite. Latencia real por captura: **no medida**, depende del hardware |
| Hardware | Recomendado GPU ≥ 8 GB VRAM o Apple Silicon con 16 GB; si no, `qwen3-vl:4b`/`2b` |
| Dependencia de listas "de confianza" | Son editables en `config.yaml`; la elección es inevitablemente opinable y conviene revisarla |

## 5. Hoja de ruta

1. **Prototipo (este repo):** todo el pipeline en local con CLI + ventana de alertas.
2. Medir latencia y calidad con un set de capturas reales; ajustar umbrales.
3. Prefiltro con OCR dedicado y clasificador de check-worthiness afinado.
4. Ampliar vectores (clickbait, conspiración, deepfakes/imágenes → `claims:imageSearch`).
5. Empaquetado (PyInstaller/Tauri) y despliegue real.
