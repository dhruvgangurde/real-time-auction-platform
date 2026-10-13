"""reserve_met in /live and bid_placed: null without a reserve, then false or true, never the
reserve itself (BR-6, brief 12.2 item 3)."""

from typing import Any

import httpx
from redis.asyncio import Redis

from app.services.live_state import state_key
from tests.realtime.client import Client

RESERVE = 10_250  # 9,000 + n x 500 never equals it, so no bid amount can match it by chance


def _numbers(value: Any) -> list[tuple[str, Any]]:
    """Every (key, scalar) pair in a JSON-like value, nested lists and dicts included."""
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in [(k, v), *_numbers(v)]]
    if isinstance(value, list):
        return [p for v in value for p in _numbers(v)]
    return []


async def test_reserve_met_in_live_and_bid_placed(
    start_instance, make_bidder, auction, db, redis: Redis, clear_snapshot_cache
) -> None:
    node = await start_instance()
    a = await auction(start_price=9_000, min_increment=500, self_outbid=False)
    plain = await auction(start_price=9_000, min_increment=500)
    await db.execute("UPDATE auctions SET reserve_price = $1 WHERE id = $2", RESERVE, a)
    _, holder_token = await make_bidder()
    _, bidder_token = await make_bidder()
    _, watcher_token = await make_bidder()
    seen: list[Any] = []
    async with httpx.AsyncClient(base_url=node.http) as http:

        async def live(auction_id: Any) -> dict[str, Any]:
            clear_snapshot_cache()  # each anonymous read reflects the bids so far
            body = (await http.get(f"/api/v1/auctions/{auction_id}/live")).json()
            seen.append(body)
            return body

        watcher = await Client.open(node.ws, watcher_token)
        await watcher.subscribe(a)
        await watcher.subscribe(plain)
        bidder = await Client.open(node.ws, bidder_token)
        await bidder.subscribe(a)
        await bidder.subscribe(plain)
        assert (await live(a))["reserve_met"] is False  # a reserve, no bids yet
        assert (await live(plain))["reserve_met"] is None  # no reserve

        # a maximum of 11,000 bids 9,000 for its holder at once (No 1)
        r = await http.post(
            f"/api/v1/auctions/{a}/proxy",
            json={"max_amount": 11_000, "request_id": "max"},
            headers={"Authorization": f"Bearer {holder_token}"},
        )
        seen.append(r.json())
        for amount in (9_500, 10_500):  # each answered by the maximum (No 2, 3 and No 4, 5)
            rid = f"b{amount}"
            await bidder.send(
                {"type": "place_bid", "auction_id": str(a), "amount": amount, "request_id": rid}
            )
            seen.append(await bidder.expect("bid_accepted", request_id=rid))
        placed = [await watcher.expect("bid_placed", sequence_no=n) for n in range(1, 6)]
        after = await live(a)

        await bidder.send(
            {"type": "place_bid", "auction_id": str(plain), "amount": 9_000, "request_id": "p1"}
        )
        plain_event = await watcher.expect("bid_placed", auction_id=str(plain))
        assert (await live(plain))["reserve_met"] is None

        # a sold auction reports what auction_closed reports (served from Postgres)
        await db.execute("UPDATE auctions SET status = 'closed_sold' WHERE id = $1", a)
        await redis.delete(state_key(a))
        closed = await live(a)
        await watcher.close()
        await bidder.close()

    assert [(e["amount"], e["bid_type"], e["reserve_met"]) for e in placed] == [
        (9_000, "automatic", False),
        (9_500, "manual", False),
        (10_000, "automatic", False),
        (10_500, "manual", True),
        (11_000, "automatic", True),
    ]
    assert after["current_price"] == 11_000 and after["reserve_met"] is True
    assert plain_event["reserve_met"] is None
    assert closed["status"] == "closed_sold" and closed["reserve_met"] is True
    for frame in [*seen, *watcher.frames, *bidder.frames]:
        for key, value in _numbers(frame):
            assert value != RESERVE, (key, frame)
            if "reserve" in key:
                assert value is None or isinstance(value, bool), (key, frame)
