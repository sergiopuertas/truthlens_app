from __future__ import annotations

import csv
from pathlib import Path
from urllib.parse import urlparse

TIER_TRUST = {"factchecker": 1.0, "reference": 0.85, "press": 0.7, "unknown": 0.5, "low": 0.2}


def domain_of(url_or_domain: str) -> str:
    s = url_or_domain.strip().lower()
    host = urlparse(s if "://" in s else f"//{s}").hostname or ""
    return host.removeprefix("www.")


def _match(domain: str, listed: str) -> bool:
    return domain == listed or domain.endswith("." + listed)


class Credibility:
    """Confianza por dominio. Las listas negativas (Iffy/CRED-1) solo bajan la confianza;
    un dominio ausente es 'desconocido', no 'fiable'."""

    def __init__(self, trusted: dict[str, list[str]], low_csv: str | Path | None = None):
        self.trusted = {tier: [d.lower() for d in doms] for tier, doms in trusted.items()}
        self.low: set[str] = set()
        if low_csv and Path(low_csv).is_file():
            with open(low_csv, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    d = domain_of(row.get("domain", "") or "")
                    if d:
                        self.low.add(d)

    def tier(self, url_or_domain: str) -> str:
        d = domain_of(url_or_domain)
        if not d:
            return "unknown"
        for tier in ("factchecker", "reference", "press"):
            if any(_match(d, x) for x in self.trusted.get(tier, [])):
                return tier
        if any(_match(d, x) for x in self.low):
            return "low"
        return "unknown"

    def trust(self, url_or_domain: str) -> float:
        return TIER_TRUST.get(self.tier(url_or_domain), 0.5)
