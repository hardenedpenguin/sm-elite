"""HTTP and WebSocket API for the dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import WSCloseCode, WSMsgType, web

from .admin import ASTERISK_ACTIONS, POWER_ACTIONS
from .ami import AmiError
from .astdb import search_directory
from .auth import setup_code, setup_code_matches
from .commands import NODE_RE, CommandError, action_command, check_command
from .favorites import Favorite, add_favorite, list_files, load_file, remove_favorite, save_file
from .monitor import Engine
from . import sysinfo
from .sounds import SCOPES

log = logging.getLogger("smelite")

COOKIE = "smelite"
SECURE_COOKIE = "__Secure-smelite"
WEB = Path(__file__).resolve().parent.parent / "web"


def create_app(engine: Engine) -> web.Application:
    app = web.Application(middlewares=[forwarded, guard])
    app["engine"] = engine
    app.on_startup.append(_start)
    app.on_shutdown.append(_close_sockets)
    app.on_cleanup.append(_stop)
    app.router.add_get("/", index)
    app.router.add_get("/api/bootstrap", bootstrap)
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/setup", setup)
    app.router.add_post("/api/login", login)
    app.router.add_post("/api/logout", logout)
    app.router.add_get("/api/users", users_list)
    app.router.add_post("/api/users", users_create)
    app.router.add_delete("/api/users/{name}", users_delete)
    app.router.add_get("/api/favorites", favorites_get)
    app.router.add_post("/api/favorites", favorites_add)
    app.router.add_delete("/api/favorites", favorites_delete)
    app.router.add_post("/api/favorites/watch", favorites_watch)
    app.router.add_get("/api/heard", heard)
    app.router.add_get("/api/directory", directory_search)
    app.router.add_get("/api/access", access_get)
    app.router.add_post("/api/access", access_post)
    app.router.add_get("/api/admin", admin_info)
    app.router.add_get("/api/admin/file", admin_file_get)
    app.router.add_post("/api/admin/file", admin_file_save)
    app.router.add_get("/api/admin/log", admin_log)
    app.router.add_get("/api/admin/backups", admin_backups)
    app.router.add_get("/api/admin/backup", admin_backup_compare)
    app.router.add_post("/api/admin/restore", admin_restore)
    app.router.add_post("/api/admin/action", admin_action)
    app.router.add_get("/api/sounds", sounds_get)
    app.router.add_post("/api/sounds/speak", sounds_speak)
    app.router.add_post("/api/sounds/play", sounds_play)
    app.router.add_post("/api/sounds/save", sounds_save)
    app.router.add_post("/api/sounds/delete", sounds_delete)
    app.router.add_get("/api/dvswitch", dvswitch_get)
    app.router.add_post("/api/dvswitch/mode", dvswitch_mode)
    app.router.add_post("/api/dvswitch/tune", dvswitch_tune)
    app.router.add_post("/api/dvswitch/restart", dvswitch_restart)
    app.router.add_get("/api/system", system_info)
    app.router.add_get("/api/system/database", system_database)
    app.router.add_get("/api/commands", commands_list)
    app.router.add_post("/api/command", command)
    app.router.add_get("/ws", websocket)
    if (WEB / "css").is_dir():
        app.router.add_static("/css", WEB / "css")
        app.router.add_static("/js", WEB / "js")
    return app


async def _start(app: web.Application) -> None:
    engine: Engine = app["engine"]
    await engine.start()
    if _setup_code_required(engine) and engine.auth.user_count() == 0:
        path = _setup_code_path(engine)
        setup_code(path)
        log.warning("No accounts yet. Create the admin account with the setup code in %s", path)


def _setup_code_path(engine: Engine) -> Path:
    return engine.settings.data_dir / "setup-code"


def _setup_code_required(engine: Engine) -> bool:
    return not engine.settings.demo


async def _close_sockets(app: web.Application) -> None:
    for socket in list(app["engine"].hub.clients):
        await socket.close(code=WSCloseCode.GOING_AWAY, message=b"server restarting")


async def _stop(app: web.Application) -> None:
    await app["engine"].close()


def _session_tokens(request: web.Request) -> list[str]:
    """Our cookie values, read without trusting other cookies on the host.

    Browsers send every cookie for the address regardless of port, and one
    malformed cookie from another app makes the stdlib parser drop the rest.
    """
    tokens = []
    for header in request.headers.getall("Cookie", []):
        for part in header.split(";"):
            name, _, value = part.strip().partition("=")
            if name == _cookie_name(request) and value:
                tokens.append(value.strip('"'))
    return tokens


def _session_user(engine: Engine, request: web.Request):
    for token in _session_tokens(request):
        user = engine.auth.user_for_token(token)
        if user is not None:
            request["token"] = token
            return user
    return None


@web.middleware
async def forwarded(request: web.Request, handler):
    """Use the client's scheme and address when a trusted reverse proxy relays the request.

    The last X-Forwarded-For hop is the one our proxy added; earlier hops come
    from the client and can be forged.
    """
    if request.remote in request.app["engine"].settings.trusted_proxies:
        changes = {}
        proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
        if proto in {"http", "https"}:
            changes["scheme"] = proto
        hops = [hop.strip() for hop in request.headers.get("X-Forwarded-For", "").split(",") if hop.strip()]
        if hops:
            changes["remote"] = hops[-1]
        if changes:
            request = request.clone(**changes)
    return await handler(request)


@web.middleware
async def guard(request: web.Request, handler):
    engine: Engine = request.app["engine"]
    request["token"] = None
    request["user"] = _session_user(engine, request)
    if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return web.json_response({"error": "cross-origin request was rejected"}, status=403)
    if engine.settings.public == "none" and request.path.startswith("/api/"):
        open_paths = {"/api/bootstrap", "/api/health", "/api/login", "/api/setup"}
        if request.path not in open_paths and request["user"] is None:
            return web.json_response({"error": "login required"}, status=401)
    try:
        return await handler(request)
    except CommandError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except AmiError as exc:
        return web.json_response({"error": f"Asterisk did not accept the request: {exc}"}, status=502)


def _asset_version() -> str:
    stamps = [path.stat().st_mtime_ns for path in (WEB / "js" / "app.js", WEB / "css" / "app.css") if path.is_file()]
    return format(max(stamps, default=0), "x")


def index(_request: web.Request) -> web.Response:
    page = WEB / "index.html"
    if not page.is_file():
        return web.Response(text="dashboard files are missing", status=500)
    version = _asset_version()
    html = page.read_text(encoding="utf-8")
    html = html.replace('"js/app.js"', f'"js/app.js?v={version}"').replace('"css/app.css"', f'"css/app.css?v={version}"')
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})


def _user_payload(user) -> dict | None:
    if user is None:
        return None
    return {
        "name": user.name,
        "role": user.role,
        "nodes": list(user.nodes) if user.nodes else None,
    }


async def bootstrap(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    settings = engine.settings
    files = list_files(settings.favorites_dir)
    nodes = []
    for node in settings.nodes:
        if not node.menu:
            continue
        nodes.append({
            "id": node.node,
            "system": node.system,
            "label": node.label,
            "voters": node.voters,
            "extras": node.extras,
        })
        for extra in node.extras:
            nodes.append({"id": extra, "system": node.system, "label": "", "voters": [], "extras": []})
    return web.json_response({
        "title": settings.title,
        "callsign": settings.callsign,
        "location": settings.location,
        "welcome": settings.welcome,
        "demo": settings.demo,
        "public": settings.public,
        "needs_setup": engine.auth.user_count() == 0,
        "setup_code_file": (
            str(_setup_code_path(engine)) if _setup_code_required(engine) and engine.auth.user_count() == 0 else None
        ),
        "admin_tools": engine.admin.enabled,
        "dvswitch": [node for node in engine.dvswitch.nodes() if node in settings.monitored_ids()],
        "user": _user_payload(request["user"]),
        "nodes": nodes,
        "groups": [{"name": group.name, "nodes": group.nodes, "system": group.system} for group in settings.groups],
        "favorites": files,
        "commands": [{"label": item.label, "command": item.command} for item in settings.commands],
    })


def _host_facts() -> dict:
    facts: dict = {}
    load_path = Path("/proc/loadavg")
    if load_path.is_file():
        facts["load"] = load_path.read_text(encoding="utf-8").split()[:3]
    thermal = Path("/sys/class/thermal")
    zones = sorted(thermal.glob("thermal_zone*/temp")) if thermal.is_dir() else []
    for zone in zones:
        try:
            celsius = int(zone.read_text().strip()) / 1000
        except (OSError, ValueError):
            continue
        if celsius > 0:
            facts["temp_c"] = round(celsius, 1)
            break
    usage = shutil.disk_usage("/")
    facts["disk_free_gb"] = round(usage.free / 1024**3, 1)
    facts["disk_pct"] = round(100 * usage.used / usage.total, 1) if usage.total else None
    return facts


async def health(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    return web.json_response({
        "ok": True,
        "demo": engine.settings.demo,
        "watching": sorted(engine.hub.wanted()),
        "host": _host_facts(),
    })


def _cookie_name(request: web.Request) -> str:
    """HTTPS and plain HTTP on the same host keep separate sessions.

    Cookies ignore the port, and a browser will not let an http:// page
    replace a Secure cookie, so a shared name breaks login on one of them.
    """
    return SECURE_COOKIE if request.secure else COOKIE


def _set_cookie(response: web.Response, request: web.Request, token: str) -> None:
    response.set_cookie(
        _cookie_name(request), token, max_age=12 * 3600, httponly=True, samesite="Lax", secure=request.secure, path="/",
    )


async def setup(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    if engine.auth.user_count() != 0:
        return web.json_response({"error": "setup is already complete"}, status=409)
    body = await _json(request)
    if _setup_code_required(engine):
        peer = request.remote or "local"
        now = time.time()
        recent = [stamp for stamp in engine.login_failures.get(peer, []) if now - stamp < 300]
        if len(recent) >= 8:
            return web.json_response({"error": "too many attempts; wait a few minutes"}, status=429)
        if not setup_code_matches(_setup_code_path(engine), str(body.get("code") or "")):
            engine.login_failures[peer] = recent + [now]
            return web.json_response({"error": "that setup code is not right"}, status=403)
    name = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    engine.auth.create_user(name, password, "admin")
    _setup_code_path(engine).unlink(missing_ok=True)
    log.warning("setup: created admin account %s", name)
    user = engine.auth.verify(name, password)
    token = engine.auth.create_session(user)
    response = web.json_response({"user": _user_payload(user)})
    _set_cookie(response, request, token)
    return response


async def login(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    peer = request.remote or "local"
    now = time.time()
    recent = [stamp for stamp in engine.login_failures.get(peer, []) if now - stamp < 300]
    if len(recent) >= 8:
        return web.json_response({"error": "too many login attempts"}, status=429)
    body = await _json(request)
    user = engine.auth.verify(str(body.get("username") or "").strip(), str(body.get("password") or ""))
    if user is None:
        recent.append(now)
        engine.login_failures[peer] = recent
        return web.json_response({"error": "login failed"}, status=401)
    engine.login_failures.pop(peer, None)
    token = engine.auth.create_session(user)
    response = web.json_response({"user": _user_payload(user)})
    _set_cookie(response, request, token)
    return response


async def logout(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    engine.auth.drop_session(request["token"])
    response = web.json_response({"ok": True})
    response.del_cookie(_cookie_name(request), path="/", secure=request.secure)
    return response


def _require(request: web.Request, role: str):
    user = request["user"]
    if user is None or not user.allows(role):
        return None
    return user


async def users_list(request: web.Request) -> web.Response:
    if _require(request, "admin") is None:
        return web.json_response({"error": "admin required"}, status=401)
    return web.json_response({"users": request.app["engine"].auth.list_users()})


async def users_create(request: web.Request) -> web.Response:
    if _require(request, "admin") is None:
        return web.json_response({"error": "admin required"}, status=401)
    body = await _json(request)
    nodes = body.get("nodes")
    if isinstance(nodes, str):
        nodes = [part.strip() for part in nodes.split(",") if part.strip()]
    request.app["engine"].auth.create_user(
        str(body.get("username") or "").strip(),
        str(body.get("password") or ""),
        str(body.get("role") or "operator"),
        nodes or None,
    )
    return web.json_response({"ok": True})


async def users_delete(request: web.Request) -> web.Response:
    actor = _require(request, "admin")
    if actor is None:
        return web.json_response({"error": "admin required"}, status=401)
    engine: Engine = request.app["engine"]
    name = request.match_info["name"]
    users = engine.auth.list_users()
    admins = [item for item in users if item["role"] == "admin"]
    if len(admins) == 1 and admins[0]["name"] == name:
        return web.json_response({"error": "the last admin cannot be removed"}, status=400)
    engine.auth.delete_user(name)
    return web.json_response({"ok": True})


def _favorite_view(engine: Engine, item: Favorite) -> dict:
    row = engine.directory.rows.get(item.node or "", {})
    return {
        "label": item.label,
        "node": item.node,
        "header": item.header,
        "only": None if item.section == "general" else item.section,
        "call": row.get("call", ""),
        "detail": row.get("desc", ""),
        "location": row.get("location", ""),
    }


async def favorites_get(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    if engine.settings.public == "none" and request["user"] is None:
        return web.json_response({"error": "login required"}, status=401)
    name = request.query.get("file") or engine.favorite_file
    try:
        items = load_file(engine.settings.favorites_dir, name)
    except FileNotFoundError:
        return web.json_response({"error": "favorites file was not found"}, status=404)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({
        "file": name,
        "items": [_favorite_view(engine, item) for item in items],
        "stats": {node: engine.hub.activities.get(node) for node in (item.node for item in items if item.node) if node in engine.hub.activities},
    })


async def favorites_add(request: web.Request) -> web.Response:
    if _require(request, "operator") is None:
        return web.json_response({"error": "operator required"}, status=401)
    engine: Engine = request.app["engine"]
    body = await _json(request)
    name = str(body.get("file") or engine.favorite_file)
    items = load_file(engine.settings.favorites_dir, name)
    items = add_favorite(items, str(body.get("node") or "").strip(), str(body.get("label") or ""))
    save_file(engine.settings.favorites_dir, name, items)
    return web.json_response({"ok": True})


async def favorites_delete(request: web.Request) -> web.Response:
    if _require(request, "operator") is None:
        return web.json_response({"error": "operator required"}, status=401)
    engine: Engine = request.app["engine"]
    body = await _json(request)
    name = str(body.get("file") or engine.favorite_file)
    items = remove_favorite(load_file(engine.settings.favorites_dir, name), str(body.get("node") or "").strip())
    save_file(engine.settings.favorites_dir, name, items)
    return web.json_response({"ok": True})


async def favorites_watch(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    if engine.settings.public == "none" and request["user"] is None:
        return web.json_response({"error": "login required"}, status=401)
    body = await _json(request)
    name = str(body.get("file") or "favorites.ini")
    # Validate the name before the scanner follows it.
    load_file(engine.settings.favorites_dir, name)
    engine.favorite_file = name
    return web.json_response({"ok": True, "file": name})


async def heard(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    known = set(engine.settings.monitored_ids())
    asked = [part.strip() for part in request.query.get("nodes", "").split(",") if part.strip()]
    nodes = [node for node in asked if node in known] if asked else sorted(known)
    try:
        limit = int(request.query.get("limit", "50"))
    except ValueError as exc:
        raise ValueError("limit must be a number") from exc
    return web.json_response(engine.heard(nodes, limit))


async def directory_search(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    query = request.query.get("q", "").strip()[:64]
    try:
        limit = max(1, min(int(request.query.get("limit", "25")), 100))
    except ValueError as exc:
        raise ValueError("limit must be a number") from exc
    if len(query) < 2:
        return web.json_response({"query": query, "items": [], "total": 0})
    rows = engine.directory.rows
    items, total = search_directory(rows, query, limit)
    for item in items:
        override = engine.settings.overrides.get(item["node"])
        if override:
            item["desc"] = override
    if len(query) >= 4 and NODE_RE.fullmatch(query) and query not in rows:
        items.insert(0, {"node": query, "call": "", "desc": engine.describe(query), "location": ""})
        total += 1
    return web.json_response({"query": query, "items": items, "total": total})


def _access_node(request: web.Request, local: str, role: str):
    """The user, when they hold ``role`` and may control ``local``."""
    engine: Engine = request.app["engine"]
    user = _require(request, role)
    if user is None:
        raise web.HTTPUnauthorized(text=json.dumps({"error": f"{role} required"}), content_type="application/json")
    if local not in engine.settings.monitored_ids() or not user.can_command(local):
        raise web.HTTPForbidden(text=json.dumps({"error": "not allowed on this node"}), content_type="application/json")
    return user


async def access_get(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    local = request.query.get("node", "").strip()
    _access_node(request, local, "operator")
    lists = await engine.access_lists(local)
    return web.json_response({"node": local, "allow": lists["allowlist"], "deny": lists["denylist"]})


async def access_post(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    body = await _json(request)
    local = str(body.get("node") or "").strip()
    _access_node(request, local, "admin")
    remote = str(body.get("target") or "").strip()
    which = str(body.get("list") or "")
    add = str(body.get("action") or "add") == "add"
    if add and remote == local:
        raise ValueError("a node cannot be listed against itself")
    await engine.access_change(local, which, remote, str(body.get("comment") or ""), add)
    detail = ""
    if add and which.startswith("deny") and body.get("disconnect"):
        linked = {str(conn.get("node")) for conn in (engine.hub.snapshots.get(local) or {}).get("conns", [])}
        if remote in linked:
            detail = await engine.run_command(action_command("disconnect", local, remote), local)
    lists = await engine.access_lists(local)
    return web.json_response({"node": local, "allow": lists["allowlist"], "deny": lists["denylist"], "detail": detail})


async def sounds_get(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    user = _require(request, "operator")
    if user is None:
        raise web.HTTPUnauthorized(text=json.dumps({"error": "operator required"}), content_type="application/json")
    return web.json_response({
        "tts": engine.sounds.tts_ready(),
        "voices": engine.sounds.voices(),
        "library": engine.sounds.library(),
        "max_chars": engine.sounds.max_chars,
        "can_save": user.allows("admin"),
    })


async def sounds_speak(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    body = await _json(request)
    local = str(body.get("node") or "").strip()
    user = _access_node(request, local, "operator")
    scope = str(body.get("scope") or "local")
    if scope not in SCOPES:
        raise ValueError("pick where to play it")
    path, seconds = await engine.sounds.speak(str(body.get("text") or ""), str(body.get("voice") or ""))
    await engine.play_sound(local, path, scope, user.name)
    return web.json_response({"ok": True, "seconds": seconds})


async def sounds_play(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    body = await _json(request)
    local = str(body.get("node") or "").strip()
    user = _access_node(request, local, "operator")
    path = engine.sounds.resolve(str(body.get("name") or ""))
    await engine.play_sound(local, path, str(body.get("scope") or "local"), user.name)
    return web.json_response({"ok": True})


async def sounds_save(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    if _require(request, "admin") is None:
        raise web.HTTPForbidden(text=json.dumps({"error": "admin required"}), content_type="application/json")
    body = await _json(request)
    entry = await engine.sounds.save(
        str(body.get("name") or "").strip(), str(body.get("text") or ""), str(body.get("voice") or ""),
    )
    log.warning("sound: %s saved %s", request["user"].name, entry["name"])
    return web.json_response({"saved": entry, "library": engine.sounds.library()})


async def sounds_delete(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    if _require(request, "admin") is None:
        raise web.HTTPForbidden(text=json.dumps({"error": "admin required"}), content_type="application/json")
    body = await _json(request)
    name = str(body.get("name") or "")
    await engine.sounds.delete(name)
    log.warning("sound: %s deleted %s", request["user"].name, name)
    return web.json_response({"library": engine.sounds.library()})


async def dvswitch_get(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    local = request.query.get("node", "").strip()
    user = _access_node(request, local, "operator")
    view = engine.dvswitch.view(local)
    view["can_restart"] = user.allows("admin") and engine.admin.enabled
    return web.json_response(view)


async def dvswitch_mode(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    body = await _json(request)
    local = str(body.get("node") or "").strip()
    user = _access_node(request, local, "operator")
    status = await engine.dvswitch.set_mode(local, str(body.get("mode") or ""), user.name)
    return web.json_response({"status": status})


async def dvswitch_tune(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    body = await _json(request)
    local = str(body.get("node") or "").strip()
    user = _access_node(request, local, "operator")
    index = body.get("id")
    if index is not None and (isinstance(index, bool) or not isinstance(index, int)):
        raise ValueError("pick a talkgroup from the list")
    status = await engine.dvswitch.tune(
        local, str(body.get("mode") or ""), user.name, index=index, custom=str(body.get("custom") or ""),
    )
    return web.json_response({"status": status})


async def dvswitch_restart(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    body = await _json(request)
    local = str(body.get("node") or "").strip()
    user = _access_node(request, local, "admin")
    if not engine.admin.enabled:
        raise web.HTTPForbidden(text=json.dumps({"error": "admin tools are turned off"}), content_type="application/json")
    log.warning("dvswitch: %s restarted the bridge services", user.name)
    if engine.settings.demo:
        detail = "demo: bridge restart skipped"
    else:
        detail = await engine.admin.run_helper(["bridge-restart"])
    return web.json_response({"detail": detail})


async def system_info(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    if _require(request, "operator") is None:
        raise web.HTTPUnauthorized(text=json.dumps({"error": "operator required"}), content_type="application/json")
    nodes = engine.settings.monitored_ids()

    async def asterisk(command: str) -> str:
        if not nodes:
            return ""
        try:
            return await engine.run_command(command, nodes[0])
        except AmiError as exc:
            return f"unavailable: {exc}"

    addresses, services, packages, version, uptime = await asyncio.gather(
        sysinfo.addresses(),
        sysinfo.services(engine.settings.system_services),
        sysinfo.packages(),
        asterisk("core show version"),
        asterisk("core show uptime"),
    )
    return web.json_response({
        **sysinfo.host_facts(),
        "addresses": addresses,
        "services": services,
        "packages": packages,
        "asterisk": {"version": version, "uptime": uptime},
        "nodes": nodes,
    })


async def system_database(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    local = request.query.get("node", "").strip()
    _access_node(request, local, "admin")
    if engine.settings.demo:
        raw = "/denylist/1998/12345 : added from SM-Elite\n/allowlist/1999/2000 : club hub\n/1998/lastconnect : 27339\n3 results found."
    else:
        raw = await engine.run_command("database show", local)
    rows = sysinfo.parse_database(raw)
    return web.json_response({"node": local, "rows": rows})


def _admin(request: web.Request):
    engine: Engine = request.app["engine"]
    user = _require(request, "admin")
    if user is None:
        error = web.HTTPForbidden if request["user"] else web.HTTPUnauthorized
        raise error(text=json.dumps({"error": "admin required"}), content_type="application/json")
    if not engine.admin.enabled:
        raise web.HTTPForbidden(
            text=json.dumps({"error": "admin tools are off; set [admin] enabled = yes in smelite.ini"}),
            content_type="application/json",
        )
    return user


def _confirm_password(request: web.Request, user, body: dict) -> None:
    engine: Engine = request.app["engine"]
    if engine.auth.verify(user.name, str(body.get("password") or "")) is None:
        raise web.HTTPForbidden(text=json.dumps({"error": "password is incorrect"}), content_type="application/json")


async def admin_info(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    _admin(request)
    return web.json_response({
        "files": engine.admin.files(),
        "helper_ok": await engine.admin.check(),
        "log_file": str(engine.admin.log_file),
        "nodes": engine.settings.monitored_ids(),
    })


async def admin_file_get(request: web.Request) -> web.Response:
    _admin(request)
    try:
        return web.json_response(request.app["engine"].admin.read(request.query.get("name", "")))
    except OSError as exc:
        raise ValueError(f"cannot read that file: {exc.strerror}") from exc


async def admin_file_save(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    user = _admin(request)
    body = await _json(request)
    _confirm_password(request, user, body)
    name = str(body.get("name") or "")
    backup = await engine.admin.write(name, str(body.get("text") or ""), str(body.get("sha256") or ""), user.name)
    return web.json_response({**engine.admin.read(name), "backup": backup})


async def admin_backups(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    _admin(request)
    name = request.query.get("name", "")
    return web.json_response({"name": name, "backups": await engine.admin.backups(name)})


async def admin_backup_compare(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    _admin(request)
    return web.json_response(await engine.admin.backup_compare(request.query.get("name", ""), request.query.get("stamp", "")))


async def admin_restore(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    user = _admin(request)
    body = await _json(request)
    _confirm_password(request, user, body)
    name = str(body.get("name") or "")
    backup = await engine.admin.restore(name, str(body.get("stamp") or ""), str(body.get("sha256") or ""), user.name)
    return web.json_response({**engine.admin.read(name), "backup": backup, "backups": await engine.admin.backups(name)})


async def admin_log(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    _admin(request)
    try:
        lines = int(request.query.get("lines", "300"))
    except ValueError as exc:
        raise ValueError("lines must be a number") from exc
    return web.json_response({"file": str(engine.admin.log_file), "text": engine.admin.tail_log(lines)})


async def admin_action(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    user = _admin(request)
    body = await _json(request)
    _confirm_password(request, user, body)
    action = str(body.get("action") or "")
    if action in POWER_ACTIONS:
        detail = await engine.admin.power(action, user.name)
    elif action in ASTERISK_ACTIONS:
        local = str(body.get("node") or "").strip() or (engine.settings.monitored_ids() or [""])[0]
        if engine.settings.node_by_id(local) is None:
            raise ValueError("pick a configured node")
        detail = await engine.asterisk_action(action, local, user.name)
    else:
        raise ValueError("unknown admin action")
    return web.json_response({"ok": True, "detail": detail})


async def commands_list(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    return web.json_response({
        "commands": [{"label": item.label, "command": item.command} for item in engine.settings.commands],
    })


async def command(request: web.Request) -> web.Response:
    user = _require(request, "operator")
    if user is None:
        return web.json_response({"error": "operator required"}, status=401)
    engine: Engine = request.app["engine"]
    body = await _json(request)
    local = str(body.get("node") or "").strip()
    if not user.can_command(local):
        return web.json_response({"error": "not allowed on this node"}, status=403)
    action = str(body.get("action") or "")
    target = str(body.get("target") or "").strip() or None
    if action == "custom":
        configured = {item.command for item in engine.settings.commands}
        if str(body.get("template") or "") not in configured:
            return web.json_response({"error": "that command is not in commands.ini"}, status=403)
    if body.get("disconnect_first") and action in {"connect", "monitor", "local_monitor"}:
        current = engine.hub.snapshots.get(local) or {}
        if current.get("conns"):
            drop = action_command("disconnect_all", local)
            await engine.run_command(drop, local)
            await asyncio_sleep()
    built = action_command(
        action,
        local,
        target,
        permanent=bool(body.get("permanent")),
        digits=str(body.get("digits") or ""),
        template=str(body.get("template") or "") or None,
    )
    check_command(built)
    detail = await engine.run_command(built, local)
    return web.json_response({"ok": True, "command": built, "detail": detail[:4000]})


async def asyncio_sleep() -> None:
    import asyncio
    await asyncio.sleep(0.5)


async def websocket(request: web.Request) -> web.WebSocketResponse:
    engine: Engine = request.app["engine"]
    if engine.settings.public == "none" and request["user"] is None:
        raise web.HTTPUnauthorized(text="login required")
    socket = web.WebSocketResponse(heartbeat=20)
    await socket.prepare(request)
    engine.hub.add(socket)
    try:
        async for message in socket:
            if message.type != WSMsgType.TEXT:
                if message.type == WSMsgType.ERROR:
                    break
                continue
            try:
                payload = json.loads(message.data)
            except json.JSONDecodeError:
                continue
            action = payload.get("action")
            if action == "ping":
                await socket.send_json({"type": "pong"})
            elif action == "subscribe":
                known = set(engine.settings.monitored_ids())
                requested = {str(node) for node in payload.get("nodes") or [] if str(node) in known}
                engine.hub.subscribe(socket, requested)
                await engine.hub.replay(socket)
    finally:
        engine.hub.remove(socket)
    return socket


async def _json(request: web.Request) -> dict:
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise ValueError("request body must be JSON") from exc
    if not isinstance(body, dict):
        raise ValueError("request body must be an object")
    return body
