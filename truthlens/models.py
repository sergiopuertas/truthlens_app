from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


def _norm_conf(v):
    """Acepta 0-1 o porcentajes 0-100 (los LLM pequeños mezclan ambos)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if 1.0 < f <= 100.0:
        f /= 100.0
    return max(0.0, min(1.0, f))


class NewsItem(BaseModel):
    headline: str
    body: str = ""
    outlet: str = ""
    platform: str = ""
    url: str = ""
    language: str = ""

    @property
    def text(self) -> str:
        return f"{self.headline}\n{self.body}".strip()


class Claim(BaseModel):
    text: str
    check_worthy: bool = True


class ClaimList(BaseModel):
    claims: list[Claim] = Field(default_factory=list)


class Evidence(BaseModel):
    url: str
    title: str = ""
    snippet: str = ""
    domain: str = ""
    kind: Literal["factcheck", "web"] = "web"
    rating: str = ""
    trust: float = 0.5
    tier: str = "unknown"


class Verdict(BaseModel):
    label: Literal["supported", "refuted", "misleading", "unverifiable"]
    confidence: float = 0.0
    reasoning: str = ""
    evidence_ids: list[int] = Field(default_factory=list)

    @field_validator("confidence", mode="before")
    @classmethod
    def _fix_conf(cls, v):
        return _norm_conf(v)


class VectorFinding(BaseModel):
    vector_id: str
    present: bool = False
    confidence: float = 0.0
    quote: str = ""
    explanation: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def _fix_conf(cls, v):
        return _norm_conf(v)


class StyleReport(BaseModel):
    findings: list[VectorFinding] = Field(default_factory=list)


class SourceRef(BaseModel):
    url: str
    title: str = ""
    domain: str = ""
    tier: str = "unknown"
    rating: str = ""


class Flag(BaseModel):
    vector: str
    confidence: float
    explanation: str
    claim: str = ""
    quote: str = ""
    sources: list[SourceRef] = Field(default_factory=list)


class ClaimResult(BaseModel):
    claim: str
    label: str
    confidence: float = 0.0
    reasoning: str = ""
    sources: list[SourceRef] = Field(default_factory=list)


class ItemReport(BaseModel):
    item: NewsItem
    claims: list[ClaimResult] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    error: str = ""
