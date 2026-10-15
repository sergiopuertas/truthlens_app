from __future__ import annotations

import logging
from collections.abc import Callable

from .claims import ClaimExtractor
from .config import Config
from .llm import StructuredLLM
from .models import ClaimResult, Evidence, Flag, ItemReport, NewsItem, SourceRef
from .retrieval import EvidenceSource
from .risk import RiskAnalyzer
from .scraper import Scraper
from .verifier import Verifier

log = logging.getLogger(__name__)


def _refs(evs: list[Evidence]) -> list[SourceRef]:
    return [SourceRef(url=e.url, title=e.title, domain=e.domain, tier=e.tier, rating=e.rating) for e in evs]


class Pipeline:
    def __init__(self, cfg: Config, llm: StructuredLLM, retriever: EvidenceSource, risk: RiskAnalyzer,
                 scraper: Scraper | None = None,
                 progress_callback: Callable[[int, str], None] | None = None):
        self.cfg, self.retriever, self.risk, self.scraper = cfg, retriever, risk, scraper
        self.progress_callback = progress_callback
        self.claims = ClaimExtractor(llm, cfg)
        self.verifier = Verifier(llm, cfg)

    def _progress(self, percent: float, message: str) -> None:
        if self.progress_callback:
            self.progress_callback(max(0, min(100, int(round(percent)))), message)

    def check_url(self, url: str) -> list[ItemReport]:
        if self.scraper is None:
            raise RuntimeError("Pipeline sin scraper")
        self._progress(5, "Conectando y extrayendo contenido…")
        items = self.scraper.scrape(url)
        log.info("%d elemento(s) extraído(s) de %s", len(items), url)
        self._progress(20, f"Contenido extraído: {len(items)} elemento(s)")
        reports = []
        for idx, item in enumerate(items):
            base = 20 + 76 * idx / max(1, len(items))
            span = 76 / max(1, len(items))
            def item_progress(percent: int, message: str, *, _base=base, _span=span) -> None:
                self._progress(_base + _span * percent / 100, message)
            try:
                self._current_item_progress = item_progress
                reports.append(self.analyze(item))
            except Exception as e:  # un artículo que falla no tumba al resto
                log.exception("Error analizando '%s'", item.headline[:60])
                reports.append(ItemReport(item=item, error=str(e)))
            finally:
                self._current_item_progress = None
        self._progress(100, "Análisis completado")
        return reports

    def analyze(self, item: NewsItem,
                progress_callback: Callable[[int, str], None] | None = None) -> ItemReport:
        emit = progress_callback or getattr(self, "_current_item_progress", None) or self._progress
        log.info("Analizando: %s", item.headline[:90])
        flags: list[Flag] = []
        results: list[ClaimResult] = []
        emit(5, "Extrayendo afirmaciones verificables…")
        claims = self.claims.extract(item)
        emit(24, f"{len(claims)} afirmación(es) detectada(s)")
        total = max(1, len(claims))
        for idx, claim in enumerate(claims):
            start = 24 + 56 * idx / total
            emit(round(start), "Buscando fuentes y contrastando afirmaciones…")
            evidence = self.retriever.gather(claim.text)
            verdict, cited = self.verifier.verify(claim.text, evidence)
            log.info("  [%s %.2f] %s", verdict.label, verdict.confidence, claim.text[:80])
            results.append(ClaimResult(claim=claim.text, label=verdict.label, confidence=verdict.confidence,
                                       reasoning=verdict.reasoning, sources=_refs(cited)))
            if verdict.label in ("refuted", "misleading") and verdict.confidence >= self.cfg.risk.verdict_min_confidence:
                flags.append(Flag(
                    vector="false" if verdict.label == "refuted" else "misleading",
                    confidence=verdict.confidence, explanation=verdict.reasoning, claim=claim.text,
                    sources=_refs([e for e in cited if e.trust >= self.cfg.risk.trusted_trust_level]),
                ))
            emit(round(24 + 56 * (idx + 1) / total), "Afirmación contrastada")
        emit(88, "Analizando sesgos, falacias y posibles mensajes de odio…")
        flags += self.risk.analyze_style(item)
        emit(97, "Preparando el informe…")
        return ItemReport(item=item, claims=results, flags=flags)
