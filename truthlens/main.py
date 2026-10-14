from __future__ import annotations

import argparse
import json
import logging
import sys

import httpx
from dotenv import load_dotenv

from .config import Config, load_config
from .credibility import Credibility
from .llm import LLMError, OpenAICompatLLM
from .models import NewsItem
from .pipeline import Pipeline
from .report import format_report
from .retrieval import Retriever, make_search
from .risk import RiskAnalyzer
from .scraper import ScrapeError, Scraper


def build_pipeline(cfg: Config, warmup: bool = True) -> Pipeline:
    llm = OpenAICompatLLM(cfg.llm)
    if warmup:
        llm.warmup()  # solo hace algo con Ollama
    cred = Credibility(cfg.trusted_domains, cfg.low_credibility_csv)
    return Pipeline(cfg, llm, Retriever(cfg, cred), RiskAnalyzer(llm, cfg), Scraper(cfg.scrape))


def run_doctor(cfg: Config) -> None:
    def line(ok: bool | None, msg: str) -> None:
        print(("✔ " if ok else "✘ " if ok is False else "• ") + msg)

    try:
        llm = OpenAICompatLLM(cfg.llm)
        line(True, f"LLM: {cfg.llm.provider} · modelo {llm.model}")
        try:
            models = llm.list_models()
            ok = llm.model in models
            line(ok, f"El modelo '{llm.model}' " + ("existe" if ok else f"NO aparece en el proveedor. Algunos disponibles: {', '.join(models[:8])}"))
        except Exception as e:
            line(None, f"No se pudo listar modelos ({e})")
    except Exception as e:
        line(False, str(e))
    line(bool(cfg.secret(cfg.factcheck.api_key_env)), f"Clave {cfg.factcheck.api_key_env} (Google Fact Check)")
    s = make_search(cfg, httpx.Client(timeout=5))
    line(s is not None, f"Búsqueda web: {s.name if s else 'ninguna (SearXNG, TAVILY_API_KEY o pip install ddgs)'}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0].startswith(("http://", "https://", "www.")):
        argv.insert(0, "check")  # `python -m truthlens https://…` equivale a `check`

    ap = argparse.ArgumentParser(prog="truthlens", description="Verifica noticias/posts a partir de una URL")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_check = sub.add_parser("check", help="analizar una URL (web o post de red social)")
    p_check.add_argument("url")
    p_check.add_argument("--json", action="store_true", help="salida en JSON")
    p_check.add_argument("--max-articles", type=int, help="en páginas índice, nº de artículos a seguir")
    p_txt = sub.add_parser("text", help="analizar un texto pegado")
    p_txt.add_argument("text")
    sub.add_parser("doctor", help="comprobar configuración y claves")
    args = ap.parse_args(argv)

    load_dotenv()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = load_config(args.config)

    if args.cmd == "doctor":
        run_doctor(cfg)
        return 0
    try:
        if args.cmd == "check":
            if args.max_articles:
                cfg.scrape.max_articles = args.max_articles
            reports = build_pipeline(cfg).check_url(args.url)
        else:
            pipe = build_pipeline(cfg)
            reports = [pipe.analyze(NewsItem(headline=args.text))]
    except (ScrapeError, LLMError) as e:
        print(f"✘ {e}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps([r.model_dump() for r in reports], ensure_ascii=False, indent=2))
    else:
        print(format_report(reports))
    return 0
