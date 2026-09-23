from __future__ import annotations

import pytest

from douyin_research.l0l1.research_briefs import _run_call_gate


def test_project_check_precedes_daily_reservation_for_each_paid_request() -> None:
    events: list[str] = []

    def check() -> None:
        events.append("project")
        if events.count("project") == 2:
            raise PermissionError("project paused")

    gate = _run_call_gate(
        lambda _spec: events.append("reserve"), project_check=check,
    )
    gate(object())  # type: ignore[arg-type]
    with pytest.raises(PermissionError, match="project paused"):
        gate(object())  # type: ignore[arg-type]
    assert events == ["project", "reserve", "project"]
