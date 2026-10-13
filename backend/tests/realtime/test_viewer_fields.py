"""Per-viewer fields in /live and `subscribed` (brief 12.5): you_lead, your_max (the owner's own
maximum only, FR-51, BR-7) and your_result after close. Absent for anonymous viewers and never in
a room broadcast."""

from typing import Any

import httpx
from redis.asyncio import Redis

from app.services.closing import Closer
from app.services.ledger import persist_outbox
from app.services.live_state import state_key
from tests.bidding.conftest import redis_now_ms
from tests.realtime.client import Client

MAXIMUM = 12_000  # never placed as a bid in this test, so it can only leak as a maximum
OWN = ("you_lead", "your_max", "your_result")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_own_fields_only_for_their_owner(
    start_instance, make_bidder, new_session, auction, db, redis: Redis, sessions
) -> None:
    node = await start_instance()
    a = await auction(start_price=10_000, min_increment=500, self_outbid=False)
    owner, owner_token = await make_bidder()
    other, other_token = await make_bidder()
    seller, seller_token = await make_bidder()
    admin, _ = await make_bidder()
    await db.execute("UPDATE auctions SET seller_id = $1 WHERE id = $2", seller, a)
    await db.execute("INSERT INTO user_roles (user_id, role) VALUES ($1, 'admin')", admin)
    admin_token = (await new_session(admin)).access_token  # roles are read at login
    _, spy_token = await make_bidder()
    tokens = {"owner": owner_token, "other": other_token, "seller": seller_token}
    tokens["admin"] = admin_token

    async with httpx.AsyncClient(base_url=node.http) as http:

        async def live(token: str | None) -> dict[str, Any]:
            r = await http.get(f"/api/v1/auctions/{a}/live", headers=_auth(token) if token else {})
            assert r.status_code == 200, r.text
            return r.json()

        spy = await Client.open(node.ws, spy_token)  # records every room frame of the test
        await spy.subscribe(a)
        r = await http.post(  # the maximum bids 10,000 for its owner at once (No 1)
            f"/api/v1/auctions/{a}/proxy",
            json={"max_amount": MAXIMUM, "request_id": "max"},
            headers=_auth(owner_token),
        )
        assert r.status_code == 200, r.text
        r = await http.post(  # answered by the maximum at 11,000 (No 2, 3)
            f"/api/v1/auctions/{a}/bids",
            json={"amount": 10_500, "request_id": "b1"},
            headers=_auth(other_token),
        )
        assert r.status_code == 200, r.text
        await spy.expect("bid_placed", sequence_no=3)

        during = {who: await live(token) for who, token in tokens.items()}
        anonymous = await live(None)
        subscribed = {}
        for who in ("owner", "other"):
            client = await Client.open(node.ws, tokens[who])
            subscribed[who] = (await client.subscribe(a))["snapshot"]
            await client.close()

        # end it and run the close task (FR-57): the scheduler's live -> closing step, by hand
        await persist_outbox(a, redis, sessions)
        now = await redis_now_ms(redis)
        await redis.hset(state_key(a), mapping={"end_time_ms": now - 1, "status": "closing"})
        await db.execute(
            "UPDATE auctions SET status = 'closing', end_time = now() - interval '1 second' "
            "WHERE id = $1",
            a,
        )
        await Closer(redis, sessions).close(a)
        after = {who: await live(token) for who, token in tokens.items()}
        closed_anonymous = await live(None)
        await spy.close()

    assert during["owner"]["sequence_no"] == 3
    assert {k: during["owner"][k] for k in OWN} == {
        "you_lead": True,
        "your_max": MAXIMUM,
        "your_result": None,
    }
    for who in ("other", "seller", "admin"):
        assert {k: during[who][k] for k in OWN} == {
            "you_lead": False,
            "your_max": None,
            "your_result": None,
        }, who
    assert subscribed["owner"]["your_max"] == MAXIMUM and subscribed["owner"]["you_lead"] is True
    assert subscribed["other"]["your_max"] is None and subscribed["other"]["you_lead"] is False

    assert after["owner"]["status"] == "closed_sold"
    assert {k: after["owner"][k] for k in OWN} == {
        "you_lead": True,
        "your_max": None,  # maxima are deactivated at close (FR-51)
        "your_result": "won",
    }
    assert after["other"]["your_result"] == "lost"
    assert after["seller"]["your_result"] is None and after["admin"]["your_result"] is None

    for body in (anonymous, closed_anonymous):
        assert not set(OWN) & set(body)
    for frame in spy.frames:  # room broadcasts carry no per-viewer field and no maximum
        assert not set(OWN) & set(frame), frame
        assert frame.get("type") == "subscribed" or MAXIMUM not in _values(frame), frame
    assert str(owner) not in str(spy.frames) and str(other) not in str(spy.frames)


def _values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [x for v in value.values() for x in _values(v)]
    if isinstance(value, list):
        return [x for v in value for x in _values(v)]
    return [value]
