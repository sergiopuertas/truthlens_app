"""Tests sin red ni modelos: LLM, scraper y recuperador se simulan con httpx.MockTransport."""
from __future__ import annotations

import json

import httpx
import pytest

from truthlens.config import Config, LLMCfg, ScrapeCfg, load_config
from truthlens.credibility import Credibility, domain_of
from truthlens.llm import LLMError, OpenAICompatLLM, extract_json
from truthlens.models import Claim, ClaimList, Evidence, NewsItem, StyleReport, Verdict, VectorFinding
from truthlens.pipeline import Pipeline
from truthlens.report import format_report
from truthlens.retrieval import Retriever
from truthlens.risk import RiskAnalyzer, quote_in_text
from truthlens.scraper import ScrapeError, Scraper
from truthlens.verifier import Verifier


# ---------------------------------------------------------------- utilidades
class FakeLLM:
    def __init__(self, responses):
        self.responses = {k: (list(v) if isinstance(v, list) else [v]) for k, v in responses.items()}

    def structured(self, model_cls, system, user, **_):
        lst = self.responses[model_cls]
        return lst.pop(0) if len(lst) > 1 else lst[0]


class FakeRetriever:
    def __init__(self, evidence):
        self.evidence = evidence

    def gather(self, claim):
        return self.evidence


def cred():
    return Credibility(Config().trusted_domains)


def ev(url, kind="web", rating=""):
    c = cred()
    return Evidence(url=url, title="t", snippet="s", domain=domain_of(url), kind=kind, rating=rating,
                    trust=c.trust(url), tier=c.tier(url))


def mock_client(handler, **kw):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True, **kw)


def chat_reply(content: str, status=200, headers=None):
    return httpx.Response(status, json={"choices": [{"message": {"content": content}}]}, headers=headers)


# ---------------------------------------------------------------- config
def test_config_presets_and_yaml(monkeypatch):
    base, model, key = LLMCfg(provider="groq").resolved()
    assert base.startswith("https://api.groq.com") and key == "GROQ_API_KEY" and model
    assert LLMCfg(provider="ollama").resolved()[2] == ""
    with pytest.raises(ValueError):
        LLMCfg(provider="custom").resolved()
    cfg = load_config("config.yaml")
    assert cfg.llm.provider == "groq" and {v.id for v in cfg.risk.style_vectors} >= {"hate", "biased"}
    assert load_config("no-existe.yaml").scrape.max_articles == 5


# ---------------------------------------------------------------- LLM
def test_extract_json_handles_think_and_fences():
    assert json.loads(extract_json('<think>hmm {x}</think>\n```json\n{"a": 1}\n```')) == {"a": 1}
    with pytest.raises(ValueError):
        extract_json("sin json")


def make_llm(handler, monkeypatch, provider="groq", **cfg):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    sleeps: list[float] = []
    llm = OpenAICompatLLM(LLMCfg(provider=provider, **cfg), http=mock_client(handler), sleep=sleeps.append)
    return llm, sleeps


def test_llm_parses_structured_and_sends_auth_and_schema(monkeypatch):
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return chat_reply('<think>x</think>{"claims": [{"text": "A", "check_worthy": true}]}')

    llm, _ = make_llm(handler, monkeypatch)
    out = llm.structured(ClaimList, "sys", "user")
    assert out.claims[0].text == "A" and seen["auth"] == "Bearer k"
    assert seen["body"]["response_format"] == {"type": "json_object"} and "JSON Schema" in seen["body"]["messages"][0]["content"]


def test_llm_retries_on_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(429, headers={"retry-after": "3"}) if calls["n"] < 3 else chat_reply('{"claims": []}')

    llm, sleeps = make_llm(handler, monkeypatch)
    assert llm.structured(ClaimList, "s", "u").claims == []
    assert calls["n"] == 3 and sleeps == [3.0, 3.0]


