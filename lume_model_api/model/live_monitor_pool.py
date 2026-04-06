from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web
from multidict import CIMultiDict


LOGGER = logging.getLogger(__name__)
SESSION_QUERY_PARAM = "lume_session"
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


@dataclass(frozen=True)
class PoolConfig:
    base_url: str
    worker_count: int
    worker_statefulset: str
    worker_headless_service: str
    namespace: str
    cluster_domain: str
    worker_port: int
    session_timeout_seconds: int
    no_ws_timeout_seconds: int
    cleanup_interval_seconds: int
    connect_timeout_seconds: float
    request_timeout_seconds: float

    @classmethod
    def from_env(cls) -> "PoolConfig":
        base_url = os.environ.get("LUME_BASE_URL", "/live-monitor").rstrip("/")
        if not base_url.startswith("/"):
            base_url = f"/{base_url}"
        return cls(
            base_url=base_url,
            worker_count=int(os.environ.get("LUME_WORKER_COUNT", "5")),
            worker_statefulset=os.environ.get(
                "LUME_WORKER_STATEFULSET", "lume-live-monitor-worker"
            ),
            worker_headless_service=os.environ.get(
                "LUME_WORKER_HEADLESS_SERVICE", "lume-live-monitor-worker"
            ),
            namespace=os.environ.get("POD_NAMESPACE", "lume-visualizations"),
            cluster_domain=os.environ.get("CLUSTER_DOMAIN", ""),
            worker_port=int(os.environ.get("LUME_WORKER_PORT", "2719")),
            session_timeout_seconds=int(
                os.environ.get("LUME_SESSION_TIMEOUT_SECONDS", "3600")
            ),
            no_ws_timeout_seconds=int(
                os.environ.get("LUME_NO_WS_TIMEOUT_SECONDS", "60")
            ),
            cleanup_interval_seconds=int(
                os.environ.get("LUME_SESSION_CLEANUP_INTERVAL_SECONDS", "30")
            ),
            connect_timeout_seconds=float(
                os.environ.get("LUME_CONNECT_TIMEOUT_SECONDS", "10")
            ),
            request_timeout_seconds=float(
                os.environ.get("LUME_REQUEST_TIMEOUT_SECONDS", "120")
            ),
        )


@dataclass
class SessionLease:
    session_id: str
    worker_index: int
    last_activity: float
    active_websockets: int = 0
    ever_had_websocket: bool = False


class PoolFullError(RuntimeError):
    pass


