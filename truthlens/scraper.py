from __future__ import annotations

import html as htmllib
import logging
import re
from urllib.parse import urldefrag, urlparse

import httpx
from lxml import html as LH

from .config import ScrapeCfg
from .credibility import domain_of
from .models import NewsItem

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (compatible; TruthLens/0.2; +local research tool)"
X_HOSTS = {"x.com", "twitter.com", "mobile.twitter.com"}
LOGIN_WALL_HOSTS = {"facebook.com", "m.facebook.com", "instagram.com", "tiktok.com", "threads.net", "threads.com"}
PLATFORM_NAMES = {"facebook.com": "Facebook", "m.facebook.com": "Facebook", "instagram.com": "Instagram",
                  "tiktok.com": "TikTok", "threads.net": "Threads", "threads.com": "Threads"}
LOGIN_WALL_RE = re.compile(r"log ?in|sign ?up|iniciar sesi|inicia sesi|reg[ií]strate|create an account", re.I)
SKIP_PATH = re.compile(
    r"/(tag|tags|category|categoria|categorias|author|autor|login|signin|subscribe|suscri\w*|search|buscar|"
    r"about|contact\w*|privacy\w*|cookies?|aviso\w*|rss|feed|newsletter)(/|$)", re.I)


class ScrapeError(RuntimeError):
    pass


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(s or "")).strip()


def _text(el) -> str:
    return _clean(el.text_content())


def _strip_site_suffix(title: str) -> str:
    return re.sub(r"\s+[|\-–—·]\s+[^|\-–—·]{2,40}$", "", title).strip()


def _same_site(a: str, b: str) -> bool:
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def _meta(tree, *names: str) -> str:
    for n in names:
        for attr in ("property", "name"):
            vals = tree.xpath(f"//meta[@{attr}='{n}']/@content")
            if vals and _clean(vals[0]):
                return _clean(vals[0])
    return ""


