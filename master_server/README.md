# iRx Master Server

`server.py` is a small protocol-compatible master-list service for iRx.

Default public endpoint: `irautox.ir:7575` (TCP).

Run it with:

```bash
python3 master_server/server.py
```

Point the DNS record for `irautox.ir` at the machine running the service and allow inbound TCP/7575. Each public game server must answer its normal UDP server-info probe on `game_port + 1`, because the master verifies that the TCP peer really owns a reachable game server before listing it.

Configuration is environment based:

```text
IRX_MASTER_BIND=0.0.0.0
IRX_MASTER_PORT=7575
IRX_MASTER_PUBLIC_HOST=irautox.ir
IRX_MASTER_MAX_CLIENTS=2048
IRX_MASTER_MAX_CONN_PER_IP=8
IRX_MASTER_COMMANDS_PER_10S=30
IRX_MASTER_SERVER_TTL=3900
IRX_MASTER_VERIFY_TIMEOUT=2.0
```

Optional hardened registration can use `IRX_MASTER_SHARED_SECRET` with `regserv2` and HMAC-SHA256. Do not distribute that secret with public clients.

Security controls include bounded protocol lines, per-IP token-bucket rate limiting, connection caps, server-count caps, strict port parsing, source-IP registration (no caller-supplied IP), stale entry expiry, and UDP reachability verification.