def test_llm_retries_invalid_json_once_then_fails(monkeypatch):
    llm, _ = make_llm(lambda req: chat_reply("no es json"), monkeypatch)
    with pytest.raises(LLMError):
        llm.structured(ClaimList, "s", "u")


def test_llm_drops_json_mode_when_unsupported(monkeypatch):
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append("response_format" in body)
        if "response_format" in body:
            return httpx.Response(400, text="response_format not supported")
        return chat_reply('{"claims": []}')

    llm, _ = make_llm(handler, monkeypatch)
    llm.structured(ClaimList, "s", "u")
    assert bodies == [True, False]


def test_llm_timeout_and_missing_key(monkeypatch):
    def boom(req):
        raise httpx.ReadTimeout("t")

    llm, sleeps = make_llm(boom, monkeypatch, max_retries=2)
    with pytest.raises(LLMError, match="Timeout"):
        llm.structured(ClaimList, "s", "u")
    assert len(sleeps) == 2
    monkeypatch.delenv("GROQ_API_KEY")
    with pytest.raises(LLMError, match="GROQ_API_KEY"):
        OpenAICompatLLM(LLMCfg(provider="groq"))


def test_ollama_warmup_posts_keep_alive(monkeypatch):
    seen = {}

    def handler(req):
        seen["url"], seen["body"] = str(req.url), json.loads(req.content)
        return httpx.Response(200, json={})

    llm, _ = make_llm(handler, monkeypatch, provider="ollama")
    llm.warmup()
    assert seen["url"] == "http://localhost:11434/api/generate" and seen["body"]["keep_alive"] == "30m"


# ---------------------------------------------------------------- scraper
PARA = "El Ministerio de Sanidad ha publicado este martes un informe con datos sobre la vacunación en España. "
ARTICLE = f"""<html lang="es"><head><title>Sanidad publica el informe de vacunación | El Diario</title>
<meta property="og:site_name" content="El Diario"></head><body><nav><a href="/">Inicio</a></nav>
<article><h1>Sanidad publica el informe anual de vacunación</h1>
{''.join(f'<p>{PARA * 2}Detalle número {i}.</p>' for i in range(6))}</article></body></html>"""


def index_page(n=3):
    links = "".join(f'<h2><a href="/noticias/articulo-{i}">Titular numero {i} de la portada con varias palabras</a></h2>' for i in range(n))
    return f'<html lang="es"><head><title>Portada</title></head><body>{links}<a href="/tag/x">Etiqueta con bastantes palabras para parecer titular</a></body></html>'


def test_scraper_single_article():
    sc = Scraper(ScrapeCfg(), http=mock_client(lambda r: httpx.Response(200, text=ARTICLE, headers={"content-type": "text/html"})))
    (item,) = sc.scrape("https://eldiario.example/n/1")
    assert item.headline == "Sanidad publica el informe anual de vacunación"
    assert item.outlet == "El Diario" and item.language == "es" and len(item.body) >= 500


def test_scraper_index_page_follows_headlines_but_not_tags():
    fetched = []

    def handler(req):
        fetched.append(req.url.path)
        return httpx.Response(200, text=index_page() if req.url.path == "/" else ARTICLE, headers={"content-type": "text/html"})

    items = Scraper(ScrapeCfg(max_articles=2), http=mock_client(handler)).scrape("https://eldiario.example/")
    assert len(items) == 2 and "/tag/x" not in fetched
    assert fetched[1:] == ["/noticias/articulo-0", "/noticias/articulo-1"]


def test_scraper_tweet_via_oembed():
    def handler(req):
        assert req.url.host == "publish.twitter.com" and req.url.params["url"].startswith("https://twitter.com/")
        return httpx.Response(200, json={"author_name": "Ana", "html": '<blockquote><p lang="es">Bulo: el Gobierno prohíbe el efectivo <a href="https://t.co/x">t.co/x</a></p>&mdash; Ana</blockquote>'})

    (item,) = Scraper(ScrapeCfg(), http=mock_client(handler)).scrape("https://x.com/ana/status/123")
    assert item.platform == "X" and item.headline.startswith("Bulo: el Gobierno prohíbe el efectivo") and item.outlet == "Ana"


