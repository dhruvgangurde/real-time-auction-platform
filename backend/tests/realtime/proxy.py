"""A TCP proxy a test can cut: the instance behind it loses Postgres or Redis mid-connection
while the shared test resources stay up for every other test."""

import asyncio
import contextlib


class Proxy:
    def __init__(self, target_host: str, target_port: int) -> None:
        self.target = (target_host, target_port)
        self.port = 0
        self._server: asyncio.Server | None = None
        self._writers: list[asyncio.StreamWriter] = []

    async def start(self) -> "Proxy":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        up_reader, up_writer = await asyncio.open_connection(*self.target)
        self._writers += [writer, up_writer]

        async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            with contextlib.suppress(Exception):
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            dst.close()

        await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))

    def cut(self) -> None:
        """Refuse new connections and reset every open one."""
        if self._server is not None:
            self._server.close()
        for writer in self._writers:
            writer.transport.abort()
