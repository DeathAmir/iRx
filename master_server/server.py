#!/usr/bin/env python3
import asyncio
import hashlib
import hmac
import ipaddress
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

BIND = os.getenv("IRX_MASTER_BIND", "0.0.0.0")
PORT = int(os.getenv("IRX_MASTER_PORT", "7575"))
PUBLIC_HOST = os.getenv("IRX_MASTER_PUBLIC_HOST", "irautox.ir")
SHARED_SECRET = os.getenv("IRX_MASTER_SHARED_SECRET", "").encode()
MAX_LINE = 4096
MAX_CLIENTS = int(os.getenv("IRX_MASTER_MAX_CLIENTS", "2048"))
MAX_SERVERS = int(os.getenv("IRX_MASTER_MAX_SERVERS", "10000"))
MAX_CONN_PER_IP = int(os.getenv("IRX_MASTER_MAX_CONN_PER_IP", "8"))
COMMANDS_PER_10S = int(os.getenv("IRX_MASTER_COMMANDS_PER_10S", "30"))
SERVER_TTL = int(os.getenv("IRX_MASTER_SERVER_TTL", "3900"))
VERIFY_TIMEOUT = float(os.getenv("IRX_MASTER_VERIFY_TIMEOUT", "2.0"))
MAX_CLOCK_SKEW = 90

