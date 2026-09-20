"""Safe local media acquisition and audio-normalization primitives.

This module is intentionally independent of the database and object storage.
Callers first select a persisted provider payload, then pass the reviewed CDN
allow-list and an explicit local destination.  It never logs or returns media
URLs, which commonly carry short-lived CDN signatures.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import socket
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

import httpx


class MediaProcessingError(RuntimeError):
    """Safe media-processing failure without a URL, response body, or stderr."""


@dataclass(frozen=True, slots=True)
class MediaDigest:
    """Non-sensitive identity of bytes written to a local destination."""

    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class _VerifiedMediaURL:
    """A request's original authority plus its verified, pinned IP address."""

    original_url: str
    host_header: str
    sni_hostname: str
    ip_address: str


_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
_CHUNK_SIZE = 1024 * 1024


def extract_tikhub_video_url(payload: Mapping[str, Any], platform_video_id: str) -> str:
    """Return the selected video's media URL from a saved TikHub detail payload.

    A detail response can contain several videos.  The media address is accepted
    only from an object whose own ``aweme_id`` (or ``platform_video_id``) exactly
    equals ``platform_video_id``.  This intentionally avoids a convenient but
    unsafe "first video" fallback.
    """

    if not isinstance(payload, Mapping):
        raise TypeError("TikHub detail payload must be a mapping")
    if not isinstance(platform_video_id, str) or not platform_video_id.strip():
        raise ValueError("platform_video_id is required")
    target = platform_video_id.strip()
    matching_record_seen = False
    for value in _walk_mappings(payload):
        if not _is_target_video(value, target):
            continue
        matching_record_seen = True
        video = value.get("video")
        if not isinstance(video, Mapping):
            continue
        for address_name in ("play_addr", "download_addr"):
            address = video.get(address_name)
            if not isinstance(address, Mapping):
                continue
            urls = address.get("url_list")
            if not isinstance(urls, list):
                continue
            for url in urls:
                if isinstance(url, str) and _is_absolute_https_url(url):
                    return url
    if matching_record_seen:
        raise MediaProcessingError("selected TikHub video has no usable HTTPS media address")
    raise MediaProcessingError("selected TikHub video is absent from the saved detail payload")


# A short alias keeps the integration layer free to use a domain-neutral name.
extract_video_download_url = extract_tikhub_video_url


