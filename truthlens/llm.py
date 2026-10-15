from __future__ import annotations

import json
import certifi
import logging
import os
import re
import time
from collections.abc import Callable
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel

from .config import LLMCfg

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class StructuredLLM(Protocol):
    def structured(self, model_cls: type[T], system: str, user: str) -> T: ...


def extract_json(text: str) -> str:
    """Quita <think>…</think>, vallas ``` y texto alrededor; devuelve el objeto JSON."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j <= i:
        raise ValueError("la respuesta no contiene un objeto JSON")
    return text[i : j + 1]


class OpenAICompatLLM:
    """Cliente para cualquier API compatible con OpenAI (Groq, Cerebras, OpenRouter, Ollama…).

    Salida estructurada portable: modo JSON + esquema en el prompt + validación con Pydantic.
    """

    def __init__(self, cfg: LLMCfg, http: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep):
        self.cfg = cfg
        self.base, self.model, key_env = cfg.resolved()
        self.key = os.environ.get(key_env, "").strip() if key_env else ""
        if key_env and not self.key:
            raise LLMError(f"Falta la variable {key_env} (ponla en .env). Proveedor: {cfg.provider}")
        self.http = http or httpx.Client(timeout=cfg.timeout_s,verify=certifi.where(),)
        self.sleep = sleep
        self._json_mode = True

    # -- HTTP -----------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}"} if self.key else {}

    def _post(self, payload: dict) -> str:
        retries = self.cfg.max_retries
        for i in range(retries + 1):
            body = dict(payload)
            if not self._json_mode:
                body.pop("response_format", None)
            try:
                r = self.http.post(f"{self.base}/chat/completions", json=body, headers=self._headers())
            except httpx.TimeoutException as e:
                if i == retries:
                    hint = " Si usas Ollama, el modelo puede estar cargándose: prueba un modelo más pequeño." if self.cfg.provider == "ollama" else ""
                    raise LLMError(f"Timeout ({self.cfg.timeout_s}s) llamando a {self.cfg.provider}.{hint}") from e
                self.sleep(2**i)
                continue
            except httpx.HTTPError as e:
                raise LLMError(f"Error de red con {self.cfg.provider}: {e}") from e
            if r.status_code == 400 and self._json_mode and "response_format" in r.text:
                log.warning("El modelo no admite modo JSON; se reintenta sin él")
                self._json_mode = False
                continue
            if r.status_code in (429, 500, 502, 503, 504) and i < retries:
                try:
                    wait = float(r.headers.get("retry-after", ""))
                except ValueError:
                    wait = 2 ** (i + 1)
                wait = min(wait, 30)
                log.warning("%s devolvió %s; reintento en %.0fs", self.cfg.provider, r.status_code, wait)
                self.sleep(wait)
                continue
            if r.status_code >= 400:
                raise LLMError(f"{self.cfg.provider} respondió {r.status_code}: {r.text[:300]}")
            try:
                return r.json()["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, ValueError) as e:
                raise LLMError(f"Respuesta inesperada de {self.cfg.provider}: {r.text[:300]}") from e
        raise LLMError("Reintentos agotados")

    # -- API pública ----------------------------------------------------------------
    def structured(self, model_cls: type[T], system: str, user: str) -> T:
        schema = json.dumps(model_cls.model_json_schema(), ensure_ascii=False)
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": f"{system}\n\nReply with ONE JSON object only (no prose, no markdown) "
                    f"that validates against this JSON Schema:\n{schema}",
                },
                {"role": "user", "content": user},
            ],
        }
        last: Exception | None = None
        for attempt in range(2):
            content = self._post(payload)
            try:
                return model_cls.model_validate_json(extract_json(content))
            except ValueError as e:  # incluye ValidationError de Pydantic
                last = e
                log.warning("JSON inválido del LLM (intento %d): %s", attempt + 1, str(e)[:150])
        raise LLMError(f"El LLM no devolvió JSON válido: {last}")

    def list_models(self) -> list[str]:
        r = self.http.get(f"{self.base}/models", headers=self._headers())
        r.raise_for_status()
        return [m.get("id", "") for m in r.json().get("data", [])]

    def warmup(self) -> None:
        """Solo Ollama: carga el modelo en memoria y lo mantiene `keep_alive`, para que la primera
        consulta real no agote el timeout."""
        if self.cfg.provider != "ollama":
            return
        root = self.base.removesuffix("/v1")
        log.info("Cargando %s en memoria (solo tarda la primera vez)…", self.model)
        try:
            self.http.post(
                f"{root}/api/generate",
                json={"model": self.model, "keep_alive": self.cfg.keep_alive},
                timeout=self.cfg.warmup_timeout_s,
            ).raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"No se pudo cargar el modelo en Ollama ({e}). ¿Está en marcha y hiciste `ollama pull {self.model}`?") from e
