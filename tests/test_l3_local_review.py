from __future__ import annotations

import http.client
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from douyin_research.l3.local_review import (
    CandidateFingerprintMismatchError,
    LocalReviewApp,
    _validate_local_review_dsn,
    create_server,
)


FINGERPRINT = "a" * 64
RAW_SENTINEL = "RAW_TRANSCRIPT_SENTINEL <script>alert('x')</script>"


@dataclass
class _Bundle:
    input_fingerprint: str = FINGERPRINT
    evidence_version: str = "l3-evidence-v1.0.0"
    evidence_modalities: tuple[str, ...] = ("metadata", "comments", "transcript")

    @property
    def evidence_bundle(self):
        return {
            "video_metadata": {"title": "PRIVATE_TITLE", "description": "PRIVATE_DESCRIPTION"},
            "comment_features": {"raw_comment_text_included": False},
            "transcript": {"text": RAW_SENTINEL},
        }


class _Assembler:
    calls: list[tuple[object, str]] = []
    bundle = _Bundle()

    def __init__(self, dsn: str) -> None:
        assert dsn == "postgresql://read-only"

    def prepare_for_privacy_review(self, video_id, *, privacy_review_version: str):
        self.calls.append((video_id, privacy_review_version))
        return self.bundle


@contextmanager
def _server(app: LocalReviewApp) -> Iterator[tuple[str, int]]:
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "127.0.0.1", server.server_port
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def _request(host: str, port: int, method: str, path: str, headers=None):
    connection = http.client.HTTPConnection(host, port, timeout=2)
    try:
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def _app(**kwargs) -> LocalReviewApp:
    _Assembler.calls = []
    return LocalReviewApp(
        "postgresql://read-only",
        video_id="00000000-0000-0000-0000-000000000001",
        privacy_review_version="privacy-v1",
        expected_fingerprint=FINGERPRINT,
        assembler_factory=_Assembler,
        token="one-time-test-token",
        **kwargs,
    )


def test_constructs_exact_candidate_and_rejects_fingerprint_mismatch() -> None:
    app = _app()
    assert str(app.candidate.video_id) == "00000000-0000-0000-0000-000000000001"
    assert _Assembler.calls == [
        (app.candidate.video_id, "privacy-v1"),
    ]
    _Assembler.bundle = _Bundle(input_fingerprint="b" * 64)
    with pytest.raises(CandidateFingerprintMismatchError):
        _app()
    _Assembler.bundle = _Bundle()


def test_landing_and_bootstrap_never_contain_evidence_body() -> None:
    with _server(_app()) as (host, port):
        status, headers, landing = _request(host, port, "GET", "/")
        assert status == 200
        assert b"/bootstrap.js" in landing
        assert RAW_SENTINEL.encode() not in landing
        assert headers["Cache-Control"].startswith("no-store")
        status, _, bootstrap = _request(host, port, "GET", "/bootstrap.js")
        assert status == 200
        assert b"window.location.hash" in bootstrap
        assert RAW_SENTINEL.encode() not in bootstrap
        status, _, review_js = _request(host, port, "GET", "/review.js")
        assert status == 200
        assert b"pagehide" in review_js
        assert b"replaceChildren" in review_js
        assert RAW_SENTINEL.encode() not in review_js
        status, _, review_css = _request(host, port, "GET", "/review.css")
        assert status == 200
        assert b".card" in review_css
        assert RAW_SENTINEL.encode() not in review_css