class SessionPool:
    def __init__(self, config: PoolConfig):
        self.config = config
        self._leases: dict[str, SessionLease] = {}
        self._lock = asyncio.Lock()
        self._stateless_worker_index = 0

    def worker_origin(self, worker_index: int) -> str:
        host = (
            f"{self.config.worker_statefulset}-{worker_index}."
            f"{self.config.worker_headless_service}."
            f"{self.config.namespace}.svc"
        )
        if self.config.cluster_domain:
            host = f"{host}.{self.config.cluster_domain}"
        return f"http://{host}:{self.config.worker_port}"

    def build_upstream_url(self, request: web.Request, worker_index: int) -> str:
        query_items = [
            (key, value)
            for key, value in request.rel_url.query.items()
            if key != SESSION_QUERY_PARAM
        ]
        query_string = urlencode(query_items, doseq=True)
        origin = self.worker_origin(worker_index).rstrip("/")
        path = request.path_qs.split("?", 1)[0]
        if query_string:
            return f"{origin}{path}?{query_string}"
        return f"{origin}{path}"

    def is_navigation_request(self, request: web.Request) -> bool:
        if request.method not in {"GET", "HEAD"}:
            return False
        if request.path not in {self.config.base_url, f"{self.config.base_url}/"}:
            return False
        accept = request.headers.get("Accept", "")
        fetch_mode = request.headers.get("Sec-Fetch-Mode", "")
        fetch_dest = request.headers.get("Sec-Fetch-Dest", "")
        return "text/html" in accept or (
            fetch_mode == "navigate" and fetch_dest in {"document", "iframe", ""}
        )

    def build_redirect_url(self, request: web.Request, session_id: str) -> str:
        canonical_path = f"{self.config.base_url}/"
        query = list(request.rel_url.query.items())
        query.append((SESSION_QUERY_PARAM, session_id))
        return str(request.rel_url.with_path(canonical_path).with_query(query))

    def build_canonical_url(self, request: web.Request) -> str:
        canonical_path = f"{self.config.base_url}/"
        return str(request.rel_url.with_path(canonical_path))

    def session_id_from_request(self, request: web.Request) -> str | None:
        session_id = request.query.get(SESSION_QUERY_PARAM)
        if session_id:
            return session_id

        referer = request.headers.get("Referer")
        if not referer:
            return None

        try:
            parsed = urlsplit(referer)
        except ValueError:
            return None

        if not parsed.path.startswith(self.config.base_url):
            return None

        values = parse_qs(parsed.query).get(SESSION_QUERY_PARAM)
        return values[0] if values else None

    async def allocate_worker(
        self, session_id: str, exclude_workers: set[int] | None = None,
    ) -> int:
        async with self._lock:
            now = time.time()
            self._purge_expired_locked(now)
            lease = self._leases.get(session_id)
            if lease is not None:
                lease.last_activity = now
                return lease.worker_index

            used_workers = {lease.worker_index for lease in self._leases.values()}
            skip = used_workers | (exclude_workers or set())
            for worker_index in range(self.config.worker_count):
                if worker_index not in skip:
                    self._leases[session_id] = SessionLease(
                        session_id=session_id,
                        worker_index=worker_index,
                        last_activity=now,
                    )
                    LOGGER.info(
                        "Assigned session %s to worker %d",
                        session_id,
                        worker_index,
                    )
                    return worker_index

        raise PoolFullError(
            f"The live monitor pool is full ({self.config.worker_count} active sessions)."
        )

    async def choose_stateless_worker(self) -> int:
        async with self._lock:
            worker_index = self._stateless_worker_index % self.config.worker_count
            self._stateless_worker_index += 1
            return worker_index

    async def touch(self, session_id: str) -> None:
        async with self._lock:
            lease = self._leases.get(session_id)
            if lease is not None:
                lease.last_activity = time.time()

    async def mark_websocket_open(self, session_id: str) -> None:
        async with self._lock:
            lease = self._leases.get(session_id)
            if lease is not None:
                lease.active_websockets += 1
                lease.ever_had_websocket = True
                lease.last_activity = time.time()

    async def mark_websocket_closed(self, session_id: str) -> None:
        async with self._lock:
            lease = self._leases.get(session_id)
            if lease is not None:
                lease.active_websockets = max(lease.active_websockets - 1, 0)
                if lease.active_websockets == 0:
                    del self._leases[session_id]
                    LOGGER.info(
                        "Session %s released (last WebSocket closed, worker %d freed)",
                        session_id,
                        lease.worker_index,
                    )
                else:
                    lease.last_activity = time.time()

    async def drop(self, session_id: str, reason: str) -> None:
        async with self._lock:
            lease = self._leases.pop(session_id, None)
            if lease is not None:
                LOGGER.warning(
                    "Dropped session %s from worker %d: %s",
                    session_id,
                    lease.worker_index,
                    reason,
                )

    async def snapshot(self) -> dict[str, int]:
        async with self._lock:
            now = time.time()
            self._purge_expired_locked(now)
            return {
                "active_sessions": len(self._leases),
                "worker_count": self.config.worker_count,
            }

    async def cleanup_forever(self) -> None:
        while True:
            await asyncio.sleep(self.config.cleanup_interval_seconds)
            async with self._lock:
                self._purge_expired_locked(time.time())

    def _purge_expired_locked(self, now: float) -> None:
        expired_sessions = [
            session_id
            for session_id, lease in self._leases.items()
            if lease.active_websockets == 0
            and (
                # Sessions that never connected a WS browser use a short TTL so
                # that a full page-load that was abandoned (tab closed before WS
                # handshake) does not block a worker slot for a full hour.
                now - lease.last_activity >= (
                    self.config.session_timeout_seconds
                    if lease.ever_had_websocket
                    else self.config.no_ws_timeout_seconds
                )
            )
        ]
        for session_id in expired_sessions:
            lease = self._leases.pop(session_id)
            LOGGER.info(
                "Expired session %s from worker %d after %.0f seconds of inactivity",
                session_id,
                lease.worker_index,
                now - lease.last_activity,
            )


