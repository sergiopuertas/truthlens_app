"""Configuración de py2app para crear TruthLens.app en macOS."""
from pathlib import Path
import sysconfig

from setuptools import setup

ROOT = Path(__file__).resolve().parent
SITE_PACKAGES = Path(sysconfig.get_paths()["purelib"])
WEB_UI = ROOT / "truthlens" / "web_ui"
if not (WEB_UI / "index.html").is_file():
    raise SystemExit(f"No se encuentra la interfaz web: {WEB_UI / 'index.html'}")

# Estos metadatos los consultan algunas dependencias en tiempo de ejecución.
PACKAGE_METADATA = [
    str(path)
    for name in ("trafilatura", "courlan", "htmldate", "justext", "ddgs", "pydantic")
    for path in SITE_PACKAGES.glob(f"{name}-*.dist-info")
]

PACKAGES = [
    "truthlens",
    "AppKit",
    "Foundation",
    "WebKit",
    "objc",
    "httpx",
    "httpcore",
    "h11",
    "idna",
    "certifi",
    "anyio",
    "typing_extensions",
    "typing_inspection",
    "annotated_types",
    "charset_normalizer",
    "urllib3",
    "pydantic",
    "pydantic_core",
    "yaml",
    "lxml",
    "lxml_html_clean",
    "trafilatura",
    "courlan",
    "htmldate",
    "justext",
    "dotenv",
    "ddgs",
    "primp",
]

OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "TruthLens",
        "CFBundleDisplayName": "TruthLens",
        "CFBundleIdentifier": "es.truthlens.desktop",
        "CFBundleShortVersionString": "0.3.0",
        "CFBundleVersion": "0.3.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    },
    "packages": PACKAGES,
    "includes": [
        "AppKit", "Foundation", "WebKit", "objc",
        "httpx._transports.default", "pydantic_core._pydantic_core",
        "lxml.etree", "lxml.html", "lxml_html_clean",
    ],
    # py2app copia esta carpeta a Contents/Resources; desktop.py contempla
    # tanto Resources/web_ui como Resources/truthlens/web_ui al localizarla.
    "resources": ["config.yaml", "truthlens/web_ui", *PACKAGE_METADATA, str(SITE_PACKAGES / "PyObjCTools")],
    "optimize": 1,
}

setup(
    name="TruthLens",
    version="0.3.0",
    app=["TruthLens.py"],
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
    zip_safe=False,
)
