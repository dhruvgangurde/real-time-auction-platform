"""Category tree (FR-20, FR-75, FR-77): public reads, admin-only changes with a reason, audit,
no cycles, no deleting what is in use."""

from uuid import uuid4

import asyncpg
from httpx import AsyncClient

from tests.lifecycle.conftest import bearer


def _slug() -> str:
    return f"s-{uuid4().hex[:12]}"


async def _create(api: AsyncClient, token: str, **body) -> dict:
    r = await api.post(
        "/api/v1/categories",
        json={"name": "Cameras", "slug": _slug(), "reason": "catalogue", **body},
        headers=bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_tree_is_public_and_nested(api: AsyncClient, make_user) -> None:
    _, admin = await make_user("admin")
    root = await _create(api, admin, name="Vehicles")
    child = await _create(api, admin, name="Bicycles", parent_id=root["id"])
    tree = (await api.get("/api/v1/categories")).json()  # anonymous
    node = next(n for n in tree if n["id"] == root["id"])
    assert [c["id"] for c in node["children"]] == [child["id"]]
    assert set(node) == {"id", "name", "slug", "parent_id", "children"}


async def test_only_admins_change_categories_and_must_give_a_reason(
    api: AsyncClient, make_user
) -> None:
    """FR-20, FR-75, BR-12."""
    _, seller = await make_user("seller")
    _, admin = await make_user("admin")
    body = {"name": "Toys", "slug": _slug(), "reason": "new section"}
    r = await api.post("/api/v1/categories", json=body, headers=bearer(seller))
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    r = await api.post("/api/v1/categories", json=body | {"reason": "  "}, headers=bearer(admin))
    assert r.status_code == 422 and "reason" in r.json()["error"]["fields"]


async def test_changes_are_audited_with_actor_and_reason(
    api: AsyncClient, make_user, db: asyncpg.Connection
) -> None:
    admin_id, admin = await make_user("admin")
    created = await _create(api, admin, name="Old name")
    url = f"/api/v1/categories/{created['id']}"
    r = await api.patch(url, json={"name": "New name", "reason": "typo"}, headers=bearer(admin))
    assert r.status_code == 200 and r.json()["name"] == "New name"
    r = await api.request("DELETE", url, json={"reason": "unused"}, headers=bearer(admin))
    assert r.status_code == 204
    rows = await db.fetch(
        "SELECT action, actor_id, reason, before->>'name' AS b, after->>'name' AS a "
        "FROM audit_log WHERE entity_type = 'category' AND entity_id = $1 ORDER BY id",
        str(created["id"]),
    )
    assert [(r["action"], r["reason"], r["b"], r["a"]) for r in rows] == [
        ("category_created", "catalogue", None, "Old name"),
        ("category_updated", "typo", "Old name", "New name"),
        ("category_deleted", "unused", "New name", None),
    ]
    assert {r["actor_id"] for r in rows} == {admin_id}


async def test_reparenting_into_its_own_subtree_is_refused(api: AsyncClient, make_user) -> None:
    _, admin = await make_user("admin")
    root = await _create(api, admin)
    child = await _create(api, admin, parent_id=root["id"])
    grandchild = await _create(api, admin, parent_id=child["id"])
    for target in (root["id"], grandchild["id"]):
        r = await api.patch(
            f"/api/v1/categories/{root['id']}",
            json={"parent_id": target, "reason": "reorganise"},
            headers=bearer(admin),
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "CATEGORY_CYCLE"
    r = await api.patch(  # moving to the root is fine
        f"/api/v1/categories/{child['id']}",
        json={"parent_id": None, "reason": "promote"},
        headers=bearer(admin),
    )
    assert r.status_code == 200 and r.json()["parent_id"] is None


async def test_in_use_categories_cannot_be_deleted(api: AsyncClient, make_user, seeded) -> None:
    seller_id, _ = await make_user("seller")
    _, admin = await make_user("admin")
    parent = await _create(api, admin)
    await _create(api, admin, parent_id=parent["id"])
    used = await _create(api, admin)
    await seeded(seller_id, "draft", category_id=used["id"])
    for category in (parent, used):
        r = await api.request(
            "DELETE",
            f"/api/v1/categories/{category['id']}",
            json={"reason": "cleanup"},
            headers=bearer(admin),
        )
        assert r.status_code == 409 and r.json()["error"]["code"] == "CATEGORY_IN_USE"


async def test_duplicate_slug_and_unknown_parent(api: AsyncClient, make_user) -> None:
    _, admin = await make_user("admin")
    taken = await _create(api, admin)
    r = await api.post(
        "/api/v1/categories",
        json={"name": "Again", "slug": taken["slug"], "reason": "dup"},
        headers=bearer(admin),
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "SLUG_TAKEN"
    r = await api.post(
        "/api/v1/categories",
        json={"name": "Orphan", "slug": _slug(), "parent_id": 99_999_999, "reason": "x"},
        headers=bearer(admin),
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "CATEGORY_NOT_FOUND"
