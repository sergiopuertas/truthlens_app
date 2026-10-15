"""Configuración de py2app: construir TruthLens.app en macOS con ./build_macos.sh."""
import sysconfig
from pathlib import Path

from setuptools import setup

SITE_PACKAGES = Path(sysconfig.get_paths()["purelib"])
PACKAGE_METADATA = [
    str(path)
    for name in ("trafilatura", "courlan", "htmldate", "justext")
    for path in SITE_PACKAGES.glob(f"{name}-*.dist-info")
]

OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "TruthLens",
        "CFBundleDisplayName": "TruthLens",
        "CFBundleIdentifier": "es.truthlens.desktop",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    },
    "packages": [
        "truthlens",
        "AppKit",
        "Foundation",
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
    ],
    "includes": ["AppKit", "Foundation", "objc"],
    "resources": ["config.yaml", *PACKAGE_METADATA, str(SITE_PACKAGES / "PyObjCTools")],
}

setup(
    name="TruthLens",
    version="0.2.5",
    app=["TruthLens.py"],
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
