from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import httpx
import pytest

from douyin_research.media_processing import (
    MediaProcessingError,
    download_media,
    extract_audio,
    extract_tikhub_video_url,
)


def _public_resolver(_host: str) -> tuple[str, ...]:
    return ("8.8.8.8",)


def _payload() -> dict[str, object]:
    return {
        "code": 200,
        "data": {
            "aweme_details": [
                {
                    "aweme_id": "other-video",
                    "video": {"play_addr": {"url_list": ["https://cdn.example.test/other?sig=secret"]}},
                },
                {
                    "aweme_id": "wanted-video",
                    "video": {
                        "play_addr": {"url_list": ["https://cdn.example.test/wanted?sig=secret"]},
                        "download_addr": {"url_list": ["https://cdn.example.test/download?sig=secret"]},
                    },
                },
            ]
        },
    }


def test_extracts_only_exact_target_from_saved_tikhub_batch_detail() -> None:
    assert extract_tikhub_video_url(_payload(), "wanted-video") == "https://cdn.example.test/wanted?sig=secret"
    with pytest.raises(MediaProcessingError, match="absent"):
        extract_tikhub_video_url(_payload(), "missing-video")


def test_extract_falls_back_to_download_address_for_target_only() -> None:
    payload = _payload()
    selected = payload["data"]["aweme_details"][1]  # type: ignore[index]
    selected["video"].pop("play_addr")  # type: ignore[index,union-attr]
    assert extract_tikhub_video_url(payload, "wanted-video") == "https://cdn.example.test/download?sig=secret"


def test_extract_prefers_https_without_silently_upgrading_http() -> None:
    payload = _payload()
    selected = payload["data"]["aweme_details"][1]  # type: ignore[index]
    selected["video"]["play_addr"]["url_list"] = [  # type: ignore[index,union-attr]
        "http://cdn.example.test/insecure?sig=secret",
        "https://cdn.example.test/secure?sig=secret",
    ]
    assert extract_tikhub_video_url(payload, "wanted-video") == "https://cdn.example.test/secure?sig=secret"
    selected["video"].pop("download_addr")  # type: ignore[index,union-attr]
    selected["video"]["play_addr"]["url_list"] = ["http://cdn.example.test/insecure?sig=secret"]  # type: ignore[index,union-attr]
    with pytest.raises(MediaProcessingError, match="HTTPS") as error:
        extract_tikhub_video_url(payload, "wanted-video")
    assert "secret" not in str(error.value)


def test_download_streams_hashes_and_never_exposes_query(tmp_path: Path) -> None:
    body = b"media-bytes" * 10
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = download_media(
            "https://media.example.test/video?signature=private",
            tmp_path / "video.bin",
            client=client,
            allowed_hosts={"example.test"},
            max_bytes=len(body),
            resolve_host=_public_resolver,
        )
    assert len(calls) == 1
    assert calls[0].url.host == "8.8.8.8"
    assert calls[0].url.query == b"signature=private"
    assert calls[0].headers["host"] == "media.example.test"
    assert calls[0].extensions["sni_hostname"] == "media.example.test"
    assert result.size == len(body)
    assert result.sha256 == hashlib.sha256(body).hexdigest()
    assert (tmp_path / "video.bin").read_bytes() == body


@pytest.mark.parametrize(
    "url",
    [
        "http://media.example.test/file",
        "https://user:pass@media.example.test/file",
        "https://localhost/file",
        "https://127.0.0.1/file",
        "https://example.test.attacker.invalid/file",
        "https://media.example.test/file#fragment",
        "https://[::1",
    ],
)
def test_download_rejects_untrusted_urls_without_network_or_url_leak(tmp_path: Path, url: str) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("request made")))
    try:
        with pytest.raises(MediaProcessingError) as error:
            download_media(
                url,
                tmp_path / "out",
                client=client,
                allowed_hosts={"example.test"},
                resolve_host=_public_resolver,
            )
    finally:
        client.close()
    assert "example.test" not in str(error.value)
    assert not (tmp_path / "out").exists()


