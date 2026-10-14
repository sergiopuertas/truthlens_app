from __future__ import annotations

import logging
from typing import Protocol

import httpx

from .config import Config
from .credibility import Credibility, domain_of
from .models import Evidence

log = logging.getLogger(__name__)

FACTCHECK_URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
UA = {"User-Agent": "TruthLens/0.1 (+local research prototype)"}


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, n: int) -> list[dict]: ...  # [{url,title,snippet}]


class SearxngSearch:
    name = "searxng"

    def __init__(self, base_url: str, http: httpx.Client):
        self.base, self.http = base_url.rstrip("/"), http

    def search(self, query, n):
        r = self.http.get(f"{self.base}/search", params={"q": query, "format": "json"})
        r.raise_for_status()
        return [
            {"url": x.get("url", ""), "title": x.get("title", ""), "snippet": x.get("content", "")}
            for x in r.json().get("results", [])[:n]
        ]


class TavilySearch:
    name = "tavily"

    def __init__(self, key: str, http: httpx.Client):
        self.key, self.http = key, http

    def search(self, query, n):
        r = self.http.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {self.key}"},
            json={"query": query, "max_results": n},
        )
        r.raise_for_status()
        return [
            {"url": x.get("url", ""), "title": x.get("title", ""), "snippet": x.get("content", "")}
            for x in r.json().get("results", [])[:n]
        ]


class DdgsSearch:
    name = "ddgs"

    def search(self, query, n):
        from ddgs import DDGS

        return [
            {"url": x.get("href", ""), "title": x.get("title", ""), "snippet": x.get("body", "")}
            for x in DDGS().text(query, max_results=n)
        ]


def make_search(cfg: Config, http: httpx.Client) -> SearchProvider | None:
    s = cfg.search
    p = s.provider
    if p in ("auto", "searxng"):
        try:
            if httpx.get(s.searxng_url, timeout=2).status_code < 500:
                return SearxngSearch(s.searxng_url, http)
        except Exception:
            if p == "searxng":
                log.warning("SearXNG no responde en %s", s.searxng_url)
    if p in ("auto", "tavily"):
        key = cfg.secret(s.tavily_key_env)
        if key:
            return TavilySearch(key, http)
    if p in ("auto", "ddgs"):
        try:
            import ddgs  # noqa: F401

            return DdgsSearch()
        except ImportError:
            pass
    return None


class EvidenceSource(Protocol):
    def gather(self, claim: str) -> list[Evidence]: ...


class Retriever:
    """Reúne evidencia: Google Fact Check (ClaimReview) + búsqueda web + texto de las páginas."""

    def __init__(self, cfg: Config, cred: Credibility, http: httpx.Client | None = None):
        self.cfg, self.cred = cfg, cred
        self.http = http or httpx.Client(timeout=cfg.search.timeout_s, follow_redirects=True, headers=UA)
        self.search = make_search(cfg, self.http)
        self.fc_key = cfg.secret(cfg.factcheck.api_key_env)

    # -- Google Fact Check Tools --------------------------------------------------
    def _google(self, claim: str) -> list[Evidence]:
        fc = self.cfg.factcheck
        params = {"query": claim[:250], "pageSize": fc.page_size, "key": self.fc_key}
        if fc.language:
            params["languageCode"] = fc.language
        r = self.http.get(FACTCHECK_URL, params=params)
        r.raise_for_status()
        out: list[Evidence] = []
        for c in r.json().get("claims", []):
            for rev in c.get("claimReview", []):
                url = rev.get("url", "")
                if not url:
                    continue
                dom = domain_of(url)
                tier = self.cred.tier(dom)
                trust = self.cred.trust(dom) if tier == "low" else max(self.cred.trust(dom), 0.8)
                out.append(
                    Evidence(
                        url=url,
                        title=rev.get("title") or c.get("text", ""),
                        snippet=f"Afirmación revisada: {c.get('text', '')} (por {rev.get('publisher', {}).get('name', dom)})",
                        domain=dom,
                        kind="factcheck",
                        rating=rev.get("textualRating", ""),
                        trust=trust,
                        tier=tier,
                    )
                )
        return out

    # -- Web ----------------------------------------------------------------------
    def _web(self, claim: str) -> list[Evidence]:
        if not self.search:
            return []
        n = self.cfg.search.max_results
        results: list[dict] = []
        for q in (claim, f"{claim} verificación bulo fact check"):
            try:
                results += self.search.search(q[:300], n)
            except Exception as e:
                log.warning("Búsqueda fallida (%s): %s", self.search.name, e)
        seen, out = set(), []
        for x in results:
            url = x["url"]
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            dom = domain_of(url)
            out.append(
                Evidence(url=url, title=x["title"], snippet=x["snippet"], domain=dom,
                         trust=self.cred.trust(dom), tier=self.cred.tier(dom))
            )
        return out

    def _fetch_text(self, url: str) -> str:
        try:
            r = self.http.get(url)
            r.raise_for_status()
            if "html" not in r.headers.get("content-type", ""):
                return ""
            import trafilatura

            text = trafilatura.extract(r.text, include_comments=False, include_tables=False) or ""
            return text[: self.cfg.search.fetch_chars]
        except Exception as e:
            log.debug("No se pudo leer %s: %s", url, e)
            return ""

    def gather(self, claim: str) -> list[Evidence]:
        ev: list[Evidence] = []
        if self.cfg.factcheck.enabled and self.fc_key:
            try:
                ev += self._google(claim)
            except Exception as e:
                log.warning("Google Fact Check falló: %s", e)
        ev += self._web(claim)
        ev.sort(key=lambda e: (e.kind != "factcheck", -e.trust))  # estable: respeta el orden de búsqueda
        ev = ev[: self.cfg.search.max_results]
        fetched = 0
        for e in ev:
            if e.kind == "web" and fetched < self.cfg.search.fetch_top and e.tier != "low":
                text = self._fetch_text(e.url)
                if len(text) > len(e.snippet):
                    e.snippet = text
                fetched += 1
        return ev
