from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher

from .config import Config
from .llm import StructuredLLM
from .models import Flag, NewsItem, StyleReport

log = logging.getLogger(__name__)

SYSTEM = """You are a media-literacy analyst. Assess the text against the listed risk vectors.
Vectors (id: definition):
{vectors}
Rules:
- Judge the TEXT, not the topic or the side it takes. Criticism of ideas, parties or politicians is not hate.
- Report a vector as present ONLY if it is clear and unambiguous; otherwise present=false.
- For every present vector give an EXACT quote copied from the text (at most 25 words) and a one-sentence explanation in the language of the text.
- confidence: a number from 0 to 1. Return one finding per vector id."""


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s)).strip()


def quote_in_text(quote: str, text: str, min_ratio: float = 0.8) -> bool:
    """La cita debe aparecer (casi) literalmente en el texto: evita explicaciones inventadas."""
    q, t = _norm(quote), _norm(text)
    if not q or not t:
        return False
    if q in t:
        return True
    m = SequenceMatcher(None, q, t, autojunk=False).find_longest_match(0, len(q), 0, len(t))
    return m.size / len(q) >= min_ratio


class RiskAnalyzer:
    def __init__(self, llm: StructuredLLM, cfg: Config):
        self.llm, self.cfg = llm, cfg
        self.vectors = {v.id: v for v in cfg.risk.style_vectors}

    def analyze_style(self, item: NewsItem) -> list[Flag]:
        if not self.vectors:
            return []
        vec_txt = "\n".join(f"- {v.id}: {v.description}" for v in self.vectors.values())
        report = self.llm.structured(StyleReport, SYSTEM.format(vectors=vec_txt), f"Text:\n{item.text[:3000]}")
        flags: list[Flag] = []
        for f in report.findings:
            v = self.vectors.get(f.vector_id)
            if not v or not f.present or f.confidence < v.min_confidence:
                continue
            if not quote_in_text(f.quote, item.text):
                log.info("Descartado '%s': la cita no aparece en el texto", f.vector_id)
                continue
            flags.append(Flag(vector=f.vector_id, confidence=f.confidence, explanation=f.explanation, quote=f.quote))
        return flags