def test_review_requires_single_use_loopback_session_and_escapes_body() -> None:
    with _server(_app()) as (host, port):
        status, headers, denied = _request(host, port, "GET", "/review")
        assert status == 403
        assert RAW_SENTINEL.encode() not in denied

        origin = f"http://{host}:{port}"
        status, headers, body = _request(
            host,
            port,
            "POST",
            "/session",
            {"Origin": origin, "Authorization": "Bearer one-time-test-token"},
        )
        assert status == 204
        assert body == b""
        cookie = headers["Set-Cookie"]
        assert "HttpOnly" in cookie
        assert "SameSite=Strict" in cookie

        status, _, _ = _request(
            host,
            port,
            "POST",
            "/session",
            {"Origin": origin, "Authorization": "Bearer one-time-test-token"},
        )
        assert status == 403

        status, headers, reviewed = _request(
            host, port, "GET", "/review", {"Cookie": cookie.split(";", 1)[0]}
        )
        assert status == 200
        assert b"RAW_TRANSCRIPT_SENTINEL" in reviewed
        assert b"<script>alert" not in reviewed
        assert b"&lt;script&gt;alert" in reviewed
        assert b'data-ttl-ms="' in reviewed
        assert b'<script src="/review.js" defer></script>' in reviewed
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Referrer-Policy"] == "no-referrer"
        assert "default-src 'none'" in headers["Content-Security-Policy"]
        assert headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"

        status, _, repeated = _request(
            host, port, "GET", "/review", {"Cookie": cookie.split(";", 1)[0]}
        )
        assert status == 403
        assert RAW_SENTINEL.encode() not in repeated


def test_session_rejects_wrong_host_and_origin_without_consuming_token() -> None:
    with _server(_app()) as (host, port):
        origin = f"http://{host}:{port}"
        status, _, _ = _request(
            host,
            port,
            "POST",
            "/session",
            {
                "Host": f"localhost:{port}",
                "Origin": origin,
                "Authorization": "Bearer one-time-test-token",
            },
        )
        assert status == 403
        status, _, _ = _request(
            host,
            port,
            "POST",
            "/session",
            {
                "Origin": f"http://localhost:{port}",
                "Authorization": "Bearer one-time-test-token",
            },
        )
        assert status == 403
        status, _, _ = _request(
            host,
            port,
            "POST",
            "/session",
            {"Origin": origin, "Authorization": "Bearer one-time-test-token"},
        )
        assert status == 204


def test_no_write_or_body_endpoints_and_ttl_fail_closed() -> None:
    clock = SimpleNamespace(value=100.0)
    app = _app(ttl_seconds=5, clock=lambda: clock.value)
    with _server(app) as (host, port):
        for method, path in (("POST", "/review"), ("GET", "/session"), ("POST", "/approve")):
            status, _, body = _request(host, port, method, path)
            assert status == 405
            assert RAW_SENTINEL.encode() not in body
        clock.value = 106.0
        status, _, body = _request(host, port, "GET", "/")
        assert status == 403
        assert RAW_SENTINEL.encode() not in body


def test_module_does_not_import_review_or_execution_boundaries() -> None:
    source = __import__("douyin_research.l3.local_review", fromlist=["x"]).__file__
    assert source is not None
    text = open(source, encoding="utf-8").read()
    assert "from .review import" not in text
    assert "from .execution import" not in text
    assert "L3ExecutionCoordinator" not in text


def test_cli_dsn_is_pinned_to_isolated_read_only_database() -> None:
    valid = (
        "postgresql://l3_local_reviewer:secret@127.0.0.1:15432/"
        "douyin_research?sslmode=disable"
    )
    assert _validate_local_review_dsn(valid) == valid
    for unsafe in (
        None,
        "postgresql://douyin_research:secret@127.0.0.1:15432/douyin_research?sslmode=disable",
        "postgresql://l3_local_reviewer:secret@db.example:15432/douyin_research?sslmode=disable",
        "postgresql://l3_local_reviewer:secret@127.0.0.1:5432/douyin_research?sslmode=disable",
        "postgresql://l3_local_reviewer:secret@127.0.0.1:15432/douyin_research?sslmode=require",
    ):
        with pytest.raises(ValueError):
            _validate_local_review_dsn(unsafe)


def test_cli_source_never_prints_bootstrap_capability() -> None:
    source = __import__("douyin_research.l3.local_review", fromlist=["x"]).__file__
    assert source is not None
    text = open(source, encoding="utf-8").read()
    assert 'print(f"Open once in this browser: {review_url}")' not in text
