"""PostgreSQL-backed daily request/cost budget gate."""

from __future__ import annotations

from datetime import date
from typing import Callable

import psycopg

from douyin_research.providers.endpoints import EndpointSpec
from douyin_research.providers.errors import ProviderBudgetError


class DailyBudgetGuard:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def make_before_external_call(
        self,
        *,
        provider: str,
        budget_key: str,
    ) -> Callable[[EndpointSpec], None]:
        """Build a provider hook that reserves one uncached request at a time."""

        def reserve(spec: EndpointSpec) -> None:
            self.acquire(
                provider=provider,
                budget_key=budget_key,
                requests=1,
                estimated_cost=float(spec.unit_cost_usd or 0.0),
            )

        return reserve

    def configure(self, *, provider: str, budget_key: str, max_requests: int | None,
                  max_cost: float | None, budget_date: date | None = None,
                  cost_currency: str = "USD") -> None:
        budget_date = budget_date or date.today()
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into daily_budget(
                  budget_date, provider, budget_key, max_cost, max_requests, cost_currency
                ) values (%s,%s,%s,%s,%s,%s)
                on conflict(budget_date, provider, budget_key)
                do update set
                  max_cost=excluded.max_cost,
                  max_requests=excluded.max_requests,
                  cost_currency=excluded.cost_currency
                """,
                (
                    budget_date,
                    provider,
                    budget_key,
                    max_cost,
                    max_requests,
                    cost_currency,
                ),
            )
            conn.commit()

    def acquire(self, *, provider: str, budget_key: str, requests: int = 1,
                estimated_cost: float = 0.0, budget_date: date | None = None) -> None:
        if requests < 0 or estimated_cost < 0:
            raise ValueError("budget reservation cannot be negative")
        budget_date = budget_date or date.today()
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                select max_cost, max_requests, spent_cost, used_requests
                from daily_budget
                where budget_date=%s and provider=%s and budget_key=%s
                for update
                """,
                (budget_date, provider, budget_key),
            )
            row = cur.fetchone()
            if row is None:
                raise ProviderBudgetError(
                    f"budget is not configured: {provider}/{budget_key}/{budget_date}"
                )
            max_cost, max_requests, spent_cost, used_requests = row
            next_requests = used_requests + requests
            next_cost = float(spent_cost) + float(estimated_cost)
            if max_requests is not None and next_requests > max_requests:
                raise ProviderBudgetError(f"request budget exceeded: {next_requests}>{max_requests}")
            if max_cost is not None and next_cost > float(max_cost):
                raise ProviderBudgetError(
                    f"cost budget exceeded: {next_cost:.6f}>{float(max_cost):.6f}"
                )
            cur.execute(
                """
                update daily_budget
                set used_requests=%s, spent_cost=%s, updated_at=now()
                where budget_date=%s and provider=%s and budget_key=%s
                """,
                (next_requests, next_cost, budget_date, provider, budget_key),
            )
            conn.commit()

    def refund(self, *, provider: str, budget_key: str, requests: int = 1,
               estimated_cost: float = 0.0, budget_date: date | None = None) -> None:
        budget_date = budget_date or date.today()
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                update daily_budget
                set used_requests=greatest(0, used_requests-%s),
                    spent_cost=greatest(0, spent_cost-%s),
                    updated_at=now()
                where budget_date=%s and provider=%s and budget_key=%s
                """,
                (requests, estimated_cost, budget_date, provider, budget_key),
            )
            conn.commit()
