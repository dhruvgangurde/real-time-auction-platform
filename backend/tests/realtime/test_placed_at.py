"""placed_at_ms: the arbitration time from Redis TIME (DC-3) in bid_accepted, bid_placed and the
/live recent_bids, equal to the ledger's created_at for the same sequence (brief 12.2 item 4)."""

from datetime import UTC, datetime

import httpx
from redis.asyncio import Redis

from app.services.ledger import persist_outbox
from tests.realtime.client import Client
from tests.realtime.conftest import Instance


def _ms(dt: datetime) -> int:
    return int(dt.astimezone(UTC).timestamp() * 1000)


async def test_placed_at_matches_ledger_created_at(
    start_instance, make_bidder, auction, redis: Redis, sessions, db
) -> None:
    node: Instance = await start_instance()
    a = await auction(start_price=10_000, min_increment=500)
    holder, holder_token = await make_bidder()
    _, bidder_token = await make_bidder()
    _, watcher_token = await make_bidder()
    async with httpx.AsyncClient(base_url=node.http) as http:
        watcher = await Client.open(node.ws, watcher_token)
        await watcher.subscribe(a)
        # a maximum bids for its holder at once (No 1), then answers the manual bid (No 2 -> 3)
        r = await http.post(
            f"/api/v1/auctions/{a}/proxy",
            json={"max_amount": 12_000, "request_id": "max"},
            headers={"Authorization": f"Bearer {holder_token}"},
        )
        assert r.status_code == 200, r.text
        bidder = await Client.open(node.ws, bidder_token)
        await bidder.subscribe(a)
        await bidder.send(
            {"type": "place_bid", "auction_id": str(a), "amount": 10_500, "request_id": "b1"}
        )
        ack = await bidder.expect("bid_accepted", request_id="b1")
        placed = {seq: await watcher.expect("bid_placed", sequence_no=seq) for seq in (1, 2, 3)}
        unpersisted = (await http.get(f"/api/v1/auctions/{a}/live")).json()["recent_bids"]

        await persist_outbox(a, redis, sessions)
        rows = await db.fetch("SELECT sequence_no, created_at FROM bids WHERE auction_id = $1", a)
        ledger = {r["sequence_no"]: _ms(r["created_at"]) for r in rows}
        persisted = (await http.get(f"/api/v1/auctions/{a}/live")).json()["recent_bids"]
        await watcher.close()
        await bidder.close()

    assert sorted(ledger) == [1, 2, 3]
    assert ack["sequence_no"] == 2 and ack["placed_at_ms"] == ledger[2]
    assert {seq: e["placed_at_ms"] for seq, e in placed.items()} == ledger
    assert placed[3]["bid_type"] == "automatic"
    assert ledger[3] == ledger[2]  # an automatic bid shares its trigger's Redis TIME
    assert {b["sequence_no"]: b["placed_at_ms"] for b in unpersisted} == ledger  # outbox
    assert {b["sequence_no"]: b["placed_at_ms"] for b in persisted} == ledger  # ledger rows
    assert str(holder) not in str(placed)  # pseudonyms only