def test_scraper_bluesky_resolves_handle():
    def handler(req):
        if "resolveHandle" in req.url.path:
            return httpx.Response(200, json={"did": "did:plc:abc"})
        assert req.url.params["uri"] == "at://did:plc:abc/app.bsky.feed.post/3kxyz"
        return httpx.Response(200, json={"thread": {"post": {"record": {"text": "Hola desde Bluesky"}, "author": {"displayName": "Luz", "handle": "luz.bsky.social"}}}})

    (item,) = Scraper(ScrapeCfg(), http=mock_client(handler)).scrape("https://bsky.app/profile/luz.bsky.social/post/3kxyz")
    assert item.platform == "Bluesky" and item.headline == "Hola desde Bluesky" and item.outlet == "Luz"


def test_scraper_login_wall_and_http_errors():
    wall = '<html><head><meta property="og:title" content="Facebook"><meta property="og:description" content="Inicia sesión en Facebook para ver publicaciones"></head></html>'
    with pytest.raises(ScrapeError, match="iniciar sesión"):
        Scraper(ScrapeCfg(), http=mock_client(lambda r: httpx.Response(200, text=wall))).scrape("https://www.facebook.com/x/posts/1")
    with pytest.raises(ScrapeError, match="403"):
        Scraper(ScrapeCfg(), http=mock_client(lambda r: httpx.Response(403))).scrape("https://paywall.example/a")
    with pytest.raises(ScrapeError, match="no se pudo extraer"):
        Scraper(ScrapeCfg(), http=mock_client(lambda r: httpx.Response(200, text="<html><body><div id=app></div></body></html>", headers={"content-type": "text/html"}))).scrape("https://spa.example/")
    with pytest.raises(ScrapeError, match="no es una página web"):
        Scraper(ScrapeCfg(), http=mock_client(lambda r: httpx.Response(200, json={}))).scrape("https://api.example/x")


# ---------------------------------------------------------------- credibilidad
def test_credibility_tiers(tmp_path):
    p = tmp_path / "low.csv"
    p.write_text("domain\nbulos-ya.example\n")
    c = Credibility(Config().trusted_domains, p)
    assert c.tier("https://www.maldita.es/x") == "factchecker" and c.tier("https://a.reuters.com") == "press"
    assert c.tier("https://bulos-ya.example/n") == "low" and c.tier("https://nadie.example") == "unknown"
    assert c.tier("notmaldita.es") == "unknown"


# ---------------------------------------------------------------- verificador
def test_verifier_degrades_untrusted_and_ignores_invented_ids():
    cfg = Config()
    llm = FakeLLM({Verdict: Verdict(label="refuted", confidence=0.95, reasoning="falso", evidence_ids=[1, 9])})
    v, cited = Verifier(llm, cfg).verify("c", [ev("https://nadie.example/a")])
    assert v.label == "unverifiable" and v.evidence_ids == [1] and len(cited) == 1


def test_verifier_keeps_refuted_with_trusted_source_and_no_evidence_shortcut():
    cfg = Config()
    llm = FakeLLM({Verdict: Verdict(label="refuted", confidence=0.9, evidence_ids=[1])})
    v, _ = Verifier(llm, cfg).verify("c", [ev("https://maldita.es/x", kind="factcheck", rating="Falso")])
    assert v.label == "refuted"
    v2, cited = Verifier(FakeLLM({}), cfg).verify("c", [])
    assert v2.label == "unverifiable" and cited == []
    assert Verdict(label="refuted", confidence=85).confidence == pytest.approx(0.85)


