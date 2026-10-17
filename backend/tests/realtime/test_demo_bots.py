"""Demo bots bid through the real socket path (brief 12.3, 12.4): a minted session's token
authenticates, the room is subscribed, and two bots on two instances racing for the same minimum
get one acceptance and one real BID_TOO_LOW, after which the loser's view has moved on."""

import asyncio
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.demo_accounts import DemoAccounts
from app.services.denylist import Denylist
from app.workers.demo_bots import BotSocket
from tests.auth.conftest import FakeClock
from tests.bidding.conftest import AuctionFactory
from tests.realtime.conftest import Instance


async def test_two_bots_race_for_one_minimum_on_two_instances(
    instances: tuple[Instance, Instance],
    auction: AuctionFactory,
    sessions: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    redis: Redis,
) -> None:
    lot = await auction(start_price=10_000, min_increment=500)
    accounts = DemoAccounts(sessions, clock, Denylist(redis, None))
    cast = await accounts.ensure(bots=2)
    signed = await accounts.sign_in(cast.bots)
    bots = [
        BotSocket(node.ws, signed[bot.id][1], bot.id, lot)
        for node, bot in zip(instances, cast.bots, strict=True)
    ]
    try:
        await asyncio.gather(*(b.open() for b in bots))
        assert [b.min_next for b in bots] == [10_000, 10_000]
        outcomes = await asyncio.gather(*(b.bid() for b in bots))
        assert sorted(outcomes) == ["BID_TOO_LOW", "accepted"]
        loser = bots[outcomes.index("BID_TOO_LOW")]
        winner = bots[outcomes.index("accepted")]
        assert winner.leading and not loser.leading
        assert await winner.bid() is None  # a bot that leads holds back
        assert loser.min_next == 10_500  # from the rejection, before any room event
        assert await loser.bid() == "accepted"
        for _ in range(100):  # the room event reaches the other instance's socket
            if not winner.leading:
                break
            await asyncio.sleep(0.02)
        assert not winner.leading and winner.min_next == 11_000
        acks = sorted(a for b in bots for a in b.acks)
        assert [(seq, amount) for seq, _, amount in acks] == [(1, 10_000), (2, 10_500)]
        assert {who for _, who, _ in acks} == {str(b.user_id) for b in bots}
    finally:
        await asyncio.gather(*(b.close() for b in bots))
        await accounts.sign_out_all()


async def test_a_bot_retries_a_subscribe_answered_service_unavailable(
    instances: tuple[Instance, Instance],
    auction: AuctionFactory,
    sessions: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A subscribe that lands while the lot goes live (Postgres says live, the live state is not
    installed yet) is answered SERVICE_UNAVAILABLE, which the client contract says to retry; the
    bot does, instead of giving up its place in the run (seen as 47 of 50 bots connected)."""
    from app.api import ws
    from app.core.errors import AppError

    real, refusals = ws.live_snapshot, []

    async def going_live(*args: Any, **kwargs: Any) -> Any:
        if len(refusals) < 2:  # the first two subscribes meet the go-live window
            refusals.append(1)
            raise AppError("SERVICE_UNAVAILABLE", "live state is being rebuilt", 503)
        return await real(*args, **kwargs)

    monkeypatch.setattr(ws, "live_snapshot", going_live)
    lot = await auction(start_price=10_000, min_increment=500)
    accounts = DemoAccounts(sessions, clock, Denylist(redis, None))
    [bot_account] = (await accounts.ensure(bots=1)).bots
    signed = await accounts.sign_in((bot_account,))
    bot = BotSocket(instances[0].ws, signed[bot_account.id][1], bot_account.id, lot)
    try:
        await bot.open()
        assert len(refusals) == 2 and bot.min_next == 10_000
        assert await bot.bid() == "accepted"
    finally:
        await bot.close()
        await accounts.sign_out_all()