def _configure_logging() -> None:
    level_name = os.environ.get("LUME_LIVE_MONITOR_POOL_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def _copy_request_headers(request: web.Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        lower_key = key.lower()
        if lower_key in HOP_BY_HOP_HEADERS or lower_key == "host":
            continue
        # Strip WebSocket handshake headers — the proxy creates its own WS
        # handshake when connecting upstream.
        if lower_key.startswith("sec-websocket-"):
            continue
        headers[key] = value

    # Forward the external Host so the worker can generate correct absolute
    # URLs (e.g. in redirect Location headers or HTML-embedded URLs).
    headers["Host"] = request.headers.get("X-Forwarded-Host", request.host)

    forwarded_for = request.headers.get("X-Forwarded-For", "").strip()
    remote = (request.remote or "").strip()
    if forwarded_for and remote:
        headers["X-Forwarded-For"] = f"{forwarded_for}, {remote}"
    else:
        headers["X-Forwarded-For"] = forwarded_for or remote
    headers["X-Forwarded-Host"] = request.headers.get("X-Forwarded-Host", request.host)
    headers["X-Forwarded-Proto"] = request.headers.get("X-Forwarded-Proto", request.scheme)
    return headers


def _copy_response_headers(headers: Iterable[tuple[str, str]]) -> CIMultiDict:
    """Copy response headers preserving duplicate values (e.g. Set-Cookie)."""
    result: CIMultiDict = CIMultiDict()
    for key, value in headers:
        if key.lower() in HOP_BY_HOP_HEADERS:
            continue
        result.add(key, value)
    return result


async def healthz(request: web.Request) -> web.Response:
    pool: SessionPool = request.app["pool"]
    return web.json_response(await pool.snapshot())


async def proxy_request(request: web.Request) -> web.StreamResponse:
    pool: SessionPool = request.app["pool"]

    if request.path == pool.config.base_url and request.method in {"GET", "HEAD"}:
        raise web.HTTPTemporaryRedirect(location=pool.build_canonical_url(request))

    if pool.is_navigation_request(request) and request.query.get(SESSION_QUERY_PARAM) is None:
        session_id = uuid.uuid4().hex
        raise web.HTTPTemporaryRedirect(location=pool.build_redirect_url(request, session_id))

    session_id = pool.session_id_from_request(request)

    is_websocket = request.headers.get("Upgrade", "").lower() == "websocket"
    if is_websocket:
        return await proxy_websocket(request, pool, session_id)

    return await proxy_http(request, pool, session_id)


async def proxy_http(
    request: web.Request,
    pool: SessionPool,
    session_id: str | None,
) -> web.StreamResponse:
    if session_id is None:
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            raise web.HTTPBadRequest(text="Missing live monitor session identifier.")
        return await _proxy_http_to_stateless_worker(request, pool)

    failed_workers: set[int] = set()
    for attempt in range(2):
        try:
            worker_index = await pool.allocate_worker(session_id, exclude_workers=failed_workers)
        except PoolFullError as exc:
            raise web.HTTPServiceUnavailable(text=str(exc)) from exc

        try:
            response = await _forward_http_request(request, pool, worker_index)
            return response
        except (asyncio.TimeoutError, OSError, web.HTTPException) as exc:
            if isinstance(exc, web.HTTPException):
                raise
            failed_workers.add(worker_index)
            await pool.drop(session_id, reason=str(exc))
            if attempt == 0:
                continue
            raise web.HTTPBadGateway(text="The assigned live monitor worker is unavailable.") from exc

    raise web.HTTPBadGateway(text="Unable to contact a live monitor worker.")


async def _proxy_http_to_stateless_worker(
    request: web.Request,
    pool: SessionPool,
) -> web.StreamResponse:
    last_error: Exception | None = None
    for _ in range(pool.config.worker_count):
        worker_index = await pool.choose_stateless_worker()
        try:
            return await _forward_http_request(request, pool, worker_index)
        except (asyncio.TimeoutError, OSError) as exc:
            last_error = exc
            continue
    raise web.HTTPBadGateway(text="No live monitor workers are currently reachable.") from last_error


async def _forward_http_request(
    request: web.Request,
    pool: SessionPool,
    worker_index: int,
) -> web.StreamResponse:
    client: ClientSession = request.app["client_session"]
    body = await request.read()
    upstream_url = pool.build_upstream_url(request, worker_index)
    headers = _copy_request_headers(request)

    async with client.request(
        request.method,
        upstream_url,
        allow_redirects=False,
        headers=headers,
        data=body,
    ) as upstream_response:
        response = web.Response(
            body=await upstream_response.read(),
            status=upstream_response.status,
            headers=_copy_response_headers(upstream_response.headers.items()),
        )
        return response


async def proxy_websocket(
    request: web.Request,
    pool: SessionPool,
    session_id: str | None,
) -> web.StreamResponse:
    if session_id is None:
        raise web.HTTPBadRequest(text="Missing live monitor session identifier.")

    client: ClientSession = request.app["client_session"]
    headers = _copy_request_headers(request)
    protocol_header = request.headers.get("Sec-WebSocket-Protocol", "")
    protocols = [value.strip() for value in protocol_header.split(",") if value.strip()]

    upstream_ws = None
    failed_workers: set[int] = set()
    for attempt in range(2):
        try:
            worker_index = await pool.allocate_worker(session_id, exclude_workers=failed_workers)
        except PoolFullError as exc:
            raise web.HTTPServiceUnavailable(text=str(exc)) from exc

        upstream_url = pool.build_upstream_url(request, worker_index)
        try:
            upstream_ws = await client.ws_connect(
                upstream_url,
                headers=headers,
                protocols=protocols or None,
                heartbeat=30.0,
                autoping=True,
                compress=0,  # Disable per-message deflate to avoid compression mismatch
            )
            break  # connected successfully
        except (asyncio.TimeoutError, OSError) as exc:
            failed_workers.add(worker_index)
            await pool.drop(session_id, reason=str(exc))
            if attempt == 0:
                continue
            raise web.HTTPBadGateway(text="Unable to connect to the assigned live monitor worker.") from exc

    # Disable compression: the proxy relays raw frames between browser and
    # worker and cannot share per-message deflate contexts across the two
    # independent WebSocket connections.
    downstream_ws = web.WebSocketResponse(protocols=protocols or None, heartbeat=30.0, compress=False)
    await downstream_ws.prepare(request)
    await pool.mark_websocket_open(session_id)

    async def downstream_to_upstream() -> None:
        async for message in downstream_ws:
            await pool.touch(session_id)
            if message.type == WSMsgType.TEXT:
                await upstream_ws.send_str(message.data)
            elif message.type == WSMsgType.BINARY:
                await upstream_ws.send_bytes(message.data)
            elif message.type == WSMsgType.PING:
                await upstream_ws.ping(message.data)
            elif message.type == WSMsgType.PONG:
                await upstream_ws.pong(message.data)
            elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                break

    async def upstream_to_downstream() -> None:
        async for message in upstream_ws:
            await pool.touch(session_id)
            if message.type == WSMsgType.TEXT:
                await downstream_ws.send_str(message.data)
            elif message.type == WSMsgType.BINARY:
                await downstream_ws.send_bytes(message.data)
            elif message.type == WSMsgType.PING:
                await downstream_ws.ping(message.data)
            elif message.type == WSMsgType.PONG:
                await downstream_ws.pong(message.data)
            elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                break

    d2u_task = asyncio.ensure_future(downstream_to_upstream())
    u2d_task = asyncio.ensure_future(upstream_to_downstream())
    try:
        await asyncio.wait({d2u_task, u2d_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in (d2u_task, u2d_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(d2u_task, u2d_task, return_exceptions=True)
    finally:
        await pool.mark_websocket_closed(session_id)
        await upstream_ws.close()
        await downstream_ws.close()

    return downstream_ws


async def pool_context(app: web.Application):
    config = PoolConfig.from_env()
    pool = SessionPool(config)
    timeout = ClientTimeout(
        total=config.request_timeout_seconds,
        connect=config.connect_timeout_seconds,
    )
    app["pool"] = pool
    # auto_decompress=False: the proxy must relay response bodies byte-for-byte.
    # With the default (True), aiohttp silently decompresses gzipped bodies
    # while preserving the original Content-Encoding header, causing the
    # browser to double-decompress — garbled data → "Failed to fetch".
    app["client_session"] = ClientSession(timeout=timeout, auto_decompress=False)
    cleanup_task = asyncio.create_task(pool.cleanup_forever())
    try:
        yield
    finally:
        cleanup_task.cancel()
        await asyncio.gather(cleanup_task, return_exceptions=True)
        await app["client_session"].close()


def create_app() -> web.Application:
    app = web.Application()
    app.cleanup_ctx.append(pool_context)
    app.router.add_get("/healthz", healthz)
    app.router.add_route("*", "/{tail:.*}", proxy_request)
    return app


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=2719)
    args = parser.parse_args()
    web.run_app(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()