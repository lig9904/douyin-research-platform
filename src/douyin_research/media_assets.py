"""Video-bound metadata for confirmed private objects, never signed URLs."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .media_storage import PrivateS3MediaStorage, StoredMediaObject


@dataclass(frozen=True, slots=True)
class MediaAssetReference:
    id: UUID
    video_id: UUID
    kind: str
    storage_location: str
    bucket: str
    object_key: str
    content_sha256: str
    size_bytes: int
    content_type: str
    source_response_id: int | None
    parent_asset_id: UUID | None


_COLUMNS = (
    "id, video_id, kind, storage_location, bucket, object_key, content_sha256, "
    "size_bytes, content_type, source_response_id, parent_asset_id"
)


class MediaAssetStore:
    """Called only after upload_file has confirmed object metadata.

    This repository is not a network upload or an authorization boundary.
    Trusted workers choose storage location; UI callers only request an asset
    by its video-bound ID through an authenticated application backend.
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def record(
        self,
        *,
        video_id: UUID | str,
        kind: str,
        storage_location: str,
        bucket: str,
        stored: StoredMediaObject,
        source_response_id: int | None = None,
        parent_asset_id: UUID | str | None = None,
    ) -> MediaAssetReference:
        video_id = UUID(str(video_id))
        parent = UUID(str(parent_asset_id)) if parent_asset_id is not None else None
        if kind not in {"video", "audio", "image"}:
            raise ValueError("invalid media kind")
        if not storage_location or not bucket or any(
            ch in storage_location + bucket for ch in "/?#@"
        ):
            raise ValueError("storage location and bucket must be identifiers")
        if (
            stored.key != PrivateS3MediaStorage.object_key(stored.sha256)
            or stored.sha256 != stored.sha256.lower()
            or type(stored.size) is not int
            or stored.size <= 0
            or not stored.content_type.startswith(kind + "/")
        ):
            raise ValueError("invalid confirmed media metadata")
        if source_response_id is not None and (
            type(source_response_id) is not int or source_response_id <= 0
        ):
            raise ValueError("invalid source response identity")

        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                f"""insert into media_asset (
                  video_id, kind, storage_location, bucket, object_key,
                  content_sha256, size_bytes, content_type, source_response_id,
                  parent_asset_id
                ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (video_id, kind, storage_location, bucket, content_sha256)
                do nothing returning {_COLUMNS}""",
                (video_id, kind, storage_location, bucket, stored.key, stored.sha256,
                 stored.size, stored.content_type, source_response_id, parent),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    f"""select {_COLUMNS} from media_asset
                    where video_id=%s and kind=%s and storage_location=%s
                      and bucket=%s and content_sha256=%s""",
                    (video_id, kind, storage_location, bucket, stored.sha256),
                ).fetchone()
                if row is None or any((
                    row["object_key"] != stored.key,
                    row["size_bytes"] != stored.size,
                    row["content_type"] != stored.content_type,
                    row["parent_asset_id"] != parent,
                )):
                    raise ValueError("existing media metadata does not match")
                # A later refresh may produce the same content in another
                # response. Keep the first object's immutable source lineage.
            return MediaAssetReference(**row)

    def get(self, video_id: UUID | str, asset_id: UUID | str) -> MediaAssetReference | None:
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                f"select {_COLUMNS} from media_asset where id=%s and video_id=%s",
                (UUID(str(asset_id)), UUID(str(video_id))),
            ).fetchone()
        return MediaAssetReference(**row) if row is not None else None
