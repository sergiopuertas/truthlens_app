from __future__ import annotations

from .config import Config
from .llm import StructuredLLM
from .models import Claim, ClaimList, NewsItem

SYSTEM = """You are a fact-checking assistant. Extract the factual claims in the text that a fact-checker could verify.
Rules:
- Each claim is ONE self-contained sentence in the same language as the text: resolve pronouns and include who, what, when, where and numbers.
- Exclude opinions, predictions, rhetorical questions, jokes and trivial or private matters.
- check_worthy=true only if the claim concerns a matter of public interest and can be checked against public sources.
- Return at most {n} claims, most important first. If there are none, return an empty list."""


class ClaimExtractor:
    def __init__(self, llm: StructuredLLM, cfg: Config):
        self.llm, self.cfg = llm, cfg

    def extract(self, item: NewsItem) -> list[Claim]:
        n = self.cfg.risk.max_claims_per_item
        out = self.llm.structured(ClaimList, SYSTEM.format(n=n), f"Text:\n{item.text[:2500]}")
        return [c for c in out.claims if c.check_worthy and c.text.strip()][:n]
