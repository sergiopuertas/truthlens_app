from __future__ import annotations

from datetime import date

from .config import Config
from .llm import StructuredLLM
from .models import Evidence, Verdict

SYSTEM = """You are a rigorous fact-checker. Judge ONE claim using ONLY the numbered evidence provided. Today is {today}.
Labels:
- supported: reliable evidence confirms the claim.
- refuted: reliable evidence shows the claim is false.
- misleading: the claim is partly true but lacks context, exaggerates or is presented deceptively.
- unverifiable: evidence is missing, off-topic, contradictory, or only from weak sources. This is the DEFAULT when unsure.
Rules:
- A fact-check entry counts only if it addresses the SAME claim, not merely the same topic.
- Do not use prior knowledge to fill gaps. Never cite an id that is not in the list.
- evidence_ids: the ids that directly support your label. Prefer trusted tiers (factchecker > reference > press).
- reasoning: at most 2 sentences, in the language of the claim.
- confidence: a number from 0 to 1."""


def format_evidence(evs: list[Evidence]) -> str:
    lines = []
    for i, e in enumerate(evs, 1):
        rating = f' · rating="{e.rating}"' if e.rating else ""
        lines.append(f"[{i}] ({e.kind} · {e.domain} · tier={e.tier}{rating}) {e.title}\n{e.snippet[:700]}")
    return "\n\n".join(lines)


class Verifier:
    def __init__(self, llm: StructuredLLM, cfg: Config):
        self.llm, self.cfg = llm, cfg

    def verify(self, claim: str, evidence: list[Evidence]) -> tuple[Verdict, list[Evidence]]:
        if not evidence:
            return Verdict(label="unverifiable", confidence=0.0, reasoning="No se encontró evidencia."), []
        user = f"Claim: {claim}\n\nEvidence:\n{format_evidence(evidence)}"
        v = self.llm.structured(Verdict, SYSTEM.format(today=date.today().isoformat()), user)
        v.evidence_ids = sorted({i for i in v.evidence_ids if 1 <= i <= len(evidence)})
        cited = [evidence[i - 1] for i in v.evidence_ids]
        r = self.cfg.risk
        if v.label in ("refuted", "misleading"):
            trusted = [e for e in cited if e.trust >= r.trusted_trust_level]
            if len(trusted) < r.min_trusted_sources:
                v = Verdict(
                    label="unverifiable",
                    confidence=min(v.confidence, 0.5),
                    reasoning=f"{v.reasoning} (Degradado: sin fuentes de confianza que lo respalden.)".strip(),
                    evidence_ids=v.evidence_ids,
                )
        return v, cited
