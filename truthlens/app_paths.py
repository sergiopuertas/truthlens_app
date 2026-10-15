"""Ubicaciones de configuración y secretos para ejecución desde código o desde .app."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

APP_NAME = "TruthLens"


def user_data_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def resource_path(filename: str) -> Path | None:
    """Busca recursos tanto en el checkout como dentro de un bundle de py2app."""
    here = Path(__file__).resolve().parent.parent
    executable = Path(sys.executable).resolve()
    candidates = [
        here / filename,
        executable.parent.parent / "Resources" / filename,
        executable.parent / "Resources" / filename,
        Path.cwd() / filename,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def prepare_desktop_environment() -> tuple[Path, Path]:
    """Devuelve (directorio de usuario, config.yaml), cargando .env sin mostrar secretos."""
    data_dir = user_data_dir()
    config_target = data_dir / "config.yaml"
    if not config_target.exists():
        bundled_config = resource_path("config.yaml")
        if bundled_config:
            shutil.copy2(bundled_config, config_target)

    example_target = data_dir / ".env.example"
    if not example_target.exists():
        bundled_example = resource_path(".env.example")
        if bundled_example:
            shutil.copy2(bundled_example, example_target)

    env_candidates = [
        Path(os.environ["TRUTHLENS_ENV_FILE"]).expanduser() if os.environ.get("TRUTHLENS_ENV_FILE") else None,
        Path(__file__).resolve().parent.parent / ".env",
        data_dir / ".env",
    ]
    for env_path in env_candidates:
        if env_path and env_path.is_file():
            load_dotenv(dotenv_path=env_path, override=False)
            break

    if not config_target.exists():
        config_target.touch()
    return data_dir, config_target
