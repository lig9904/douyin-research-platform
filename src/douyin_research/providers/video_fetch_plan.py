"""Pure, base-tariff routing. Planning never reads secrets or calls a provider."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

from .endpoints import get_endpoint


@dataclass(frozen=True, slots=True)
class VideoFetchRequest:
    endpoint_key: str
    video_ids: tuple[str, ...]

    @property
    def estimated_cost_usd(self) -> Decimal:
        price = get_endpoint(self.endpoint_key).unit_cost_usd
        if price is None:
            raise ValueError(f"missing quote: {self.endpoint_key}")
        return Decimal(str(price))

    def kwargs(self) -> dict[str, Any]:
        if self.endpoint_key == "douyin.app.one_video":
            return {"aweme_id": self.video_ids[0]}
        if self.endpoint_key.endswith("statistics"):
            return {"aweme_ids": ",".join(self.video_ids)}
        return {"body": list(self.video_ids)}


def plan_video_fetches(
    video_ids: Iterable[str], *, purpose: str = "detail", strategy: str = "cost_aware"
) -> tuple[VideoFetchRequest, ...]:
    """Deduplicate before pricing; preserve order. Cheapest base cost, then fewer calls.

    No unverified discount is applied. Statistics cannot replace content details.
    Existing batch50 callers can explicitly retain their verified request shape.
    """
    if purpose not in {"detail", "statistics"}:
        raise ValueError("purpose must be detail or statistics")
    if strategy not in {"cost_aware", "batch50"}:
        raise ValueError("strategy must be cost_aware or batch50")
    ids: list[str] = []
    seen: set[str] = set()
    for value in video_ids:
        if not isinstance(value, str) or not value.strip() or "," in value:
            raise ValueError("video IDs must be non-empty strings without commas")
        value = value.strip()
        if value not in seen:
            seen.add(value)
            ids.append(value)
    if not ids:
        return ()
    if purpose == "statistics":
        options = ((50, "douyin.app.multi_video_statistics"), (2, "douyin.app.video_statistics"))
    elif strategy == "batch50":
        options = ((50, "douyin.app.multi_video_v2"),)
    else:
        options = ((50, "douyin.app.multi_video_v2"), (10, "douyin.app.multi_video"),
                   (1, "douyin.app.one_video"))
    # Capacities have linear full-batch prices. Optimize the final <=50 IDs;
    # whole 50-ID groups are optimal at this fixed, reviewed base tariff.
    result: list[VideoFetchRequest] = []
    full_key = options[0][1]
    cursor = 0
    while len(ids) - cursor > 50:
        result.append(VideoFetchRequest(full_key, tuple(ids[cursor:cursor + 50])))
        cursor += 50
    tail = ids[cursor:]
    best: list[tuple[Decimal, int, list[tuple[int, str]]]] = [(Decimal(0), 0, [])]
    for count in range(1, len(tail) + 1):
        candidates = []
        for capacity, key in options:
            price = get_endpoint(key).unit_cost_usd
            if price is None:
                raise ValueError(f"missing quote: {key}")
            taken = min(count, capacity)
            previous = best[count - taken]
            candidates.append((previous[0] + Decimal(str(price)), previous[1] + 1,
                               previous[2] + [(taken, key)]))
        best.append(min(candidates, key=lambda candidate: candidate[:2]))
    offset = 0
    for count, key in best[len(tail)][2]:
        result.append(VideoFetchRequest(key, tuple(tail[offset:offset + count])))
        offset += count
    return tuple(result)
