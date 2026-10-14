from __future__ import annotations

import logging

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
                 scraper: Scraper | None = None):
        self.cfg, self.retriever, self.risk, self.scraper = cfg, retriever, risk, scraper
        self.claims = ClaimExtractor(llm, cfg)
        self.verifier = Verifier(llm, cfg)

    def check_url(self, url: str) -> list[ItemReport]:
        if self.scraper is None:
            raise RuntimeError("Pipeline sin scraper")
        items = self.scraper.scrape(url)
        log.info("%d elemento(s) extraído(s) de %s", len(items), url)
        reports = []
        for item in items:
            try:
                reports.append(self.analyze(item))
            except Exception as e:  # un artículo que falla no tumba al resto
                log.exception("Error analizando '%s'", item.headline[:60])
                reports.append(ItemReport(item=item, error=str(e)))
        return reports

    def analyze(self, item: NewsItem) -> ItemReport:
        log.info("Analizando: %s", item.headline[:90])
        flags: list[Flag] = []
        results: list[ClaimResult] = []
        for claim in self.claims.extract(item):
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
        flags += self.risk.analyze_style(item)
        return ItemReport(item=item, claims=results, flags=flags)
