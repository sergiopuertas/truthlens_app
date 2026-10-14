from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

# provider -> (base_url, modelo por defecto, variable de entorno con la clave)
# Los ids de modelo cambian a menudo: `python -m truthlens doctor` comprueba que existe.
PRESETS: dict[str, tuple[str, str, str]] = {
    "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-20b", "GROQ_API_KEY"),
    "cerebras": ("https://api.cerebras.ai/v1", "llama-3.3-70b", "CEREBRAS_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free", "OPENROUTER_API_KEY"),
    "ollama": ("http://localhost:11434/v1", "qwen3:4b", ""),
}


class LLMCfg(BaseModel):
    provider: str = "groq"      # groq | cerebras | openrouter | ollama | custom
    model: str = ""             # vacío = el del preset
    base_url: str = ""          # vacío = el del preset (obligatorio con provider: custom)
    api_key_env: str = ""       # vacío = el del preset
    timeout_s: float = 60
    max_retries: int = 3
    keep_alive: str = "30m"     # solo Ollama: mantiene el modelo cargado en memoria
    warmup_timeout_s: float = 300

    def resolved(self) -> tuple[str, str, str]:
        base, model, key_env = PRESETS.get(self.provider, ("", "", ""))
        base, model, key_env = self.base_url or base, self.model or model, self.api_key_env or key_env
        if not base or not model:
            raise ValueError(f"llm.provider '{self.provider}': define llm.base_url y llm.model en config.yaml")
        return base.rstrip("/"), model, key_env


class ScrapeCfg(BaseModel):
    timeout_s: float = 20
    max_articles: int = 5          # en páginas índice (portadas, secciones), cuántos artículos seguir
    min_article_chars: int = 500   # menos texto que esto → se trata como página índice
    max_body_chars: int = 3000


class SearchCfg(BaseModel):
    provider: str = "auto"
    searxng_url: str = "http://localhost:8080"
    tavily_key_env: str = "TAVILY_API_KEY"
    max_results: int = 8
    fetch_top: int = 3
    fetch_chars: int = 2500
    timeout_s: float = 15


class FactCheckCfg(BaseModel):
    enabled: bool = True
    api_key_env: str = "GOOGLE_FACTCHECK_API_KEY"
    language: str = "es"
    page_size: int = 5


class StyleVector(BaseModel):
    id: str
    description: str
    min_confidence: float = 0.75


def _default_vectors() -> list[StyleVector]:
    return [
        StyleVector(id="biased", description="Lenguaje cargado, encuadre unilateral u omisión evidente de contexto relevante.", min_confidence=0.75),
        StyleVector(id="fallacious", description="Falacias lógicas claras (ad hominem, falso dilema, hombre de paja, generalización apresurada...).", min_confidence=0.75),
        StyleVector(id="hate", description="Deshumaniza o incita a la hostilidad/violencia contra personas por una característica protegida. La crítica a ideas o políticos NO cuenta.", min_confidence=0.8),
    ]


class RiskCfg(BaseModel):
    verdict_min_confidence: float = 0.7
    min_trusted_sources: int = 1
    trusted_trust_level: float = 0.7
    max_claims_per_item: int = 2
    style_vectors: list[StyleVector] = Field(default_factory=_default_vectors)


DEFAULT_TRUSTED: dict[str, list[str]] = {
    "factchecker": [
        "maldita.es", "newtral.es", "verifica.efe.com", "factcheck.afp.com", "snopes.com",
        "politifact.com", "fullfact.org", "factcheck.org", "edmo.eu", "euvsdisinfo.eu", "eufactcheck.eu",
    ],
    "reference": [
        "who.int", "europa.eu", "un.org", "ine.es", "boe.es", "aemet.es", "sanidad.gob.es",
        "nasa.gov", "cdc.gov", "nih.gov", "worldbank.org", "imf.org", "oecd.org",
    ],
    "press": [
        "reuters.com", "apnews.com", "efe.com", "bbc.com", "elpais.com", "rtve.es", "europapress.es",
        "lavanguardia.com", "elmundo.es", "abc.es", "theguardian.com", "nytimes.com", "france24.com", "dw.com",
    ],
}


class Config(BaseModel):
    llm: LLMCfg = Field(default_factory=LLMCfg)
    scrape: ScrapeCfg = Field(default_factory=ScrapeCfg)
    search: SearchCfg = Field(default_factory=SearchCfg)
    factcheck: FactCheckCfg = Field(default_factory=FactCheckCfg)
    risk: RiskCfg = Field(default_factory=RiskCfg)
    low_credibility_csv: str = "data/low_credibility.csv"
    trusted_domains: dict[str, list[str]] = Field(default_factory=lambda: dict(DEFAULT_TRUSTED))

    def secret(self, env_name: str) -> str:
        return os.environ.get(env_name, "").strip()


def load_config(path: str | Path | None = "config.yaml") -> Config:
    """Carga config.yaml; si no existe, usa los valores por defecto. Claves desconocidas se ignoran."""
    if path and Path(path).is_file():
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return Config.model_validate(data)
    return Config()
