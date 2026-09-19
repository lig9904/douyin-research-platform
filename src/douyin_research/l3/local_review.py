"""Loopback-only viewer for the exact L3 evidence a reviewer must inspect.

This module intentionally keeps evidence in one local process.  It does not
call Windmill, create an approval, reserve budget, or construct a provider.
"""

from __future__ import annotations

import argparse
import hmac
import html
import json
import os
import re
import secrets
import threading
import time
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from .evidence import L3EvidenceAssembler


_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}\Z")
_DEFAULT_TTL_SECONDS = 10 * 60
_COOKIE_NAME = "l3_review_session"
_LOCAL_REVIEW_HOST = "127.0.0.1"
_LOCAL_REVIEW_DB_PORT = 15432
_LOCAL_REVIEW_DB_USER = "l3_local_reviewer"
_LOCAL_REVIEW_DB_NAME = "douyin_research"
_BOOTSTRAP_JS = """(() => {
  const token = window.location.hash.slice(1);
  history.replaceState(null, '', '/');
  if (!token) { document.body.textContent = 'Missing local review token.'; return; }
  fetch('/session', {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    credentials: 'same-origin'
  }).then((response) => {
    if (!response.ok) throw new Error('session rejected');
    window.location.replace('/review');
  }).catch(() => { document.body.textContent = 'Local review session rejected.'; });
})();
"""
_LANDING_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Local L3 review</title>
<script src="/bootstrap.js" defer></script></head>
<body>Establishing local review session.</body></html>"""
_REVIEW_JS = """(() => {
  const clearEvidence = () => {
    document.documentElement.replaceChildren(document.createTextNode('Local review expired or closed.'));
  };
  const ttl = Number(document.body.dataset.ttlMs || '0');
  window.addEventListener('pagehide', clearEvidence, { once: true });
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) clearEvidence();
  });
  window.setTimeout(clearEvidence, Math.max(0, ttl));
})();
"""
_REVIEW_CSS = """:root {
  color-scheme: light;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #132238;
  background: #f4f7fb;
}
* { box-sizing: border-box; }
body { max-width: 1120px; margin: 0 auto; padding: 40px 24px 64px; }
header { margin-bottom: 24px; }
h1 { margin: 0 0 10px; font-size: 30px; letter-spacing: -0.02em; }
p { margin: 0; color: #607087; line-height: 1.65; }
.notice { padding: 14px 16px; border: 1px solid #b9d8fb; border-radius: 12px; background: #eef7ff; color: #214f7d; }
.grid { display: grid; gap: 20px; margin-top: 22px; }
.card { padding: 22px; border: 1px solid #dce4ee; border-radius: 16px; background: white; box-shadow: 0 8px 28px rgba(34, 58, 90, 0.06); }
h2 { margin: 0 0 14px; font-size: 18px; }
pre { margin: 0; padding: 16px; overflow-wrap: anywhere; white-space: pre-wrap; border-radius: 10px; background: #f7f9fc; color: #26384f; font: 13px/1.65 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.fingerprint { color: #165d9c; }
"""


class CandidateFingerprintMismatchError(ValueError):
    """The expected Windmill manifest does not bind the current evidence."""


@dataclass(frozen=True, slots=True)
class LocalReviewCandidate:
    """A single evidence bundle held only in this process's memory."""

    video_id: UUID
    evidence_fingerprint: str
    evidence_version: str
    evidence_modalities: tuple[str, ...]
    privacy_review_version: str
    evidence_bundle: Mapping[str, object]


class LocalReviewApp:
    """State for one short-lived, loopback-only evidence viewer."""

    def __init__(
        self,
        dsn: str,
        *,
        video_id: UUID | str,
        privacy_review_version: str,
        expected_fingerprint: str,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        assembler_factory: Callable[[str], Any] = L3EvidenceAssembler,
        token: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(dsn, str) or not dsn:
            raise ValueError("L3 review database URL is required")
        if not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 3600:
            raise ValueError("L3 review TTL must be between 1 and 3600 seconds")
        if not isinstance(expected_fingerprint, str) or not _FINGERPRINT_RE.fullmatch(
            expected_fingerprint
        ):
            raise ValueError("expected L3 evidence fingerprint is invalid")

        normalized_video_id = UUID(str(video_id))
        bundle = assembler_factory(dsn).prepare_for_privacy_review(
            normalized_video_id,
            privacy_review_version=privacy_review_version,
        )
        if not hmac.compare_digest(bundle.input_fingerprint, expected_fingerprint):
            raise CandidateFingerprintMismatchError(
                "current L3 evidence does not match the expected fingerprint"
            )
        self.candidate = LocalReviewCandidate(
            video_id=normalized_video_id,
            evidence_fingerprint=bundle.input_fingerprint,
            evidence_version=bundle.evidence_version,
            evidence_modalities=tuple(bundle.evidence_modalities),
            privacy_review_version=privacy_review_version,
            evidence_bundle=bundle.evidence_bundle,
        )
        self._clock = clock
        self._expires_at = clock() + ttl_seconds
        self._bootstrap_token = token or secrets.token_urlsafe(32)
        self._session_token: str | None = None
        self._lock = threading.Lock()

    @property
    def expires_at(self) -> float:
        return self._expires_at

    @property
    def bootstrap_token(self) -> str:
        """One-time token to be placed only in a browser URL fragment."""

        return self._bootstrap_token

    def expired(self) -> bool:
        return self._clock() >= self._expires_at

    def establish_session(self, authorization: str | None) -> str | None:
        """Consume the bootstrap bearer token and return one cookie value."""

        if self.expired() or not isinstance(authorization, str):
            return None
        expected = f"Bearer {self._bootstrap_token}"
        with self._lock:
            if not self._bootstrap_token or not hmac.compare_digest(
                authorization, expected
            ):
                return None
            self._bootstrap_token = ""
            self._session_token = secrets.token_urlsafe(32)
            return self._session_token

    def consume_session(self, cookie_header: str | None) -> bool:
        """Accept the session for exactly one evidence response."""

        if self.expired() or not self._session_token or not cookie_header:
            return False
        try:
            cookies = SimpleCookie()
            cookies.load(cookie_header)
            supplied = cookies.get(_COOKIE_NAME)
        except (CookieError, ValueError):
            return False
        if supplied is None:
            return False
        with self._lock:
            if not self._session_token or not hmac.compare_digest(
                supplied.value, self._session_token
            ):
                return False
            self._session_token = None
            return True

    def render_review_html(self) -> bytes:
        """Render only for an authenticated session; escaping prevents HTML injection."""

        payload = json.dumps(
            self.candidate.evidence_bundle,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
            default=str,
        )
        escaped = html.escape(payload, quote=True)
        manifest = html.escape(
            json.dumps(
                {
                    "video_id": str(self.candidate.video_id),
                    "evidence_fingerprint": self.candidate.evidence_fingerprint,
                    "evidence_version": self.candidate.evidence_version,
                    "evidence_modalities": list(self.candidate.evidence_modalities),
                    "privacy_review_version": self.candidate.privacy_review_version,
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ),
            quote=True,
        )
        ttl_ms = max(0, int((self._expires_at - self._clock()) * 1000))
        return (
            "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<title>Local L3 evidence review</title>"
            "<link rel=\"stylesheet\" href=\"/review.css\">"
            "<script src=\"/review.js\" defer></script></head>"
            f"<body data-ttl-ms=\"{ttl_ms}\">"
            "<header><h1>L3 正文安全审核</h1>"
            "<p class=\"notice\">此页仅显示当前指纹对应的模型输入。正文只返回一次；刷新、后退、离页或超时后不可再次访问。</p></header>"
            "<main class=\"grid\">"
            f"<section class=\"card\"><h2>候选清单与指纹</h2><pre class=\"fingerprint\">{manifest}</pre></section>"
            f"<section class=\"card\"><h2>实际模型输入正文</h2><pre>{escaped}</pre></section>"
            "</main>"
            "</body></html>"
        ).encode("utf-8")


class _LocalReviewHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], app: LocalReviewApp) -> None:
        self.app = app
        super().__init__(address, _LocalReviewHandler)


class _LocalReviewHandler(BaseHTTPRequestHandler):
    server: _LocalReviewHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        """Never write request paths, headers, or response details to access logs."""

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler convention
        if not self._valid_host():
            self._send_text(HTTPStatus.FORBIDDEN, "forbidden")
            return
        if self.server.app.expired():
            self._send_text(HTTPStatus.FORBIDDEN, "session expired")
            return
        if self.path == "/":
            self._send_bytes(HTTPStatus.OK, _LANDING_PAGE.encode("utf-8"), "text/html")
            return
        if self.path == "/bootstrap.js":
            self._send_bytes(
                HTTPStatus.OK,
                _BOOTSTRAP_JS.encode("utf-8"),
                "application/javascript",
            )
            return
        if self.path == "/review.js":
            self._send_bytes(
                HTTPStatus.OK,
                _REVIEW_JS.encode("utf-8"),
                "application/javascript",
            )
            return
        if self.path == "/review.css":
            self._send_bytes(
                HTTPStatus.OK,
                _REVIEW_CSS.encode("utf-8"),
                "text/css",
            )
            return
        if self.path == "/review":
            if not self.server.app.consume_session(self.headers.get("Cookie")):
                self._send_text(HTTPStatus.FORBIDDEN, "forbidden")
                return
            self._send_bytes(
                HTTPStatus.OK,
                self.server.app.render_review_html(),
                "text/html",
            )
            return
        if self.path == "/session":
            self._send_text(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")
            return
        self._send_text(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler convention
        if self.path != "/session":
            self._send_text(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")
            return
        if not self._valid_host() or not self._valid_origin():
            self._send_text(HTTPStatus.FORBIDDEN, "forbidden")
            return
        if self.headers.get("Content-Length") not in (None, "0"):
            self._send_text(HTTPStatus.BAD_REQUEST, "request body is not accepted")
            return
        session = self.server.app.establish_session(self.headers.get("Authorization"))
        if session is None:
            self._send_text(HTTPStatus.FORBIDDEN, "forbidden")
            return
        ttl = max(0, int(self.server.app.expires_at - time.monotonic()))
        cookie = (
            f"{_COOKIE_NAME}={session}; HttpOnly; SameSite=Strict; Path=/; "
            f"Max-Age={ttl}"
        )
        self._send_bytes(HTTPStatus.NO_CONTENT, b"", None, set_cookie=cookie)

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler convention
        self._send_text(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")

    def _valid_host(self) -> bool:
        host = self.headers.get("Host")
        return host == f"127.0.0.1:{self.server.server_port}"

    def _valid_origin(self) -> bool:
        return self.headers.get("Origin") == f"http://127.0.0.1:{self.server.server_port}"

    def _send_text(self, status: HTTPStatus, text: str) -> None:
        self._send_bytes(status, text.encode("utf-8"), "text/plain")

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str | None,
        *,
        set_cookie: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; connect-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
            "style-src 'self'; img-src 'none'",
        )
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if content_type:
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


def create_server(app: LocalReviewApp, *, port: int = 0) -> _LocalReviewHTTPServer:
    """Create a server that can only bind the IPv4 loopback address."""

    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("local review port is invalid")
    return _LocalReviewHTTPServer(("127.0.0.1", port), app)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start a loopback-only L3 evidence viewer")
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--privacy-review-version", required=True)
    parser.add_argument("--expected-fingerprint", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--ttl-seconds", type=int, default=_DEFAULT_TTL_SECONDS)
    return parser


def _validate_local_review_dsn(dsn: str | None) -> str:
    """Accept only the isolated least-privilege database endpoint."""

    if not isinstance(dsn, str) or not dsn:
        raise ValueError("L3 review database URL is required")
    parsed = urlsplit(dsn)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname != _LOCAL_REVIEW_HOST
        or parsed.port != _LOCAL_REVIEW_DB_PORT
        or parsed.username != _LOCAL_REVIEW_DB_USER
        or parsed.path != f"/{_LOCAL_REVIEW_DB_NAME}"
        or not parsed.password
        or query != {"sslmode": ["disable"]}
    ):
        raise ValueError("L3 review database URL is not the isolated local reviewer")
    return dsn


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        dsn = _validate_local_review_dsn(os.environ.get("L3_REVIEW_DATABASE_URL"))
        app = LocalReviewApp(
            dsn,
            video_id=args.video_id,
            privacy_review_version=args.privacy_review_version,
            expected_fingerprint=args.expected_fingerprint,
            ttl_seconds=args.ttl_seconds,
        )
        server = create_server(app, port=args.port)
    except Exception:
        print("Local L3 review page was not started.")
        return 2

    host, port = server.server_address
    review_url = f"http://{host}:{port}/#{app.bootstrap_token}"
    try:
        browser_opened = webbrowser.open(review_url, new=1)
    except Exception:
        browser_opened = False
    if not browser_opened:
        server.server_close()
        print("Local L3 review browser could not be opened; the page was not started.")
        return 2
    print(f"Local L3 review is listening on {host}:{port} for {args.ttl_seconds} seconds.")
    timer = threading.Timer(args.ttl_seconds, server.shutdown)
    timer.daemon = True
    timer.start()
    try:
        server.serve_forever()
    finally:
        timer.cancel()
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through CLI usage
    raise SystemExit(main())
