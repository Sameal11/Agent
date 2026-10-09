"""Web search that returns TEXT (title, URL, snippet), so the model can find a URL itself.

Providers are tried in order until one returns results:
  1. DuckDuckGo Lite (GET). The html.duckduckgo.com endpoint now answers every request,
     GET or POST, browser-like headers or not, with a 202 bot-check page ("anomaly"),
     which the old parser reported as "No results".
  2. Bing, as a fallback when DuckDuckGo is blocked or empty.
A provider that serves a bot-check page is reported as blocked, not as "no results", so the
model does not conclude the thing it searched for does not exist.
"""
from __future__ import annotations

import base64
import urllib.parse
from html.parser import HTMLParser

from linux_mcp.schemas import FetchPageArgs, ToolResult, WebSearchArgs
from linux_mcp.utils import http

# DDG Lite serves results to a plain client but a bot-check to a fake Chrome User-Agent.
_DDG_HEADERS = {"User-Agent": "linux-mcp/0.1 (+local agent)"}
_BING_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"}
_MAX_BYTES = 800_000


class SearchBlocked(Exception):
    pass


def _text(parts: list[str], limit: int) -> str:
    return " ".join("".join(parts).split())[:limit]


def _real_url(href: str) -> str | None:
    """Unwrap DuckDuckGo (/l/?uddg=) and Bing (/ck/a?...&u=a1<base64>) redirect links."""
    if href.startswith("//"):
        href = "https:" + href
    parts = urllib.parse.urlparse(href)
    query = urllib.parse.parse_qs(parts.query)
    if "uddg" in query:
        href = query["uddg"][0]
    elif parts.netloc.endswith("bing.com") and parts.path.startswith("/ck/") and "u" in query:
        encoded = query["u"][0]
        if not encoded.startswith("a1"):
            return None
        encoded = encoded[2:]
        try:
            href = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    parts = urllib.parse.urlparse(href)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if parts.netloc.endswith(("duckduckgo.com", "bing.com")) or "y.js" in href:  # ads/internal
        return None
    return href


class _DdgLite(HTMLParser):
    """<a class='result-link' href=...>title</a> ... <td class='result-snippet'>text</td>"""

    def __init__(self):
        super().__init__()
        self.results: list[dict] = []
        self._field: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "a" and "result-link" in classes:
            url = _real_url(a.get("href", ""))
            self.results.append({"title": "", "url": url, "snippet": ""})
            self._field, self._buf = "title", []
        elif tag == "td" and "result-snippet" in classes and self.results:
            self._field, self._buf = "snippet", []

    def handle_data(self, data):
        if self._field:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if (self._field == "title" and tag == "a") or (self._field == "snippet" and tag == "td"):
            self.results[-1][self._field] = _text(self._buf, 120 if self._field == "title" else 300)
            self._field = None


class _Bing(HTMLParser):
    """<li class="b_algo"> <h2><a href=...>title</a></h2> ... <p>snippet</p> </li>"""

    def __init__(self):
        super().__init__()
        self.results: list[dict] = []
        self._depth = 0          # <li> nesting inside the current b_algo
        self._in_h2 = False
        self._field: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "li":
            if self._depth:
                self._depth += 1
            elif "b_algo" in (a.get("class") or "").split():
                self._depth = 1
                self.results.append({"title": "", "url": None, "snippet": ""})
            return
        if not self._depth:
            return
        cur = self.results[-1]
        if tag == "h2":
            self._in_h2 = True
        elif tag == "a" and self._in_h2 and cur["url"] is None:
            cur["url"] = _real_url(a.get("href", ""))
            self._field, self._buf = "title", []
        elif tag == "p" and not cur["snippet"] and self._field is None:
            self._field, self._buf = "snippet", []

    def handle_data(self, data):
        if self._field:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "li" and self._depth:
            self._depth -= 1
        elif tag == "h2":
            self._in_h2 = False
        if (self._field == "title" and tag == "a") or (self._field == "snippet" and tag == "p"):
            self.results[-1][self._field] = _text(self._buf, 120 if self._field == "title" else 300)
            self._field = None


def _dedupe(results: list[dict], limit: int) -> list[dict]:
    seen, out = set(), []
    for r in results:
        if r["url"] and r["title"] and r["url"] not in seen:
            seen.add(r["url"])
            out.append(r)
            if len(out) >= limit:
                break
    return out


def parse_ddg_lite(page: str, limit: int) -> list[dict]:
    parser = _DdgLite()
    parser.feed(page)
    return _dedupe(parser.results, limit)


def parse_bing(page: str, limit: int) -> list[dict]:
    parser = _Bing()
    parser.feed(page)
    return _dedupe(parser.results, limit)


def _get(url: str, headers: dict) -> str:
    resp = http.request(url, headers=headers, max_bytes=_MAX_BYTES)
    page = resp.text()
    # DDG answers a bot-check with 202 + "anomaly"; Bing with a captcha page.
    if resp.status == 202 or resp.status >= 400 or "anomaly-modal" in page or "/captcha/" in page:
        raise SearchBlocked(f"HTTP {resp.status}")
    return page


