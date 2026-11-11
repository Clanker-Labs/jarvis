"""web_search and read_url.

read_url is model-driven and web content can carry prompt injections, so it refuses loopback,
private, link-local, CGNAT/Tailscale and other non-public addresses (checked on every redirect hop)
unless `tools.url_allow_private` is on.
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
import logging
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from pydantic import BaseModel, Field

from ...secrets import SecretStore
from ..registry import Tool, ToolContext

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (X11; Linux aarch64) JarvisAssistant/0.1"
MAX_BYTES = 3_000_000
MAX_CHARS = 12_000
CGNAT = ipaddress.ip_network("100.64.0.0/10")


class SearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    count: int = Field(5, ge=1, le=10)


class UrlArgs(BaseModel):
    url: str = Field(min_length=8, max_length=2000, description="http(s) URL to read.")


class BlockedURL(ValueError):
    pass


def ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip in CGNAT
    )


async def check_url(url: str, allow_private: bool) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise BlockedURL("only http(s) URLs are allowed")
    if allow_private:
        return
    host = p.hostname
    try:
        ips = [ipaddress.ip_address(host)]
    except ValueError:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, p.port or 443, type=socket.SOCK_STREAM
        )
        ips = [ipaddress.ip_address(i[4][0]) for i in infos]
    if not ips or not all(ip_is_public(ip) for ip in ips):
        raise BlockedURL(f"{host} resolves to a private address; refusing to fetch")


def extract_text(content: bytes, content_type: str, url: str) -> tuple[str, str]:
    """→ (title, text)."""
    ct = content_type.split(";")[0].strip().lower()
    if ct == "application/pdf" or url.lower().endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:40])
        return (reader.metadata.title if reader.metadata and reader.metadata.title else ""), text
    if ct.startswith("text/plain") or ct in ("application/json", "text/markdown"):
        return "", content.decode(errors="replace")
    import trafilatura

    html = content.decode(errors="replace")
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True) or ""
    meta = trafilatura.extract_metadata(html)
    return (meta.title if meta and meta.title else ""), text


async def fetch(url: str, allow_private: bool, *, max_redirects: int = 5) -> tuple[str, bytes, str]:
    async with httpx.AsyncClient(
        timeout=15, follow_redirects=False, headers={"User-Agent": UA}
    ) as c:
        for _ in range(max_redirects + 1):
            await check_url(url, allow_private)
            async with c.stream("GET", url) as r:
                if r.is_redirect:
                    url = urljoin(url, r.headers.get("location", ""))
                    continue
                r.raise_for_status()
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > MAX_BYTES:
                        break
                return url, bytes(buf), r.headers.get("content-type", "")
    raise BlockedURL("too many redirects")


# --------------------------------------------------------------------------- search backends


async def _brave(c: httpx.AsyncClient, q: str, n: int, key: str) -> list[dict[str, str]]:
    r = await c.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": q, "count": n},
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
    )
    r.raise_for_status()
    return [
        {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("description", "")}
        for x in r.json().get("web", {}).get("results", [])[:n]
    ]


async def _tavily(c: httpx.AsyncClient, q: str, n: int, key: str) -> list[dict[str, str]]:
    r = await c.post(
        "https://api.tavily.com/search",
        json={"query": q, "max_results": n},
        headers={"Authorization": f"Bearer {key}"},
    )
    r.raise_for_status()
    return [
        {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
        for x in r.json().get("results", [])[:n]
    ]


async def _searxng(c: httpx.AsyncClient, q: str, n: int, base: str) -> list[dict[str, str]]:
    r = await c.get(base.rstrip("/") + "/search", params={"q": q, "format": "json"})
    r.raise_for_status()
    return [
        {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
        for x in r.json().get("results", [])[:n]
    ]


async def _duckduckgo(c: httpx.AsyncClient, q: str, n: int) -> list[dict[str, str]]:
    from lxml import html as lhtml

    r = await c.post("https://html.duckduckgo.com/html/", data={"q": q}, headers={"User-Agent": UA})
    r.raise_for_status()
    doc = lhtml.fromstring(r.text)
    out = []
    for res in doc.xpath("//div[contains(concat(' ', @class, ' '), ' result ')]")[: n * 2]:
        a = res.xpath(".//a[contains(@class, 'result__a')]")
        if not a:
            continue
        href = a[0].get("href", "")
        if "uddg=" in href:
            href = parse_qs(urlparse(href).query).get("uddg", [href])[0]
        if not href.startswith("http") or "duckduckgo.com/y.js" in href:
            continue
        snip = res.xpath(".//*[contains(@class, 'result__snippet')]")
        out.append(
            {
                "title": a[0].text_content().strip(),
                "url": href,
                "snippet": snip[0].text_content().strip() if snip else "",
            }
        )
        if len(out) >= n:
            break
    return out


def make_web_tools(secrets: SecretStore, cfg: Callable[[], Any]) -> list[Tool]:
    async def web_search(a: SearchArgs, _c: ToolContext) -> dict[str, Any]:
        tc = cfg()
        order = (
            [tc.web_search_backend]
            if tc.web_search_backend != "auto"
            else ["brave", "tavily", "searxng", "duckduckgo"]
        )
        errors = []
        async with httpx.AsyncClient(timeout=10) as c:
            for backend in order:
                try:
                    if backend == "brave" and (k := secrets.get("BRAVE_API_KEY")):
                        results = await _brave(c, a.query, a.count, k)
                    elif backend == "tavily" and (k := secrets.get("TAVILY_API_KEY")):
                        results = await _tavily(c, a.query, a.count, k)
                    elif backend == "searxng" and tc.searxng_url:
                        results = await _searxng(c, a.query, a.count, tc.searxng_url)
                    elif backend == "duckduckgo":
                        results = await _duckduckgo(c, a.query, a.count)
                    else:
                        continue
                    return {"backend": backend, "results": results}
                except (httpx.HTTPError, ValueError) as e:
                    errors.append(f"{backend}: {e}")
        return {"error": "search failed", "details": errors}

    async def read_url(a: UrlArgs, _c: ToolContext) -> dict[str, Any]:
        try:
            final, body, ctype = await fetch(a.url, cfg().url_allow_private)
        except BlockedURL as e:
            return {"error": str(e)}
        title, text = await asyncio.to_thread(extract_text, body, ctype, final)
        text = text.strip()
        truncated = len(text) > MAX_CHARS
        return {"url": final, "title": title, "text": text[:MAX_CHARS], "truncated": truncated}

    return [
        Tool(
            "web_search",
            "Search the web. Returns titles, URLs and snippets.",
            web_search,
            params=SearchArgs,
            label="Searching the web",
            icon="search",
        ),
        Tool(
            "read_url",
            "Fetch a web page or PDF and return its main text.",
            read_url,
            params=UrlArgs,
            label="Reading",
            icon="globe",
            timeout_s=30,
        ),
    ]
