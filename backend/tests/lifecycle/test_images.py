"""Image intake (FR-13, NFR-24, SI-5, D10): decoded type, size and pixel caps, re-encoding,
random names, read-only serving, no orphan files."""

import io
import struct
import zlib
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.storage import LocalStorage
from app.services.auctions import AuctionService
from app.services.auth import CurrentUser
from tests.lifecycle.conftest import bearer, png


def _jpeg_with_exif() -> bytes:
    exif = Image.Exif()
    exif[0x010E] = "secret location note"  # ImageDescription
    exif[0x0112] = 6  # Orientation: rotate 90 degrees
    out = io.BytesIO()
    Image.new("RGB", (40, 20), (10, 120, 200)).save(out, format="JPEG", exif=exif.tobytes())
    return out.getvalue()


def _png_header_claiming(width: int, height: int) -> bytes:
    """A PNG whose header claims a huge image: a decompression bomb by its size alone."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00" * 64))
        + chunk(b"IEND", b"")
    )


def _files(storage: LocalStorage) -> list[Path]:
    return [p for p in storage.root.rglob("*") if p.is_file()] if storage.root.exists() else []


async def _upload(api: AsyncClient, token: str, auction_id: UUID, data: bytes, **headers: str):
    return await api.post(
        f"/api/v1/auctions/{auction_id}/images",
        content=data,
        headers=bearer(token) | headers,
    )


async def test_upload_is_reencoded_without_metadata_and_served(
    api: AsyncClient, make_user, seeded, storage: LocalStorage
) -> None:
    """NFR-24: EXIF (and anything else in the upload) is gone; orientation is applied; the
    stored name is random; the file is served under /media."""
    seller_id, token = await make_user("seller")
    auction_id = await seeded(seller_id, "draft")
    original = _jpeg_with_exif()
    r = await _upload(api, token, auction_id, original, **{"Content-Type": "text/plain"})
    assert r.status_code == 201, r.text
    url = r.json()["url"]
    assert url.startswith(f"/media/auctions/{auction_id}/") and url.endswith(".jpg")
    [stored] = _files(storage)
    data = stored.read_bytes()
    assert b"secret location note" not in data
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG"
        assert dict(image.getexif()) == {}
        assert image.size == (20, 40)  # rotated by the orientation tag, then the tag dropped
    served = await api.get(url)
    assert served.status_code == 200 and served.content == data
    assert (await api.delete(url)).status_code in (404, 405)  # read-only


@pytest.mark.parametrize(
    "data",
    [
        b"<html><script>alert(1)</script></html>",  # disguised as an image by its headers
        b"GIF89a" + b"\x00" * 64,  # a real image type that is not allowed
        png()[:40],  # truncated
    ],
)
async def test_anything_that_does_not_decode_as_jpeg_png_or_webp_is_refused(
    api: AsyncClient, make_user, seeded, storage: LocalStorage, data: bytes
) -> None:
    seller_id, token = await make_user("seller")
    auction_id = await seeded(seller_id, "draft")
    r = await _upload(api, token, auction_id, data, **{"Content-Type": "image/jpeg"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNSUPPORTED_IMAGE"
    assert _files(storage) == []


async def test_gif_with_a_valid_body_is_refused(api: AsyncClient, make_user, seeded) -> None:
    seller_id, token = await make_user("seller")
    auction_id = await seeded(seller_id, "draft")
    out = io.BytesIO()
    Image.new("RGB", (4, 4)).save(out, format="GIF")
    r = await _upload(api, token, auction_id, out.getvalue())
    assert r.json()["error"]["code"] == "UNSUPPORTED_IMAGE"


async def test_size_cap_is_enforced_while_streaming(api: AsyncClient, make_user, seeded) -> None:
    """FR-13: at most 5 MB; one byte more is refused."""
    seller_id, token = await make_user("seller")
    auction_id = await seeded(seller_id, "draft")
    r = await _upload(api, token, auction_id, b"\x00" * (settings.image_max_bytes + 1))
    assert r.status_code == 413 and r.json()["error"]["code"] == "IMAGE_TOO_LARGE"


async def test_decompression_bomb_header_is_refused(api: AsyncClient, make_user, seeded) -> None:
    """NFR-24: a PNG header claiming 50,000 x 50,000 pixels is refused before decoding."""
    seller_id, token = await make_user("seller")
    auction_id = await seeded(seller_id, "draft")
    r = await _upload(api, token, auction_id, _png_header_claiming(50_000, 50_000))
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNSUPPORTED_IMAGE"


async def test_pixel_cap(
    api: AsyncClient, make_user, seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "image_max_pixels", 100)
    seller_id, token = await make_user("seller")
    auction_id = await seeded(seller_id, "draft")
    r = await _upload(api, token, auction_id, png((20, 20)))
    assert r.json()["error"]["code"] == "UNSUPPORTED_IMAGE"


async def test_non_owner_is_forbidden_and_the_count_is_capped(
    api: AsyncClient, make_user, seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id, owner = await make_user("seller")
    _, other = await make_user("seller")
    auction_id = await seeded(owner_id, "scheduled")
    r = await _upload(api, other, auction_id, png())
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    monkeypatch.setattr(settings, "images_per_auction", 1)
    assert (await _upload(api, owner, auction_id, png())).status_code == 201
    r = await _upload(api, owner, auction_id, png())
    assert r.status_code == 409 and r.json()["error"]["code"] == "TOO_MANY_IMAGES"


async def test_remove_deletes_the_file(
    api: AsyncClient, make_user, seeded, storage: LocalStorage, db: asyncpg.Connection
) -> None:
    seller_id, token = await make_user("seller")
    auction_id = await seeded(seller_id, "draft")
    image_id = (await _upload(api, token, auction_id, png())).json()["id"]
    assert len(_files(storage)) == 1
    r = await api.delete(f"/api/v1/auctions/{auction_id}/images/{image_id}", headers=bearer(token))
    assert r.status_code == 204 and _files(storage) == []
    actions = await db.fetch(
        "SELECT action FROM audit_log WHERE entity_id = $1 ORDER BY id", str(auction_id)
    )
    assert [a["action"] for a in actions] == ["image_added", "image_removed"]


async def test_failed_commit_leaves_no_file(
    auction_service: AuctionService,
    make_user,
    seeded,
    storage: LocalStorage,
    db: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D10: the file is saved before the commit; if the commit fails it is deleted."""
    seller_id, _ = await make_user("seller")
    auction_id = await seeded(seller_id, "draft")

    async def failing_commit(self: AsyncSession) -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)

    async def body():
        yield png()

    user = CurrentUser(seller_id, ("bidder", "seller"), seller_id)
    with pytest.raises(RuntimeError, match="commit failed"):
        await auction_service.add_image(user, auction_id, body())
    assert _files(storage) == []
    assert (
        await db.fetchval("SELECT count(*) FROM auction_images WHERE auction_id = $1", auction_id)
        == 0
    )