def _search_ddg(query: str, limit: int) -> list[dict]:
    q = urllib.parse.urlencode({"q": query})
    return parse_ddg_lite(_get(f"https://lite.duckduckgo.com/lite/?{q}", _DDG_HEADERS), limit)


def _search_bing(query: str, limit: int) -> list[dict]:
    q = urllib.parse.urlencode({"q": query})
    return parse_bing(_get(f"https://www.bing.com/search?{q}", _BING_HEADERS), limit)


PROVIDERS = [("duckduckgo", _search_ddg), ("bing", _search_bing)]


def web_search(args: WebSearchArgs) -> ToolResult:
    problems = []
    for name, search in PROVIDERS:
        try:
            results = search(args.query, args.max_results)
        except SearchBlocked as e:
            problems.append(f"{name}: blocked the request ({e})")
            continue
        except (http.FetchError, OSError) as e:
            problems.append(f"{name}: {e}")
            continue
        if results:
            return ToolResult(ok=True, data={"provider": name, "results": results})
        problems.append(f"{name}: no results")
    if all(p.endswith("no results") for p in problems):
        return ToolResult(ok=False, error="No results for this query. Try fewer or different words.")
    return ToolResult(ok=False, error=(
        "Web search is unavailable right now (" + "; ".join(problems) + "). This says nothing "
        "about whether the page exists. Tell the user search failed; do not guess a URL."))


TOOL_SPEC = {
    "name": "web_search",
    "description": "Search the web; returns title, URL and snippet of the top results. Use it to FIND "
                   "a website or page, then open it with open_url using a URL from the results.",
    "args_model": WebSearchArgs,
    "handler": web_search,
}


# ---- fetch_page: read a page's text (so the model can actually read the web) ----
class _TextExtractor(HTMLParser):
    """Turn HTML into readable plain text. Drops script/style/etc., inserts line breaks at
    block tags, and records links. Stdlib only - no BeautifulSoup dependency."""
    _SKIP = {"script", "style", "head", "noscript", "svg", "template", "iframe"}
    _BLOCK = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
              "section", "article", "header", "footer", "ul", "ol", "table", "pre", "blockquote"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._skip = 0
        self._href: str | None = None
        self._linktext: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        if tag in self._BLOCK:
            self.parts.append("\n")
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._linktext = []

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        if tag in self._BLOCK:
            self.parts.append("\n")
        if tag == "a" and self._href:
            self.links.append((" ".join("".join(self._linktext).split()), self._href))
            self._href = None

    def handle_data(self, data):
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._linktext.append(data)

    def text(self) -> str:
        lines, blank = [], False
        for ln in "".join(self.parts).splitlines():
            ln = ln.strip()
            if ln:
                lines.append(ln)
                blank = False
            elif not blank:          # collapse runs of blank lines into one
                lines.append("")
                blank = True
        return "\n".join(lines).strip()


def fetch_page(args: FetchPageArgs) -> ToolResult:
    try:
        resp = http.request(args.url, max_bytes=2_000_000, timeout=12)
    except http.FetchError as e:
        return ToolResult(ok=False, error=f"Could not fetch {args.url} ({e.kind}): {e}")
    if resp.status >= 400:
        return ToolResult(ok=False, error=f"HTTP {resp.status} for {resp.url}")

    ctype = resp.content_type
    if ctype and "html" not in ctype and "xml" not in ctype:
        if ctype.startswith("text/") or "json" in ctype:   # plain text / json: return as-is
            return ToolResult(ok=True, data={"url": resp.url, "content_type": ctype,
                                             "text": resp.text()[:args.max_chars]})
        return ToolResult(ok=False, error=f"{resp.url} is {ctype}, not a readable web page.")

    extractor = _TextExtractor()
    extractor.feed(resp.text())
    text = extractor.text()
    if args.find:
        q = args.find.lower()
        hits = [ln for ln in text.split("\n") if q in ln.lower()]
        text = "\n".join(hits) if hits else f"(no line matches '{args.find}'; showing the start)\n{text}"

    data = {"url": resp.url, "text": text[:args.max_chars]}
    if args.include_links:
        seen, links = set(), []
        for label, href in extractor.links:
            absolute = urllib.parse.urljoin(resp.url, href)
            if absolute.startswith(("http://", "https://")) and absolute not in seen:
                seen.add(absolute)
                links.append({"text": label[:80], "url": absolute})
        data["links"] = links[:60]
    return ToolResult(ok=True, data=data)


FETCH_TOOL_SPEC = {
    "name": "fetch_page",
    "description": "Read a web page as plain text (optionally only lines matching `find`, and/or its "
                   "links). Use this to READ a page's contents; use open_url only to open a page in the "
                   "user's browser. Fetch a URL from web_search results or the user, not a guessed one.",
    "args_model": FetchPageArgs,
    "handler": fetch_page,
}
