"""Lumen V32 Xray data-plane manager.

The public Railway port is owned by ``edge_gateway.js``.  It forwards only
registered per-link paths to loopback Xray inbounds.  Every inbound is bound to
127.0.0.1 and contains exactly one Lumen identity.  Xray then sends the decoded
connection to a per-route SOCKS bridge, which reuses Lumen's existing exact
proxy resolver and fail-closed outbound connector.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Awaitable, Callable

import outbound

log = logging.getLogger("Lumen.xray")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
XRAY_DIR = DATA_DIR / "xray"
XRAY_CONFIG = XRAY_DIR / "config.json"
XRAY_ROUTES = DATA_DIR / "xray-routes.json"
XRAY_BIN = os.environ.get("XRAY_BIN", "/usr/local/bin/xray")
INBOUND_BASE = int(os.environ.get("XRAY_INBOUND_BASE_PORT", "21000"))
BRIDGE_BASE = int(os.environ.get("LUMEN_BRIDGE_BASE_PORT", "31000"))

_get_links: Callable[[], dict] | None = None
_resolve_selection: Callable[[dict, str], Awaitable[dict | None]] | None = None
_process: asyncio.subprocess.Process | None = None
_bridges: list[asyncio.AbstractServer] = []
_sync_lock = asyncio.Lock()
_last_digest = ""
_started_at = 0.0
_last_error = ""
_persist_usage = None
_usage_task: asyncio.Task | None = None
_usage_dirty = False


def _transport(protocol: str) -> tuple[str, str]:
    mapping = {
        "vless-ws": ("vless", "ws"),
        "vless-xhttp": ("vless", "xhttp"),
        "vless-httpupgrade": ("vless", "httpupgrade"),
        "vmess-ws": ("vmess", "ws"),
        "trojan-ws": ("trojan", "ws"),
    }
    if protocol not in mapping:
        raise ValueError(f"unsupported Xray protocol: {protocol}")
    return mapping[protocol]


def public_path(protocol: str, uuid: str, location_id: str = "") -> str:
    suffix = location_id if location_id else "default"
    return f"/xray/{protocol}/{uuid}/{suffix}"


def _route_specs(links: dict) -> list[dict]:
    specs: list[dict] = []
    for uid, link in sorted(links.items()):
        if not isinstance(link, dict) or not link.get("active", True):
            continue
        protocol = str(link.get("protocol") or "vless-ws")
        try:
            proto, network = _transport(protocol)
        except ValueError:
            continue
        locations = [{"id": "", "active": True}]
        ml = link.get("_runtime_multi_location")
        if isinstance(ml, dict) and ml.get("enabled"):
            locations = [x for x in ml.get("locations", []) if isinstance(x, dict) and x.get("active", True)]
        for loc in locations:
            loc_id = str(loc.get("id") or "")
            specs.append({
                "uuid": uid,
                "protocol_id": protocol,
                "protocol": proto,
                "network": network,
                "location_id": loc_id,
                "path": public_path(protocol, uid, loc_id),
                "ip_limit": max(0, int(link.get("ip_limit", 0) or 0)),
            })
    return specs


def _client_settings(spec: dict) -> dict:
    uid = spec["uuid"]
    email = f"{uid}@lumen"
    if spec["protocol"] == "vless":
        return {"clients": [{"id": uid, "email": email, "flow": ""}], "decryption": "none"}
    if spec["protocol"] == "vmess":
        return {"clients": [{"id": uid, "email": email, "alterId": 0}]}
    return {"clients": [{"password": uid, "email": email}]}


def _stream_settings(spec: dict) -> dict:
    network, path = spec["network"], spec["path"]
    result: dict = {"network": network, "security": "none"}
    if network == "ws":
        result["wsSettings"] = {"path": path, "acceptProxyProtocol": False}
    elif network == "httpupgrade":
        result["httpupgradeSettings"] = {"path": path}
    elif network == "xhttp":
        result["xhttpSettings"] = {"path": path, "mode": "auto"}
    return result


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, link: dict, loc_id: str, uid: str) -> None:
    remote_writer = None
    try:
        if await reader.readexactly(2) != b"\x05\x01":
            return
        method = await reader.readexactly(1)
        if method != b"\x00":
            return
        writer.write(b"\x05\x00"); await writer.drain()
        head = await reader.readexactly(4)
        if head[:3] != b"\x05\x01\x00":
            return
        atyp = head[3]
        if atyp == 1:
            host = ".".join(str(x) for x in await reader.readexactly(4))
        elif atyp == 4:
            import ipaddress
            host = str(ipaddress.ip_address(await reader.readexactly(16)))
        elif atyp == 3:
            size = (await reader.readexactly(1))[0]
            host = (await reader.readexactly(size)).decode("idna")
        else:
            return
        port = int.from_bytes(await reader.readexactly(2), "big")
        selection = await _resolve_selection(link, loc_id) if _resolve_selection else None
        endpoints = [selection["endpoint"]] if selection else []
        proxy_id = selection["proxy_id"] if selection else ""
        remote_reader, remote_writer, _ = await outbound.open_outbound(
            host, port, link=link, uuid=uid, endpoints=endpoints, proxy_id=proxy_id,
        )
        writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"); await writer.drain()

        async def pump(src, dst, count: bool = False):
            global _usage_dirty
            sent = 0
            started = asyncio.get_running_loop().time()
            while True:
                chunk = await src.read(256 * 1024)
                if not chunk:
                    break
                if count:
                    limit = int(link.get("limit_bytes", 0) or 0)
                    used = int(link.get("used_bytes", 0) or 0)
                    if limit and used + len(chunk) > limit:
                        break
                    link["used_bytes"] = used + len(chunk)
                    _usage_dirty = True
                    speed = int(link.get("speed_limit_bytes", 0) or 0)
                    if speed > 0:
                        sent += len(chunk)
                        delay = sent / speed - (asyncio.get_running_loop().time() - started)
                        if delay > 0: await asyncio.sleep(min(delay, 1.0))
                dst.write(chunk); await dst.drain()

        tasks = [asyncio.create_task(pump(reader, remote_writer, True)), asyncio.create_task(pump(remote_reader, writer, True))]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending: task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError):
        try:
            writer.write(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00"); await writer.drain()
        except Exception:
            pass
    finally:
        for stream in (remote_writer, writer):
            if stream is not None:
                stream.close()
                try: await stream.wait_closed()
                except Exception: pass


async def _build(links: dict) -> tuple[dict, dict, list[asyncio.AbstractServer]]:
    specs = _route_specs(links)
    inbounds, outbounds, rules, route_rows, servers = [], [], [], [], []
    for index, spec in enumerate(specs):
        inbound_port = INBOUND_BASE + index
        bridge_port = BRIDGE_BASE + index
        tag = f"in-{index}"
        out_tag = f"route-{index}"
        inbounds.append({
            "tag": tag, "listen": "127.0.0.1", "port": inbound_port,
            "protocol": spec["protocol"], "settings": _client_settings(spec),
            "streamSettings": _stream_settings(spec),
            "sniffing": {"enabled": False, "destOverride": []},
        })
        outbounds.append({"tag": out_tag, "protocol": "socks", "settings": {"servers": [{"address": "127.0.0.1", "port": bridge_port}]}})
        rules.append({"type": "field", "inboundTag": [tag], "outboundTag": out_tag})
        config_link = links[spec["uuid"]]
        link = config_link.get("_source_link", config_link)
        servers.append({"port": bridge_port, "link": link, "location_id": spec["location_id"], "uuid": spec["uuid"]})
        route_rows.append({
            "path": spec["path"], "port": inbound_port, "network": spec["network"],
            "uuid": spec["uuid"], "ip_limit": spec["ip_limit"],
        })
    config = {
        "log": {"loglevel": "warning"},
        "stats": {},
        "policy": {"levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}}},
        "inbounds": inbounds,
        "outbounds": outbounds + [{"tag": "blocked", "protocol": "blackhole", "settings": {}}],
        "routing": {"domainStrategy": "AsIs", "rules": rules},
    }
    routes = {"version": 32, "routes": route_rows}
    return config, routes, servers


async def sync(force: bool = False) -> bool:
    global _process, _bridges, _last_digest, _started_at, _last_error
    if _get_links is None:
        return False
    async with _sync_lock:
        links = _get_links()
        # Attach current Multi-Location runtime data without persisting an implementation detail.
        config, routes, new_servers = await _build(links)
        payload = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(payload).hexdigest()
        if not force and digest == _last_digest and _process and _process.returncode is None:
            return True
        XRAY_DIR.mkdir(parents=True, exist_ok=True)
        candidate = XRAY_DIR / "config.candidate.json"
        candidate.write_bytes(payload)
        binary = shutil.which(XRAY_BIN) or (XRAY_BIN if Path(XRAY_BIN).exists() else "")
        if not binary:
            _last_error = "Xray binary is missing"
            log.error(_last_error)
            return False
        test = await asyncio.create_subprocess_exec(binary, "run", "-test", "-config", str(candidate), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        _out, err = await test.communicate()
        if test.returncode != 0:
            _last_error = "Xray rejected generated config: " + err.decode(errors="replace")[-500:]
            log.error(_last_error)
            return False
        old_config = XRAY_CONFIG.read_bytes() if XRAY_CONFIG.exists() else None
        old_routes = XRAY_ROUTES.read_bytes() if XRAY_ROUTES.exists() else None
        old_process, old_bridges = _process, _bridges
        if old_process and old_process.returncode is None:
            old_process.terminate()
            try: await asyncio.wait_for(old_process.wait(), 3)
            except asyncio.TimeoutError: old_process.kill()
        for server in old_bridges: server.close(); await server.wait_closed()
        actual_servers = []
        for item in new_servers:
            server = await asyncio.start_server(
                lambda r, w, link=item["link"], loc=item["location_id"], uid=item["uuid"]: _relay(r, w, link, loc, uid),
                "127.0.0.1", item["port"],
            )
            actual_servers.append(server)
        _process = await asyncio.create_subprocess_exec(binary, "run", "-config", str(candidate), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.sleep(0.25)
        if _process.returncode is not None:
            _last_error = "Xray exited during startup"
            for server in actual_servers: server.close(); await server.wait_closed()
            _process = None
            if old_config is not None:
                XRAY_CONFIG.write_bytes(old_config)
                _process = await asyncio.create_subprocess_exec(binary, "run", "-config", str(XRAY_CONFIG), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                _bridges = []
            return False
        candidate.replace(XRAY_CONFIG)
        tmp_routes = XRAY_ROUTES.with_suffix(".tmp")
        tmp_routes.write_text(json.dumps(routes, separators=(",", ":")), encoding="utf-8")
        tmp_routes.replace(XRAY_ROUTES)
        _bridges = actual_servers
        _last_digest = digest
        _started_at = asyncio.get_running_loop().time()
        _last_error = ""
        log.info("Xray V32 activated routes=%d revision=%s", len(routes["routes"]), digest[:12])
        return True


async def _usage_flusher() -> None:
    global _usage_dirty
    while True:
        await asyncio.sleep(30)
        if _usage_dirty and _persist_usage is not None:
            _usage_dirty = False
            try: await _persist_usage(rotate=False)
            except Exception: log.exception("could not persist Xray usage")

async def start(get_links: Callable[[], dict], resolve_selection: Callable[[dict, str], Awaitable[dict | None]], persist_usage=None) -> bool:
    global _get_links, _resolve_selection, _persist_usage, _usage_task
    _get_links, _resolve_selection, _persist_usage = get_links, resolve_selection, persist_usage
    ok = await sync(force=True)
    if _usage_task is None or _usage_task.done(): _usage_task = asyncio.create_task(_usage_flusher())
    return ok


async def stop() -> None:
    global _process, _bridges, _usage_task
    if _usage_task:
        _usage_task.cancel()
        await asyncio.gather(_usage_task, return_exceptions=True)
        _usage_task = None
    for server in _bridges: server.close(); await server.wait_closed()
    _bridges = []
    if _process and _process.returncode is None:
        _process.terminate()
        try: await asyncio.wait_for(_process.wait(), 3)
        except asyncio.TimeoutError: _process.kill()
    _process = None


def status() -> dict:
    running = bool(_process and _process.returncode is None)
    uptime = int(asyncio.get_running_loop().time() - _started_at) if running and _started_at else 0
    return {"running": running, "revision": _last_digest[:12], "uptime": uptime, "error": _last_error, "routes_file": XRAY_ROUTES.exists()}
