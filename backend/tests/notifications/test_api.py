"""The user's notifications (FR-74): newest first, paged, unread filter, mark one or all read,
another user's is NOT_FOUND, per-type email preferences with known types only."""

import asyncpg
from httpx import AsyncClient

from app.models.notification import NOTIFICATION_TYPES
from tests.db.conftest import make_auction
from tests.lifecycle.conftest import bearer
from tests.notifications.support import add_notification


async def test_paging_unread_and_marking(
    api: AsyncClient, make_user, notification_service, db: asyncpg.Connection
) -> None:
    user_id, token = await make_user()
    other_id, other = await make_user()
    auction = await make_auction(db)
    ids = [await add_notification(db, user_id, auction) for _ in range(5)]
    foreign = await add_notification(db, other_id, auction)
    url = "/api/v1/notifications"

    first = (await api.get(url, params={"limit": 2}, headers=bearer(token))).json()
    assert [n["id"] for n in first["items"]] == [ids[4], ids[3]]
    assert first["items"][0]["type"] == "outbid" and first["items"][0]["read"] is False
    rest = (
        await api.get(
            url, params={"limit": 10, "before": first["next_before"]}, headers=bearer(token)
        )
    ).json()
    assert [n["id"] for n in rest["items"]] == [ids[2], ids[1], ids[0]]
    assert rest["next_before"] is None

    assert (await api.post(f"{url}/{ids[0]}/read", headers=bearer(token))).status_code == 204
    assert (await api.post(f"{url}/{ids[0]}/read", headers=bearer(token))).status_code == 204
    unread = (await api.get(url, params={"unread": True}, headers=bearer(token))).json()
    assert ids[0] not in [n["id"] for n in unread["items"]]
    for target in (foreign, 999_999_999):
        refused = await api.post(f"{url}/{target}/read", headers=bearer(token))
        assert (refused.status_code, refused.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert (await api.post(f"{url}/read-all", headers=bearer(token))).json() == {"updated": 4}
    assert (await api.get(url, params={"unread": True}, headers=bearer(token))).json()[
        "items"
    ] == []
    still = (await api.get(url, params={"unread": True}, headers=bearer(other))).json()
    assert [n["id"] for n in still["items"]] == [foreign]
    assert (await api.get(url)).status_code == 401


async def test_email_preferences(api: AsyncClient, make_user, notification_service) -> None:
    _, token = await make_user()
    url = "/api/v1/notifications/preferences"
    defaults = (await api.get(url, headers=bearer(token))).json()
    assert defaults == {"email": {t: True for t in NOTIFICATION_TYPES}}
    changed = await api.put(
        url, json={"email": {"outbid": False, "ending_soon": False}}, headers=bearer(token)
    )
    assert changed.json()["email"]["outbid"] is False
    assert changed.json()["email"]["payment_due"] is True
    again = await api.put(url, json={"email": {"outbid": True}}, headers=bearer(token))
    assert again.json()["email"]["outbid"] is True
    for bad in ({"email": {"reserve_price": True}}, {"email": {}, "sms": {}}, {"email": "yes"}):
        assert (await api.put(url, json=bad, headers=bearer(token))).status_code == 422