logging.basicConfig(
    level=os.getenv("IRX_MASTER_LOGLEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("irx-master")


@dataclass
class GameServer:
    host: str
    port: int
    last_seen: float = field(default_factory=time.monotonic)
    verified: bool = False
    auth_v2: bool = False


class TokenBucket:
    def __init__(self, capacity: int, period: float):
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.period = period
        self.updated = time.monotonic()

    def allow(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        elapsed = now - self.updated
        self.updated = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.capacity / self.period)
        if self.tokens < cost:
            return False
        self.tokens -= cost
        return True


class PongProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.waiters: Dict[Tuple[str, int], asyncio.Future] = {}

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr):
        key = (addr[0], addr[1])
        fut = self.waiters.pop(key, None)
        if fut and not fut.done():
            fut.set_result(data)

    async def verify(self, host: str, game_port: int) -> bool:
        if not self.transport:
            return False
        info_port = game_port + 1
        if not (1 <= game_port <= 65534):
            return False
        loop = asyncio.get_running_loop()
        key = (host, info_port)
        old = self.waiters.pop(key, None)
        if old and not old.done():
            old.cancel()
        fut = loop.create_future()
        self.waiters[key] = fut
        self.transport.sendto(b"\x01", key)
        try:
            data = await asyncio.wait_for(fut, VERIFY_TIMEOUT)
            return bool(data)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self.waiters.pop(key, None)
            return False


class MasterServer:
    def __init__(self):
        self.servers: Dict[Tuple[str, int], GameServer] = {}
        self.active_by_ip: Dict[str, int] = {}
        self.buckets: Dict[str, TokenBucket] = {}
        self.clients = 0
        self.pong = PongProtocol()
        self._cleanup_task: Optional[asyncio.Task] = None

    @staticmethod
    def normalize_ip(value: str) -> str:
        return str(ipaddress.ip_address(value))

    def bucket(self, ip: str) -> TokenBucket:
        b = self.buckets.get(ip)
        if not b:
            b = self.buckets[ip] = TokenBucket(COMMANDS_PER_10S, 10.0)
        return b

    def cleanup(self):
        cutoff = time.monotonic() - SERVER_TTL
        stale = [key for key, item in self.servers.items() if item.last_seen < cutoff]
        for key in stale:
            self.servers.pop(key, None)
        if stale:
            log.info("expired %d stale server registrations", len(stale))

    async def cleanup_loop(self):
        while True:
            await asyncio.sleep(30)
            self.cleanup()

    def server_list_payload(self) -> bytes:
        self.cleanup()
        lines = []
        for item in sorted(self.servers.values(), key=lambda s: (s.host, s.port)):
            if item.verified:
                lines.append(f"addserver {item.host} {item.port}\n")
        return ("".join(lines) + "\0").encode("ascii", "strict")

    def verify_hmac(self, host: str, port: int, timestamp: int, nonce: str, signature: str) -> bool:
        if not SHARED_SECRET:
            return False
        if abs(int(time.time()) - timestamp) > MAX_CLOCK_SKEW:
            return False
        if len(nonce) < 16 or len(nonce) > 128 or len(signature) != 64:
            return False
        msg = f"{host}|{port}|{timestamp}|{nonce}".encode()
        expected = hmac.new(SHARED_SECRET, msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature.lower())

    async def register_legacy(self, host: str, port: int, writer: asyncio.StreamWriter):
        if not 1 <= port <= 65534:
            writer.write(b"failreg invalid port\n")
            await writer.drain()
            return
        if len(self.servers) >= MAX_SERVERS and (host, port) not in self.servers:
            writer.write(b"failreg registry full\n")
            await writer.drain()
            return
        verified = await self.pong.verify(host, port)
        if not verified:
            writer.write(b"failreg failed pinging server\n")
            await writer.drain()
            return
        self.servers[(host, port)] = GameServer(host, port, verified=True, auth_v2=False)
        writer.write(b"succreg\n")
        await writer.drain()
        log.info("registered legacy server %s:%d", host, port)

    async def register_v2(self, host: str, args, writer: asyncio.StreamWriter):
        if len(args) != 5:
            writer.write(b"failreg invalid regserv2\n")
            await writer.drain()
            return
        try:
            port = int(args[1])
            timestamp = int(args[2])
        except ValueError:
            writer.write(b"failreg invalid arguments\n")
            await writer.drain()
            return
        nonce, signature = args[3], args[4]
        if not 1 <= port <= 65534 or not self.verify_hmac(host, port, timestamp, nonce, signature):
            writer.write(b"failreg authentication failed\n")
            await writer.drain()
            return
        verified = await self.pong.verify(host, port)
        if not verified:
            writer.write(b"failreg failed pinging server\n")
            await writer.drain()
            return
        self.servers[(host, port)] = GameServer(host, port, verified=True, auth_v2=True)
        writer.write(b"succreg\n")
        await writer.drain()
        log.info("registered authenticated server %s:%d", host, port)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        if not peer:
            writer.close()
            return
        try:
            host = self.normalize_ip(peer[0])
        except ValueError:
            writer.close()
            return
        if self.clients >= MAX_CLIENTS or self.active_by_ip.get(host, 0) >= MAX_CONN_PER_IP:
            writer.close()
            await writer.wait_closed()
            return
        self.clients += 1
        self.active_by_ip[host] = self.active_by_ip.get(host, 0) + 1
        try:
            while True:
                try:
                    line = await asyncio.wait_for(reader.readline(), timeout=180.0)
                except asyncio.TimeoutError:
                    break
                if not line:
                    break
                if len(line) > MAX_LINE or not line.endswith(b"\n"):
                    break
                if not self.bucket(host).allow():
                    log.warning("rate limit exceeded by %s", host)
                    break
                try:
                    text = line.decode("ascii", "strict").strip()
                except UnicodeDecodeError:
                    break
                if not text:
                    continue
                parts = text.split()
                cmd = parts[0].lower()
                if cmd == "list":
                    writer.write(self.server_list_payload())
                    await writer.drain()
                    break
                # Native iRx sends: regserv <port> <description>-<key> <version>.
                # As in the original C++ master, only the first numeric argument
                # is authoritative; all registration identity comes from peer IP
                # plus the UDP reachability check.
                if cmd == "regserv" and len(parts) >= 2:
                    try:
                        port = int(parts[1])
                    except ValueError:
                        writer.write(b"failreg invalid port\n")
                        await writer.drain()
                        continue
                    await self.register_legacy(host, port, writer)
                    continue
                if cmd == "regserv2":
                    await self.register_v2(host, parts, writer)
                    continue
                if cmd == "ping":
                    writer.write(b"pong\n")
                    await writer.drain()
                    continue
                writer.write(b"fail unknown command\n")
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self.clients -= 1
            left = self.active_by_ip.get(host, 1) - 1
            if left <= 0:
                self.active_by_ip.pop(host, None)
            else:
                self.active_by_ip[host] = left
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def run(self):
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(lambda: self.pong, local_addr=(BIND, 0))
        server = await asyncio.start_server(self.handle, BIND, PORT, limit=MAX_LINE + 1)
        self._cleanup_task = asyncio.create_task(self.cleanup_loop())
        sockets = ", ".join(str(s.getsockname()) for s in server.sockets or [])
        log.info("iRx master listening on %s (public %s:%d)", sockets, PUBLIC_HOST, PORT)
        async with server:
            await server.serve_forever()


async def main():
    master = MasterServer()
    await master.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
