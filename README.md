# TruthLens — aplicación de escritorio para macOS

TruthLens analiza artículos, portadas/secciones y publicaciones de redes sociales. Extrae afirmaciones verificables, las contrasta con fact-checks y fuentes web, y busca señales de información falsa o engañosa, sesgos, falacias e incitación al odio.

Esta versión conserva el motor de análisis original y añade una aplicación de barra de menús para macOS: se abre desde el icono `TL`, muestra una ventana compacta, permite ajustar los parámetros y enseña una rueda animada, porcentaje y estado durante el análisis. Cuando termina, la ventana se amplía para mostrar el informe con afirmaciones y fuentes.

## Inicio rápido en macOS

Necesitas macOS y Python 3.11 o 3.12. Desde Terminal, dentro de la carpeta del proyecto:

```bash
python3 -m venv .venv-desktop
source .venv-desktop/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python run_desktop.py
```

La app aparecerá como `TL` en la barra de menús. Selecciona **Abrir TruthLens**. Para detenerla, usa **Salir** desde ese menú.

## Crear una app instalable (.app)

En macOS, ejecuta:

```bash
./build_macos.sh
```

El paquete se crea en `dist/TruthLens.app`. Puedes moverlo a `/Applications`. La compilación debe hacerse en un Mac; no es posible generar un bundle macOS nativo desde Linux/Windows. La primera apertura puede estar sujeta a los avisos de seguridad habituales de macOS para aplicaciones compiladas localmente.

## Configuración y claves

El archivo `.env` real no forma parte de este paquete. Así se evita incluir claves privadas accidentalmente. La aplicación busca credenciales en este orden:

1. La ruta indicada por `TRUTHLENS_ENV_FILE`, si la has definido.
2. `.env` en la carpeta del proyecto (útil al ejecutar `run_desktop.py`).
3. `~/Library/Application Support/TruthLens/.env` (recomendado para la app instalada).

En el primer arranque se copia una plantilla a `~/Library/Application Support/TruthLens/.env.example`. Duplica esa plantilla, llama al duplicado `.env` y rellénalo localmente. Nunca compartas ese archivo ni lo añadas al zip de distribución.

Variables compatibles:

- `GROQ_API_KEY`: proveedor Groq (opción inicial).
- `CEREBRAS_API_KEY`: proveedor Cerebras.
- `OPENROUTER_API_KEY`: proveedor OpenRouter.
- `GOOGLE_FACTCHECK_API_KEY`: API de Google Fact Check Tools.
- `TAVILY_API_KEY`: búsqueda web opcional; sin ella se puede utilizar el buscador de respaldo instalado con las dependencias.

Ollama no necesita clave, pero requiere que el servicio local esté en marcha y que el modelo configurado exista. El selector de proveedor no crea credenciales ni instala modelos por sí solo.

La configuración persistente de la app se guarda en `~/Library/Application Support/TruthLens/config.yaml`. Al primer arranque se copia allí `config.yaml`; tus cambios a ese archivo se mantienen entre versiones. Las claves no se copian a la app compilada.

## Qué puedes configurar desde la interfaz

- **Entrada:** URL o texto pegado.
- **Proveedor IA:** Groq, Cerebras, OpenRouter u Ollama.
- **Artículos:** cuántos seguir cuando el enlace es una portada o sección.
- **Afirmaciones:** máximo de afirmaciones extraídas por contenido.
- **Idioma de fact-checks:** español, inglés o todos.
- **Confianza mínima:** umbral para alertas de afirmaciones refutadas/engañosas.
- **Análisis de estilo:** activar/desactivar sesgos, falacias y posible incitación al odio.

## Privacidad y límites

La extracción del artículo se realiza desde el equipo, pero el texto y las afirmaciones que se comprueban se envían al proveedor de IA elegido. Las búsquedas y la consulta a Google Fact Check también pueden enviar consultas a servicios externos. Revisa sus políticas de datos. Para no enviar el texto a un proveedor de IA alojado, configura Ollama y usa un modelo local; las búsquedas externas seguirán pudiendo enviar consultas fuera del equipo.

Las alertas son una ayuda para la verificación, no una prueba definitiva. En la salida aparecen los razonamientos disponibles y las fuentes usadas.

## Uso por terminal (opcional)

La interfaz no reemplaza los comandos originales:

```bash
python -m truthlens doctor
python -m truthlens check https://medio.es/noticia
python -m truthlens text "El Gobierno prohibirá el efectivo mañana"
```

## Pruebas

```bash
pytest -q
```

La suite principal usa dobles/mocks y está pensada para ejecutarse sin llamar a servicios externos ni cargar modelos.