class Scraper:
    """URL → lista de NewsItem. Artículo suelto, página índice (sigue enlaces) o post de red social."""

    def __init__(self, cfg: ScrapeCfg, http: httpx.Client | None = None):
        self.cfg = cfg
        self.http = http or httpx.Client(timeout=cfg.timeout_s, follow_redirects=True,
                                         headers={"User-Agent": UA, "Accept-Language": "es,en;q=0.8"})

    # -- entrada ----------------------------------------------------------------------
    def scrape(self, url: str) -> list[NewsItem]:
        url = url.strip()
        if "://" not in url:
            url = "https://" + url
        host = domain_of(url)
        try:
            if host in X_HOSTS:
                items = self._tweet(url)
            elif host.endswith("reddit.com"):
                items = self._reddit(url)
            elif host == "bsky.app":
                items = self._bluesky(url)
            elif host == "t.me":
                items = self._telegram(url)
            elif host in LOGIN_WALL_HOSTS:
                items = self._opengraph(url, PLATFORM_NAMES.get(host, host))
            else:
                items = self._web(url) if not self._looks_mastodon(url) else (self._mastodon(url) or self._web(url))
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            why = "requiere login o bloquea el acceso automatizado" if code in (401, 403, 429) else "no se pudo descargar"
            raise ScrapeError(f"{url}: HTTP {code} ({why}).") from e
        except httpx.HTTPError as e:
            raise ScrapeError(f"{url}: error de red ({e}).") from e
        if not items:
            raise ScrapeError(f"{url}: no se pudo extraer texto (¿página que exige JavaScript, login o suscripción?).")
        return items

    def _get(self, url: str, **kw) -> httpx.Response:
        r = self.http.get(url, **kw)
        r.raise_for_status()
        return r

    # -- redes sociales ---------------------------------------------------------------
    def _tweet(self, url: str) -> list[NewsItem]:
        url = re.sub(r"^https?://(www\.|mobile\.)?x\.com", "https://twitter.com", url)
        data = self._get("https://publish.twitter.com/oembed", params={"url": url, "omit_script": "1", "dnt": "true"}).json()
        p = LH.fromstring(data["html"]).find(".//p")
        text = _text(p) if p is not None else ""
        return [NewsItem(headline=text, outlet=data.get("author_name", ""), platform="X", url=url)] if text else []

    def _reddit(self, url: str) -> list[NewsItem]:
        base = url.split("?")[0].rstrip("/")
        try:
            d = self._get(base + "/.json").json()[0]["data"]["children"][0]["data"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            return self._opengraph(url, "Reddit")
        return [NewsItem(headline=_clean(d.get("title")), body=_clean(d.get("selftext"))[: self.cfg.max_body_chars],
                         outlet=f"r/{d.get('subreddit', '')}", platform="Reddit", url=url)]

    def _bluesky(self, url: str) -> list[NewsItem]:
        m = re.search(r"/profile/([^/]+)/post/([^/?#]+)", url)
        if not m:
            return []
        handle, rkey = m.groups()
        api = "https://public.api.bsky.app/xrpc"
        did = handle if handle.startswith("did:") else self._get(f"{api}/com.atproto.identity.resolveHandle", params={"handle": handle}).json()["did"]
        d = self._get(f"{api}/app.bsky.feed.getPostThread", params={"uri": f"at://{did}/app.bsky.feed.post/{rkey}", "depth": 0}).json()
        post = d["thread"]["post"]
        text = _clean(post.get("record", {}).get("text"))
        name = post.get("author", {}).get("displayName") or post.get("author", {}).get("handle", "")
        return [NewsItem(headline=text, outlet=name, platform="Bluesky", url=url)] if text else []

    def _telegram(self, url: str) -> list[NewsItem]:
        m = re.search(r"t\.me/(?:s/)?([^/]+)/(\d+)", url)
        if not m:
            return self._web(url)
        chan, post = m.groups()
        tree = LH.fromstring(self._get(f"https://t.me/{chan}/{post}", params={"embed": "1", "mode": "tme"}).text)
        nodes = tree.xpath("//div[contains(@class,'tgme_widget_message_text')]")
        text = _text(nodes[0]) if nodes else ""
        return [NewsItem(headline=text, outlet=f"t.me/{chan}", platform="Telegram", url=url)] if text else []

    @staticmethod
    def _looks_mastodon(url: str) -> bool:
        return bool(re.match(r"^https?://[^/]+/@[^/]+/\d+/?$", url))

    def _mastodon(self, url: str) -> list[NewsItem]:
        p = urlparse(url)
        sid = p.path.rstrip("/").rsplit("/", 1)[-1]
        try:
            d = self._get(f"{p.scheme}://{p.netloc}/api/v1/statuses/{sid}").json()
        except (httpx.HTTPError, ValueError):
            return []
        text = _text(LH.fromstring(f"<div>{d.get('content', '')}</div>"))
        name = d.get("account", {}).get("display_name") or d.get("account", {}).get("acct", "")
        return [NewsItem(headline=text, outlet=name, platform="Mastodon", url=url)] if text else []

    def _opengraph(self, url: str, platform: str) -> list[NewsItem]:
        """Plataformas con muro de login: solo se obtiene lo que exponen las metaetiquetas públicas."""
        tree = LH.fromstring(self._get(url).text)
        title = _meta(tree, "og:title", "twitter:title")
        desc = _meta(tree, "og:description", "twitter:description", "description")
        text = desc or title
        if len(text) < 20 or LOGIN_WALL_RE.search(title + " " + desc):
            raise ScrapeError(f"{platform} exige iniciar sesión para ver este contenido; copia el texto del post y usa `truthlens text \"…\"`.")
        return [NewsItem(headline=text, outlet=_meta(tree, "og:site_name") or platform, platform=platform, url=url)]

    # -- web genérica -----------------------------------------------------------------
    def _web(self, url: str) -> list[NewsItem]:
        r = self._get(url)
        if "html" not in r.headers.get("content-type", "html"):
            raise ScrapeError(f"{url}: el contenido no es una página web ({r.headers.get('content-type')}).")
        art = self._article(r.text, str(r.url))
        if art and len(art.body) >= self.cfg.min_article_chars:
            return [art]
        # Página índice (portada, sección): seguir los titulares
        items: list[NewsItem] = []
        for link in self._links(LH.fromstring(r.text), str(r.url))[: self.cfg.max_articles * 2]:
            if len(items) >= self.cfg.max_articles:
                break
            try:
                sub = self._article(self._get(link).text, link)
            except httpx.HTTPError as e:
                log.debug("Saltado %s: %s", link, e)
                continue
            if sub and len(sub.body) >= self.cfg.min_article_chars:
                items.append(sub)
        return items or ([art] if art else [])

    def _article(self, html_text: str, url: str) -> NewsItem | None:
        import trafilatura

        text = (trafilatura.extract(html_text, url=url, include_comments=False, include_tables=False) or "").strip()
        if not text:
            return None
        tree = LH.fromstring(html_text)
        h1 = [t for t in (_text(h) for h in tree.xpath("//h1")) if len(t) >= 15]
        title = h1[0] if h1 else _strip_site_suffix(_meta(tree, "og:title") or _clean("".join(tree.xpath("//title/text()"))))
        if not title:
            return None
        return NewsItem(
            headline=title, body=text[: self.cfg.max_body_chars],
            outlet=_meta(tree, "og:site_name") or domain_of(url), platform="web", url=url,
            language=(tree.get("lang") or "")[:2],
        )

    def _links(self, tree, base_url: str) -> list[str]:
        tree.make_links_absolute(base_url)
        base_dom = domain_of(base_url)
        seen = {urldefrag(base_url)[0].rstrip("/")}
        out: list[str] = []
        anchors = tree.xpath("//h1//a[@href]|//h2//a[@href]|//h3//a[@href]|//article//a[@href]") + tree.xpath("//a[@href]")
        for a in anchors:
            href = urldefrag(a.get("href", ""))[0]
            p = urlparse(href)
            words = _text(a).split()
            if (p.scheme not in ("http", "https") or not _same_site(domain_of(href), base_dom)
                    or not p.path.strip("/") or SKIP_PATH.search(p.path)
                    or len(words) < 5 or not 25 <= len(" ".join(words)) <= 250):
                continue
            key = href.rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            out.append(href)
        return out
