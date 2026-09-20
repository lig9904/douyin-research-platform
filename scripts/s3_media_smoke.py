"""Small real S3 acceptance test; credentials are prompted without echo.

Creates one 0.1-second silent WAV and leaves its content-addressed object in the
specified bucket. Never prints credentials or presigned URLs. No paid API calls.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import io
import json
import tempfile
import wave
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from douyin_research.media_storage import PrivateS3MediaStorage, S3MediaStorageConfig


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--public-endpoint", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    access = getpass.getpass("S3 access key (hidden): ")
    secret = getpass.getpass("S3 secret key (hidden): ")
    stage = "configuration"
    try:
        store = PrivateS3MediaStorage(S3MediaStorageConfig(
            endpoint=args.endpoint, bucket=args.bucket, region=args.region,
            access_key_id=access, secret_access_key=secret,
            force_path_style=True, use_ssl=args.endpoint.startswith("https://"),
            public_endpoint=args.public_endpoint,
        ))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(b"\0" * 1600)
        payload = buf.getvalue()
        with tempfile.TemporaryDirectory(prefix="douyin-s3-smoke-") as tmp:
            path = Path(tmp) / "silent-smoke.wav"
            path.write_bytes(payload)
            stage = "upload"
            stored = store.upload_file(path, content_type="audio/wav")
            stage = "deduplicate"
            assert store.upload_file(path, content_type="audio/wav") == stored
        stage = "signed_read"
        url = store.presigned_read_url(stored.key, expires_in=300)
        parsed = urlsplit(url)
        params = dict(parse_qsl(parsed.query))
        assert params.get("X-Amz-Algorithm") == "AWS4-HMAC-SHA256"
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as http:
            response = http.get(url)
            assert response.status_code == 200
            assert response.content == payload
            assert hashlib.sha256(response.content).hexdigest() == stored.sha256
            stage = "anonymous_denied"
            anonymous_url = urlunsplit(parsed._replace(query=""))
            anonymous = http.get(anonymous_url)
            assert anonymous.status_code == 403
            stage = "tampered_signature_denied"
            signature = params["X-Amz-Signature"]
            params["X-Amz-Signature"] = ("0" if signature[0] != "0" else "1") + signature[1:]
            tampered = http.get(urlunsplit(parsed._replace(query=urlencode(params))))
            assert tampered.status_code == 403
        print(json.dumps({
            "ok": True, "bucket": args.bucket, "key": stored.key,
            "bytes": stored.size, "sha256": stored.sha256,
            "signed_get": 200, "anonymous_get": 403, "tampered_get": 403,
            "expiry_seconds": 300, "retained_test_object": True,
        }))
        return 0
    except Exception as error:
        # Provider exceptions and request URLs can contain secrets.
        safe_code = None
        cursor = error
        for _ in range(5):
            response = getattr(cursor, "response", None)
            if isinstance(response, dict):
                code = response.get("Error", {}).get("Code")
                if code in {"InvalidAccessKeyId", "SignatureDoesNotMatch", "AccessDenied",
                            "NoSuchBucket", "RequestTimeTooSkewed", "InvalidToken", "403"}:
                    safe_code = code
            cursor = getattr(cursor, "__context__", None)
            if cursor is None:
                break
        if safe_code == "403" and stage == "upload":
            # HEAD omits S3's structured error body. One read-only GET of the
            # same test key distinguishes invalid credentials from permissions.
            try:
                probe = store._client.get_object(
                    Bucket=args.bucket,
                    Key=store.object_key(hashlib.sha256(payload).hexdigest()),
                )
                probe["Body"].close()
            except Exception as probe_error:
                detail = getattr(probe_error, "response", {})
                code = detail.get("Error", {}).get("Code") if isinstance(detail, dict) else None
                if code in {"InvalidAccessKeyId", "SignatureDoesNotMatch", "AccessDenied",
                            "NoSuchBucket", "NoSuchKey", "RequestTimeTooSkewed", "InvalidToken"}:
                    safe_code = code
        print(json.dumps({"ok": False, "failed_stage": stage, "provider_code": safe_code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
