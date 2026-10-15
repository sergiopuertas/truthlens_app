#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "No se encuentra $PYTHON_BIN. Instala Python 3.11 o 3.12 y vuelve a intentarlo." >&2
  exit 1
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Este script debe ejecutarse en macOS; py2app no puede compilar una app macOS desde este sistema." >&2
  exit 2
fi

"$PYTHON_BIN" -m venv .venv-desktop
# shellcheck disable=SC1091
source .venv-desktop/bin/activate

python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python -m pip install py2app

rm -rf build dist
python -m compileall -q truthlens TruthLens.py
python setup.py py2app

APP_PATH="$(pwd)/dist/TruthLens.app"
PLIST_PATH="$APP_PATH/Contents/Info.plist"
RESOURCES_PATH="$APP_PATH/Contents/Resources"

if [[ ! -d "$APP_PATH" ]]; then
  echo "No se ha generado $APP_PATH." >&2
  exit 3
fi

UI_FILE="$(find "$RESOURCES_PATH" -type f -path '*/web_ui/index.html' -print -quit)"
if [[ -z "$UI_FILE" ]]; then
  echo "La compilación no ha incluido truthlens/web_ui/index.html en el bundle." >&2
  echo "Comprueba la opción 'resources' de setup.py y vuelve a compilar." >&2
  exit 4
fi

echo
echo "Aplicación compilada: $APP_PATH"
echo "Interfaz incluida: $UI_FILE"
echo "Versión:"
plutil -p "$PLIST_PATH" | grep -E 'CFBundle(ShortVersionString|Version|Identifier)'
echo "Hash del ejecutable:"
shasum -a 256 "$APP_PATH/Contents/MacOS/TruthLens"

# Las credenciales no se incluyen en el bundle. Si hay un .env en el checkout,
# se copia a Application Support para que la app lanzada desde Finder lo encuentre.
if [[ -f .env ]]; then
  mkdir -p "$HOME/Library/Application Support/TruthLens"
  cp .env "$HOME/Library/Application Support/TruthLens/.env"
  chmod 600 "$HOME/Library/Application Support/TruthLens/.env"
fi

echo
echo "Puedes copiar TruthLens.app a /Applications y abrirla desde Finder."
echo "Si no arranca, revisa ~/Library/Application Support/TruthLens/desktop.log."
echo "Para probar el ejecutable directamente: open -n \"$APP_PATH\""
