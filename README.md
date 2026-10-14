# TruthLens (prototipo)

Le das una URL (artículo, portada/sección o post de red social); extrae el texto, verifica las afirmaciones con fact-checks y búsqueda web, y señala si algo es falso, engañoso, sesgado, falaz o incita al odio, con explicación y fuentes.

## Instalación

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt                                  # y rellena las claves
python -m truthlens doctor                             # comprueba claves, modelo y búsqueda
```

Claves (en `.env`):
- `GROQ_API_KEY` (console.groq.com, capa gratuita): el LLM, alojado, sin cargar modelos en tu equipo.
- `GOOGLE_FACTCHECK_API_KEY`: clave de API de Google Cloud con "Fact Check Tools API" activada.
- Búsqueda web: `pip install ddgs` ya basta (sin clave); mejor `TAVILY_API_KEY` o SearXNG.

## Uso

```bash
python -m truthlens https://www.rtve.es/noticias/verificartve/        # equivale a `check <url>`
python -m truthlens check https://x.com/usuario/status/123 --json
python -m truthlens check https://medio.es/seccion --max-articles 3
python -m truthlens text "El Gobierno prohibirá el efectivo mañana"    # texto pegado
```

## Qué soporta la entrada

| Origen | Cómo |
|---|---|
| Artículo web | `trafilatura` (texto principal) |
| Portada / sección | detecta que hay poco texto y sigue hasta `scrape.max_articles` titulares |
| X/Twitter | oEmbed público |
| Bluesky, Mastodon, Telegram, Reddit | API pública de cada uno |
| Facebook, Instagram, TikTok, Threads | solo metaetiquetas públicas; casi siempre exigen login → se avisa y se usa `text` |
| Webs con JavaScript/paywall | no soportado (error claro); siguiente paso posible: navegador headless |

## Proveedor LLM (`config.yaml → llm`)

`groq` (por defecto) · `cerebras` · `openrouter` · `ollama` (local) · `custom` (cualquier API compatible con OpenAI, con `base_url` y `model`). Los ids de modelo cambian: `doctor` comprueba que el configurado existe y lista alternativas. Ante 429/5xx reintenta con espera; con Ollama precarga el modelo (`keep_alive`) antes de la primera consulta.

## Cómo decide alertar

- **Falso / engañoso**: solo si el verificador cita ≥1 fuente de confianza (tiers en `config.yaml`) y confianza ≥ 0.7; si no, es "no verificable".
- **Sesgo / falacia / odio** (y los vectores que añadas): el LLM debe dar una cita literal del texto; si no aparece, se descarta.

## Privacidad

Ahora el texto de cada artículo y las afirmaciones se envían al proveedor LLM elegido (antes todo era local). Revisa sus condiciones de uso de datos; para mantenerlo local usa `provider: ollama`.

## Tests

`pytest -q` (21 tests, sin red ni modelo).
