"""Web search that returns TEXT (titles + links), so the model can find a URL itself."""
from __future__ import annotations

import urllib.parse
import urllib.request
from html.parser import HTMLParser

from linux_mcp.schemas import ToolResult, WebSearchArgs

# Fixed: Removed "?q=" so the POST request handles the query payload correctly
SEARCH_URL = "https://html.duckduckgo.com/html/"
_MAX_BYTES = 600_000
_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) linux-mcp", "Accept-Language": "en"}


class _Anchors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results: list[dict] = []
        self.current_result: dict | None = None
        
        # Tracking states
        self.in_result = False
        self.in_title_link = False
        self.title_text: list[str] = []
        self.div_depth = 0  # Added to track nested div tags

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        class_name = attrs_dict.get("class", "")

        # 1. Detect the start of a result block
        if tag == "div" and "result" in class_name.split():
            self.in_result = True
            self.current_result = {"title": "", "url": ""}
            self.div_depth = 1  # Reset depth for new result block
            
        # Track nested divs to avoid premature closing
        elif self.in_result and tag == "div":
            self.div_depth += 1

        # 2. Inside a result, find the main result link (Fixed: result__url -> result__a)
        elif self.in_result and tag == "a" and "result__a" in class_name.split():
            href = attrs_dict.get("href", "")
            url = _real_url(href)
            if url and self.current_result:
                self.current_result["url"] = url
                self.in_title_link = True
                self.title_text = []

    def handle_data(self, data):
        # Collect text fragments inside the title link
        if self.in_title_link:
            self.title_text.append(data)

    def handle_endtag(self, tag):
        # Close out the title link text collection
        if tag == "a" and self.in_title_link:
            if self.current_result:
                clean_title = " ".join("".join(self.title_text).split())
                self.current_result["title"] = clean_title[:120]
            self.in_title_link = False

        # Close out the whole result block only when the outermost parent div closes
        elif tag == "div" and self.in_result:
            self.div_depth -= 1
            if self.div_depth == 0:
                if self.current_result and self.current_result["url"] and self.current_result["title"]:
                    self.results.append(self.current_result)
                self.current_result = None
                self.in_result = False
   

def _real_url(href: str) -> str | None:
    if "y.js" in href:  # ads
        return None
    if href.startswith("//"):
        href = "https:" + href
    query = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    if "uddg" in query:  # DuckDuckGo wraps the real link
        href = query["uddg"][0]
    parts = urllib.parse.urlparse(href)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.netloc.endswith("duckduckgo.com"):
        return None
    return href


def parse_results(page: str, limit: int) -> list[dict]:
    parser = _Anchors()
    parser.feed(page)  # Fixed: Removed the duplicate parser.feed(page) call
    
    # De-duplicate clean entries up to the limit
    seen = set()
    deduped_results = []
    
    for item in parser.results:
        if item["url"] not in seen:
            seen.add(item["url"])
            deduped_results.append(item)
            if len(deduped_results) >= limit:
                break
    return deduped_results


def _fetch(query: str) -> str:
    # 1. Prepare form data payload for a standard POST request
    data_dict = {"q": query}
    encoded_data = urllib.parse.urlencode(data_dict).encode("utf-8")
    
    # 2. Emulate an official web browser environment to pass WAF verification
    browser_headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://html.duckduckgo.com",
        "Referer": "https://html.duckduckgo.com/"
    }
    
    # 3. Requesting the base endpoint via POST bypasses the blocking checks
    req = urllib.request.Request(
        SEARCH_URL, 
        data=encoded_data, 
        headers=browser_headers, 
        method="POST"
    )
    
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read(_MAX_BYTES).decode("utf-8", errors="replace")


def web_search(args: WebSearchArgs) -> ToolResult:
    try:
        page = _fetch(args.query)
    except OSError as e:  # URLError, HTTPError, timeouts
        return ToolResult(ok=False, error=(
            f"Web search failed ({e}). As a fallback call open_url with "
            "https://www.google.com/search?q=<url-encoded terms> so the user can look."))
    results = parse_results(page, args.max_results)
    if not results:
        return ToolResult(ok=False, error=(
            "No results (the search provider may have blocked this request). Try different "
            "words, or open a Google search page with open_url."))
    return ToolResult(ok=True, data=results)


TOOL_SPEC = {
    "name": "web_search",
    "description": "Search the web; returns titles and URLs of the top results. Use it to FIND a "
                   "website or page, then open it with open_url.",
    "args_model": WebSearchArgs,
    "handler": web_search,
}