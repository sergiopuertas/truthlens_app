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
python setup.py py2app

# Las credenciales no se incluyen dentro de la aplicación. La ejecución desde
# Finder no hereda el entorno de la terminal, así que deja una copia local en
# el directorio de datos que la aplicación ya utiliza.
if [[ -f .env ]]; then
  mkdir -p "$HOME/Library/Application Support/TruthLens"
  cp .env "$HOME/Library/Application Support/TruthLens/.env"
  chmod 600 "$HOME/Library/Application Support/TruthLens/.env"
fi

echo
echo "Aplicación compilada: $(pwd)/dist/TruthLens.app"
echo "Versión:"
plutil -p "$(pwd)/dist/TruthLens.app/Contents/Info.plist" | grep -E 'CFBundle(ShortVersionString|Version|Identifier)'
echo "Hash del ejecutable:"
shasum -a 256 "$(pwd)/dist/TruthLens.app/Contents/MacOS/TruthLens"
echo "Puedes copiar TruthLens.app a /Applications y abrirla. Las claves no se incluyen en el paquete."
echo "Para abrir una instancia nueva desde Terminal: open -n \"$(pwd)/dist/TruthLens.app\""
