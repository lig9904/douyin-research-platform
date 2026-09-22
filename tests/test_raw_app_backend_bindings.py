from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend"


def test_every_raw_app_backend_is_explicitly_inline() -> None:
    metadata_files = sorted(BACKEND.glob("*.yaml"))
    assert metadata_files
    for metadata in metadata_files:
        source = metadata.read_text(encoding="utf-8")
        assert source.startswith("type: inline\n"), metadata


def test_database_backends_bind_the_research_resource() -> None:
    for name in ("get_daily_briefing.yaml", "get_video_raw_records.yaml"):
        source = (BACKEND / name).read_text(encoding="utf-8")
        assert "fields:\n  db:\n    type: static\n" in source, name
        assert "value: $res:f/content_research/research_db" in source, name