def test_download_pins_every_safe_redirect_hop(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    resolved: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(302, headers={"location": "/next?signature=private"})
        return httpx.Response(200, content=b"ok")

    def resolver(host: str) -> tuple[str, ...]:
        resolved.append(host)
        return ("8.8.8.8",)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = download_media(
            "https://media.example.test/start?signature=private",
            tmp_path / "out",
            client=client,
            allowed_hosts={"example.test"},
            resolve_host=resolver,
        )
    assert result.size == 2
    assert resolved == ["media.example.test", "media.example.test"]
    assert [request.url.host for request in requests] == ["8.8.8.8", "8.8.8.8"]
    assert [request.headers["host"] for request in requests] == ["media.example.test", "media.example.test"]
    assert [request.extensions["sni_hostname"] for request in requests] == ["media.example.test", "media.example.test"]
    assert requests[1].url.query == b"signature=private"


def test_download_revalidates_cross_domain_redirect_and_cleans_partial(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://attacker.invalid/private?key=secret"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(MediaProcessingError) as error:
            download_media(
                "https://media.example.test/video?token=private",
                tmp_path / "out",
                client=client,
                allowed_hosts={"example.test"},
                resolve_host=_public_resolver,
            )
    assert "token" not in str(error.value)
    assert "attacker" not in str(error.value)
    assert not (tmp_path / "out").exists()


def test_download_rejects_allowlisted_host_resolved_to_private_address(tmp_path: Path) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("request made")))
    try:
        with pytest.raises(MediaProcessingError) as error:
            download_media(
                "https://media.example.test/video?token=private",
                tmp_path / "out",
                client=client,
                allowed_hosts={"example.test"},
                resolve_host=lambda _: ("10.0.0.8",),
            )
    finally:
        client.close()
    assert "token" not in str(error.value)
    assert not (tmp_path / "out").exists()


def test_download_only_sends_a_verified_resolved_ip(tmp_path: Path) -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.host)
        return httpx.Response(200, content=b"ok")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(MediaProcessingError):
            download_media(
                "https://media.example.test/video",
                tmp_path / "out",
                client=client,
                allowed_hosts={"media.example.test"},
                resolve_host=lambda _: ("10.0.0.8",),
            )
    assert sent == []


def test_download_size_limit_removes_only_its_partial_file(tmp_path: Path) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"012345"))) as client:
        with pytest.raises(MediaProcessingError, match="size"):
            download_media(
                "https://media.example.test/video",
                tmp_path / "out",
                client=client,
                allowed_hosts={"media.example.test"},
                max_bytes=5,
                resolve_host=_public_resolver,
            )
    assert not (tmp_path / "out").exists()


def test_download_never_overwrites_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.write_bytes(b"keep")
    with pytest.raises(MediaProcessingError, match="already exists"):
        download_media(
            "https://media.example.test/video",
            output,
            client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"new"))),
            allowed_hosts={"media.example.test"},
            resolve_host=_public_resolver,
        )
    assert output.read_bytes() == b"keep"


def test_download_racing_publisher_keeps_its_target(tmp_path: Path) -> None:
    output = tmp_path / "out"

    def handler(_request: httpx.Request) -> httpx.Response:
        # This runs after download_media's initial output preflight but before
        # it publishes its own completed temporary file.
        output.write_bytes(b"other publisher")
        return httpx.Response(200, content=b"our media")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(MediaProcessingError, match="already exists"):
            download_media(
                "https://media.example.test/video",
                output,
                client=client,
                allowed_hosts={"media.example.test"},
                resolve_host=_public_resolver,
            )
    assert output.read_bytes() == b"other publisher"
    assert not list(tmp_path.glob(".out.*.download"))


def test_extract_audio_uses_no_shell_restricted_protocols_and_atomic_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "video.mp4"
    source.write_bytes(b"not-real-video")
    target = tmp_path / "audio.wav"
    seen: dict[str, object] = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        Path(command[-1]).write_bytes(b"RIFFfake")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = extract_audio(source, target, ffmpeg_binary="/usr/local/bin/ffmpeg")
    command = seen["command"]
    assert isinstance(command, list)
    assert "-protocol_whitelist" in command
    assert command[command.index("-protocol_whitelist") + 1] == "file,pipe"
    assert command[command.index("-ac") + 1] == "1"
    assert command[command.index("-ar") + 1] == "16000"
    assert command[command.index("-c:a") + 1] == "pcm_s16le"
    assert seen["kwargs"]["shell"] is False  # type: ignore[index]
    assert target.read_bytes() == b"RIFFfake"
    assert result.size == len(b"RIFFfake")


def test_extract_audio_failure_does_not_leak_stderr_or_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "video.mp4"
    source.write_bytes(b"input")
    target = tmp_path / "audio.wav"

    def fail(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["ffmpeg"], stderr=b"signed-url=secret")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(MediaProcessingError) as error:
        extract_audio(source, target)
    assert "secret" not in str(error.value)
    assert not target.exists()
