"""Object storage for auction images (SI-5, NFR-24, D10).

A small interface over a local directory (the `media` named volume), served read-only under
/media. Keys are relative paths chosen by the server, never by the client. Swapping in an
external object store means another class with the same three methods.
"""

import asyncio
import os
from pathlib import Path

MEDIA_URL_PREFIX = "/media"


class LocalStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"key outside the storage root: {key!r}")
        return path

    def _write(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)  # readers never see a half-written file

    async def save(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(self._write, key, data)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, True)

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    @staticmethod
    def url(key: str) -> str:
        return f"{MEDIA_URL_PREFIX}/{key}"
