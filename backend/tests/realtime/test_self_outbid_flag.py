"""self_outbid_blocked in /live: the FR-38 policy as the auction enforces it, so the client
disables bidding for the leader only when the policy is on (brief 12.6 B1)."""

import httpx
import pytest

from app.core.config import settings


async def test_live_reports_the_self_outbid_policy(
    start_instance, auction, monkeypatch: pytest.MonkeyPatch
) -> None:
    node = await start_instance()
    on = await auction(self_outbid=True)
    off = await auction(self_outbid=False)
    monkeypatch.setattr(settings, "self_outbid_policy_enabled", False)
    scheduled = await auction(status="scheduled", starts_in_ms=3_600_000, ends_in_ms=7_200_000)
    async with httpx.AsyncClient(base_url=node.http) as http:

        async def flag(auction_id: object) -> bool:
            return (await http.get(f"/api/v1/auctions/{auction_id}/live")).json()[
                "self_outbid_blocked"
            ]

        # live: the value copied into each auction's live state, which place_bid.lua enforces
        assert await flag(on) is True
        assert await flag(off) is False
        # without live state: the platform setting the auction will copy at go-live
        assert await flag(scheduled) is False
        monkeypatch.setattr(settings, "self_outbid_policy_enabled", True)
        assert await flag(scheduled) is True