def download_media(
    url: str,
    destination: str | Path,
    *,
    client: httpx.Client | None = None,
    allowed_hosts: Iterable[str],
    max_bytes: int = 2 * 1024 * 1024 * 1024,
    resolve_host: Callable[[str], Iterable[str]] | None = None,
) -> MediaDigest:
    """Stream an HTTPS media object to a new local file with SSRF protections.

    Every redirect target is independently checked.  The caller must pass the
    reviewed CDN domains; a hostname matches only itself or a dot-delimited
    subdomain, never a substring.  Literal loopback/private/reserved IP hosts
    are rejected before any request is made.  Each verified DNS answer is then
    pinned into the request URL, while the original authority remains the Host
    header and TLS SNI value.  This prevents a post-check DNS re-resolution.

    An injected client is intended for controlled tests.  Production callers
    should normally let this boundary create its own ``trust_env=False`` client
    so environment proxies cannot bypass the pinned connection.
    """

    hosts = _normalize_allowed_hosts(allowed_hosts)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    output = _new_output_path(destination)
    resolver = resolve_host or _resolve_host
    owns_client = client is None
    http_client = client or httpx.Client(
        follow_redirects=False,
        timeout=httpx.Timeout(30.0),
        trust_env=False,
        # Do not reuse a TLS connection created for a different CDN hostname.
        limits=httpx.Limits(max_keepalive_connections=0),
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".download", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink()
    try:
        current_url = url
        redirects = 0
        while True:
            verified = _validate_media_url(current_url, hosts, resolver)
            try:
                pinned_url = httpx.URL(verified.original_url).copy_with(host=verified.ip_address)
                with http_client.stream(
                    "GET",
                    pinned_url,
                    headers={"Host": verified.host_header},
                    follow_redirects=False,
                    extensions={"sni_hostname": verified.sni_hostname},
                ) as response:
                    if response.status_code in _REDIRECT_STATUS_CODES:
                        if redirects >= 3:
                            raise MediaProcessingError("media download exceeded redirect limit")
                        location = response.headers.get("location")
                        if not location:
                            raise MediaProcessingError("media download redirect is invalid")
                        next_url = urljoin(current_url, location)
                        current_url = next_url
                        redirects += 1
                        continue
                    if not 200 <= response.status_code < 300:
                        raise MediaProcessingError("media download request failed")
                    result = _write_stream(response, temporary, max_bytes)
                    # link(2) publishes only if no other caller created the
                    # destination after our preflight check.  Never replace or
                    # delete a racing caller's object.
                    try:
                        os.link(temporary, output)
                    except FileExistsError:
                        raise MediaProcessingError("media output already exists") from None
                    return result
            except MediaProcessingError:
                raise
            except (httpx.HTTPError, OSError, ValueError):
                raise MediaProcessingError("media download request failed") from None
    finally:
        # This pathname is allocated solely by this invocation.  The target is
        # deliberately never removed here: it might be a competing publisher's.
        temporary.unlink(missing_ok=True)
        if owns_client:
            http_client.close()


def extract_audio(
    input: str | Path,
    output: str | Path,
    *,
    ffmpeg_binary: str = "ffmpeg",
    timeout_seconds: float = 120.0,
) -> MediaDigest:
    """Create a mono, 16 kHz signed-16-bit PCM WAV from a local media file.

    ffmpeg receives an argument list (never a shell command) and is allowed only
    local-file and pipe protocols.  A same-directory temporary output is linked
    into place only after ffmpeg succeeds, so an existing output is never
    overwritten and failed conversions leave no partial target.
    """

    source = _local_input_path(input)
    target = _new_output_path(output)
    if not isinstance(ffmpeg_binary, str) or not ffmpeg_binary.strip():
        raise ValueError("ffmpeg_binary is required")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be positive")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".wav", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink()
    published = False
    try:
        command = [
            ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-n",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str(temporary),
        ]
        subprocess.run(
            command,
            check=True,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=float(timeout_seconds),
        )
        if not temporary.is_file():
            raise MediaProcessingError("audio extraction did not create output")
        # link(2) is an atomic create-only publication: unlike replace(), it
        # cannot clobber a file created between the initial existence check and
        # publication.
        try:
            os.link(temporary, target)
        except FileExistsError:
            raise MediaProcessingError("audio output already exists") from None
        published = True
        result = _digest_file(target)
        published = False
        return result
    except MediaProcessingError:
        raise
    except (OSError, subprocess.SubprocessError):
        raise MediaProcessingError("audio extraction failed") from None
    finally:
        temporary.unlink(missing_ok=True)
        if published:
            target.unlink(missing_ok=True)


def _walk_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_mappings(child)


def _is_target_video(value: Mapping[str, Any], target: str) -> bool:
    # TikHub's persisted batch detail format is
    # data.aweme_details[*].aweme_id/video.play_addr.url_list.
    return any(
        isinstance(value.get(key), (str, int)) and str(value[key]) == target
        for key in ("aweme_id", "platform_video_id")
    )


def _is_absolute_https_url(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def _normalize_allowed_hosts(allowed_hosts: Iterable[str]) -> frozenset[str]:
    if isinstance(allowed_hosts, (str, bytes)):
        raise TypeError("allowed_hosts must be an iterable of host names")
    normalized: set[str] = set()
    try:
        supplied = iter(allowed_hosts)
    except TypeError as exc:
        raise TypeError("allowed_hosts must be an iterable of host names") from exc
    for host in supplied:
        if not isinstance(host, str):
            raise ValueError("allowed_hosts contains an invalid host")
        cleaned = host.strip().rstrip(".").lower()
        if not cleaned or "/" in cleaned or "@" in cleaned or ":" in cleaned:
            raise ValueError("allowed_hosts contains an invalid host")
        try:
            cleaned = cleaned.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("allowed_hosts contains an invalid host") from exc
        if _is_ip_host(cleaned):
            raise ValueError("allowed_hosts cannot contain an IP address")
        normalized.add(cleaned)
    if not normalized:
        raise ValueError("allowed_hosts must not be empty")
    return frozenset(normalized)


def _validate_media_url(
    value: str,
    allowed_hosts: frozenset[str],
    resolve_host: Callable[[str], Iterable[str]],
) -> _VerifiedMediaURL:
    if not isinstance(value, str):
        raise MediaProcessingError("media URL is invalid")
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise MediaProcessingError("media URL is invalid") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise MediaProcessingError("media URL is invalid")
    host = parsed.hostname.rstrip(".").lower()
    if _is_ip_host(host) or host == "localhost":
        raise MediaProcessingError("media URL host is not permitted")
    try:
        canonical_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise MediaProcessingError("media URL host is not permitted") from None
    if not any(canonical_host == allowed or canonical_host.endswith(f".{allowed}") for allowed in allowed_hosts):
        raise MediaProcessingError("media URL host is not permitted")
    try:
        addresses = tuple(resolve_host(canonical_host))
    except (OSError, ValueError):
        raise MediaProcessingError("media URL host is not permitted") from None
    if not addresses or any(not _is_public_ip(address) for address in addresses):
        raise MediaProcessingError("media URL host is not permitted")
    return _VerifiedMediaURL(
        original_url=value,
        host_header=parsed.netloc,
        sni_hostname=canonical_host,
        ip_address=addresses[0],
    )


def _is_ip_host(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
    )


def _is_public_ip(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
    )


def _resolve_host(host: str) -> tuple[str, ...]:
    """Resolve every address before connecting, preventing DNS-to-RFC1918 SSRF."""

    return tuple(
        str(item[4][0])
        for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    )


def _new_output_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.name or path.exists() or path.is_symlink():
        raise MediaProcessingError("media output already exists or is invalid")
    if not path.parent.is_dir():
        raise MediaProcessingError("media output directory is unavailable")
    return path


def _write_stream(response: httpx.Response, temporary: Path, max_bytes: int) -> MediaDigest:
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("xb") as file:
            for chunk in response.iter_bytes(chunk_size=_CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise MediaProcessingError("media download exceeds configured size")
                digest.update(chunk)
                file.write(chunk)
    except FileExistsError:
        raise MediaProcessingError("media output already exists") from None
    return MediaDigest(sha256=digest.hexdigest(), size=size)


def _local_input_path(value: str | Path) -> Path:
    raw = str(value)
    parsed = urlsplit(raw)
    if parsed.scheme and parsed.scheme != "file":
        raise ValueError("audio input must be a local file")
    path = Path(parsed.path if parsed.scheme == "file" else raw)
    if not path.is_file():
        raise ValueError("audio input must be an existing local file")
    return path


def _digest_file(path: Path) -> MediaDigest:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as file:
        while chunk := file.read(_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return MediaDigest(sha256=digest.hexdigest(), size=size)