# ---------------------------------------------------------------- riesgo
def test_style_flags_require_valid_quote_and_confidence():
    item = NewsItem(headline="Esa gentuza son una plaga y hay que echarlos a todos del país")
    report = StyleReport(findings=[
        VectorFinding(vector_id="hate", present=True, confidence=0.9, quote="son una plaga y hay que echarlos", explanation="Deshumaniza."),
        VectorFinding(vector_id="biased", present=True, confidence=0.9, quote="frase inventada que no existe", explanation="x"),
        VectorFinding(vector_id="fallacious", present=True, confidence=0.3, quote="son una plaga", explanation="x"),
        VectorFinding(vector_id="desconocido", present=True, confidence=0.99, quote="son una plaga", explanation="x"),
    ])
    flags = RiskAnalyzer(FakeLLM({StyleReport: report}), Config()).analyze_style(item)
    assert [f.vector for f in flags] == ["hate"]
    assert quote_in_text("hay que eliminar", "Hay que ELIMINAR esto.")


# ---------------------------------------------------------------- Google Fact Check
def test_google_factcheck_parsing(monkeypatch):
    cfg = Config()
    monkeypatch.setenv(cfg.factcheck.api_key_env, "KEY")
    payload = {"claims": [{"text": "X prohíbe el efectivo", "claimReview": [
        {"url": "https://maldita.es/malditobulo/abc", "title": "No, no se prohíbe el efectivo", "textualRating": "Falso", "publisher": {"name": "Maldita.es"}}]}]}

    def handler(req):
        assert req.url.params["key"] == "KEY" and req.url.params["languageCode"] == "es"
        return httpx.Response(200, json=payload)

    cfg.search.provider = "none"
    out = Retriever(cfg, cred(), http=mock_client(handler)).gather("X prohíbe el efectivo")
    assert len(out) == 1 and out[0].kind == "factcheck" and out[0].rating == "Falso" and out[0].trust >= 0.8


# ---------------------------------------------------------------- pipeline e2e
ARTICLE_ITEM = NewsItem(headline="El Gobierno prohíbe por ley el uso del efectivo desde mañana", outlet="X", url="https://x.example/1")


def build(verdict, evidence, style=None, scraper=None):
    cfg = Config()
    llm = FakeLLM({ClaimList: ClaimList(claims=[Claim(text="El Gobierno prohibirá el efectivo mañana.")]),
                   Verdict: verdict, StyleReport: style or StyleReport()})
    return Pipeline(cfg, llm, FakeRetriever(evidence), RiskAnalyzer(llm, cfg), scraper)


def test_pipeline_flags_trusted_refutation_and_report_shows_sources():
    pipe = build(Verdict(label="refuted", confidence=0.92, reasoning="Maldita lo desmiente.", evidence_ids=[1]),
                 [ev("https://maldita.es/x", kind="factcheck", rating="Falso")])
    rep = pipe.analyze(ARTICLE_ITEM)
    assert rep.flags[0].vector == "false" and rep.flags[0].sources[0].rating == "Falso"
    text = format_report([rep])
    assert "Información falsa" in text and "https://maldita.es/x" in text and "1 con alertas" in text


def test_pipeline_no_flag_when_unverifiable_or_untrusted_source_only():
    rep = build(Verdict(label="unverifiable", confidence=0.4), [ev("https://nadie.example/a")]).analyze(ARTICLE_ITEM)
    assert rep.flags == [] and rep.claims[0].label == "unverifiable"
    rep2 = build(Verdict(label="refuted", confidence=0.95, evidence_ids=[1]), [ev("https://nadie.example/a")]).analyze(ARTICLE_ITEM)
    assert rep2.flags == [] and rep2.claims[0].label == "unverifiable"


def test_pipeline_check_url_isolates_failures():
    class TwoItems:
        def scrape(self, url):
            return [ARTICLE_ITEM, NewsItem(headline="Segundo titular suficientemente largo para analizar")]

    pipe = build(Verdict(label="unverifiable", confidence=0.4), [], scraper=TwoItems())
    calls = {"n": 0}
    orig = pipe.analyze

    def flaky(item):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return orig(item)

    pipe.analyze = flaky
    reps = pipe.check_url("https://x.example")
    assert reps[0].error == "boom" and reps[1].error == "" and "No se pudo analizar" in format_report(reps)
