"""NFR-6, OR-5: listing, search and detail respond within 500 ms at the 95th percentile with
2,000 auctions in the catalogue. Run with -s to see the numbers and the query plans:

    docker compose exec api pytest tests/discovery/test_nfr6.py -s
"""

import random
import statistics
import time
from uuid import UUID

import asyncpg
from httpx import AsyncClient

from tests.db.conftest import make_user

AUCTIONS = 2_000
REQUESTS = 100
WORDS = ["vintage", "brass", "oak", "silver", "camera", "watch", "lamp", "chair"]


async def _seed(db: asyncpg.Connection, categories: list[int]) -> list[UUID]:
    seller = await make_user(db)
    rows = await db.fetch(
        """
        INSERT INTO auctions (seller_id, category_id, title, description, starting_price,
            min_increment, start_time, end_time, original_end_time, status, created_at)
        SELECT $1,
               ($2::bigint[])[1 + g % array_length($2::bigint[], 1)],
               'Lot ' || g || ' ' || ($3::text[])[1 + g % 8] || ' ' || ($3::text[])[1 + g % 5],
               'A ' || ($3::text[])[1 + (g * 7) % 8] || ' piece, number ' || g,
               1000 + (g % 500) * 100,
               100,
               now() + interval '1 hour',
               now() + interval '1 hour' + (1 + g % 72) * interval '1 hour',
               now() + interval '1 hour' + (1 + g % 72) * interval '1 hour',
               (ARRAY['scheduled', 'scheduled', 'closed_sold', 'closed_no_sale', 'draft'])
                   [1 + g % 5],
               now() - g * interval '1 second'
        FROM generate_series(1, $4::int) AS g
        RETURNING id, status
        """,
        seller,
        categories,
        WORDS,
        AUCTIONS,
    )
    return [r["id"] for r in rows if r["status"] != "draft"]


def _stats(name: str, samples: list[float]) -> float:
    p50 = statistics.median(samples)
    p95 = statistics.quantiles(samples, n=20)[18]
    print(f"[NFR-6] {name:<7} n={len(samples)}  p50={p50:6.1f} ms  p95={p95:6.1f} ms")
    return p95


async def _timed(api: AsyncClient, url: str, params: dict | None = None) -> float:
    started = time.perf_counter()
    r = await api.get(url, params=params)
    elapsed = (time.perf_counter() - started) * 1000
    assert r.status_code == 200, r.text
    return elapsed


async def test_listing_search_and_detail_p95_under_500_ms(
    api: AsyncClient, db: asyncpg.Connection, category
) -> None:
    root = await category(name="Catalogue")
    leaves = [await category(parent_id=root) for _ in range(6)]
    ids = await _seed(db, leaves)
    await db.execute("ANALYZE auctions")
    rng = random.Random(7)

    lists = [await _timed(api, "/api/v1/auctions", {"page": 1 + i % 5}) for i in range(REQUESTS)]
    searches = [
        await _timed(api, "/api/v1/auctions", {"q": rng.choice(WORDS), "category": root})
        for _ in range(REQUESTS)
    ]
    details = [await _timed(api, f"/api/v1/auctions/{rng.choice(ids)}") for _ in range(REQUESTS)]
    p95 = [_stats("list", lists), _stats("search", searches), _stats("detail", details)]

    for name, sql, args in (
        (
            "list",
            "SELECT id FROM auctions WHERE status IN ('scheduled', 'live', 'closing') "
            "ORDER BY created_at DESC, id DESC LIMIT 21",
            (),
        ),
        (
            "search",
            "SELECT id FROM auctions WHERE status IN ('scheduled', 'live', 'closing') "
            "AND category_id = ANY($1) AND search_vector @@ websearch_to_tsquery('simple', $2) "
            "ORDER BY ts_rank(search_vector, websearch_to_tsquery('simple', $2)) DESC, "
            "created_at DESC, id DESC LIMIT 21",
            (leaves, "brass"),
        ),
    ):
        plan = await db.fetch(f"EXPLAIN ANALYZE {sql}", *args)
        print(f"[OR-5] EXPLAIN {name}:\n  " + "\n  ".join(r[0] for r in plan))

    assert max(p95) < 500, p95
