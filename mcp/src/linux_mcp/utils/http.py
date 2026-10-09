"""Minimal HTTP client for the research tools (check_url, fetch_page).

Only http/https, including after redirects: urllib would otherwise follow a redirect to
`file://` or `ftp://`. An HTTP error status is a *response* (404 is an answer the model
needs), not an exception; only "no answer at all" (DNS, TLS, timeout) raises.
"""
from __future__ import annotations

import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

# A browser-like agent: many sites refuse obviously automated clients with 403, which
# would make a live page look dead.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


class FetchError(RuntimeError):
    """The server never answered. `kind` is dns | tls | timeout | connection | scheme."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass
class Response:
    status: int
    url: str                      # final URL after redirects
    headers: dict = field(default_factory=dict)
    body: bytes = b""

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    def text(self) -> str:
        charset = "utf-8"
        for part in self.headers.get("content-type", "").split(";")[1:]:
            k, _, v = part.strip().partition("=")
            if k.lower() == "charset" and v:
                charset = v.strip('"')
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


class _HttpOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).scheme not in ("http", "https"):
            raise FetchError("scheme", f"redirect to a non-http(s) URL refused: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_HttpOnlyRedirects)


def request(url: str, method: str = "GET", max_bytes: int = 2_000_000, timeout: float = 10,
            headers: dict | None = None) -> Response:
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise FetchError("scheme", "only http(s) URLs are allowed")
    req = urllib.request.Request(url, headers={**_HEADERS, **(headers or {})}, method=method)
    try:
        with _opener.open(req, timeout=timeout) as resp:
            body = resp.read(max_bytes) if method != "HEAD" else b""
            return Response(resp.status, resp.geturl(), {k.lower(): v for k, v in resp.headers.items()}, body)
    except urllib.error.HTTPError as e:
        body = e.read(max_bytes) if method != "HEAD" else b""
        return Response(e.code, e.geturl() or url, {k.lower(): v for k, v in (e.headers or {}).items()}, body)
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, socket.gaierror):
            raise FetchError("dns", f"domain does not resolve: {reason}") from e
        if isinstance(reason, ssl.SSLError):
            raise FetchError("tls", f"TLS/certificate error: {reason}") from e
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise FetchError("timeout", f"timed out after {timeout}s") from e
        raise FetchError("connection", f"connection failed: {reason}") from e
    except (TimeoutError, socket.timeout) as e:
        raise FetchError("timeout", f"timed out after {timeout}s") from e
    except (ConnectionError, OSError) as e:
        raise FetchError("connection", f"connection failed: {e}") from e
