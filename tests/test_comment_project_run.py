from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from douyin_research.l0l1.comments import CommentCollector


def test_project_comment_run_is_attributed_at_creation_and_costed_in_usd() -> None:
    project_id = uuid4()
    run_id = uuid4()
    created: list[tuple[tuple, dict]] = []
    finished: list[tuple[tuple, dict]] = []

    class RunStore:
        def create_run(self, *args, **kwargs):
            created.append((args, kwargs))
            return run_id

        def finish_run(self, *args, **kwargs):
            finished.append((args, kwargs))

    class Provider:
        platform_name = "douyin"
        provider_name = "tikhub"

        def fetch_comments(self, *_args, **_kwargs):
            return SimpleNamespace(
                items=[object()],
                pagination={"pages_fetched": 1, "cached_pages": 0, "external_pages": 1,
                            "has_more": 1, "cursor": 20},
                cached=False,
                request_fingerprint="request-fingerprint",
            )

    class EvidenceStore:
        def ingest(self, *_args, **_kwargs):
            return SimpleNamespace(
                observations_inserted=1,
                new_comments=1,
                duplicate_observations=0,
            )

    result = CommentCollector(
        provider=Provider(), evidence_store=EvidenceStore(), run_store=RunStore()
    ).collect("7684244242625316517", project_id=project_id, cursor="0")

    assert result.run_id == run_id
    assert created[0][1]["project_id"] == project_id
    assert finished[0][1]["cost_currency"] == "USD"
    assert finished[0][1]["summary"]["api_cost_basis"] == "estimated"
    assert finished[0][1]["summary"]["unknown_cost_calls"] == 0
    assert result.next_cursor == "20"
    assert result.has_more is True
