import asyncio
import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import dockerops, jobs, store

VERSION = "1.0.0"
PASSWORD = os.environ.get("PULLPANEL_PASSWORD", "")
STATIC = Path(__file__).parent / "static"
log = logging.getLogger("pullpanel")



@asynccontextmanager
async def lifespan(_app):
    if not PASSWORD:
        log.warning("PULLPANEL_PASSWORD no está definida: la interfaz no va a dejar entrar")
    task = asyncio.create_task(scheduler())
    yield
    task.cancel()


app = FastAPI(title="PullPanel", version=VERSION, docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=store.get_secret(), max_age=14 * 24 * 3600,
                   same_site="lax", session_cookie="pullpanel_session")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def require_auth(request: Request):
    if not PASSWORD:
        raise HTTPException(503, "Falta definir PULLPANEL_PASSWORD en el compose")
    if not request.session.get("auth"):
        raise HTTPException(401, "No autenticado")


def _err(exc):
    msg = getattr(exc, "explanation", None) or str(exc)
    return HTTPException(400, msg)


# ---------- páginas / sesión ----------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/session")
def session(request: Request):
    return {"authenticated": bool(PASSWORD and request.session.get("auth")),
            "password_configured": bool(PASSWORD), "version": VERSION}


@app.post("/api/login")
async def login(request: Request):
    if not PASSWORD:
        raise HTTPException(503, "Falta definir PULLPANEL_PASSWORD en el compose")
    body = await request.json()
    if not hmac.compare_digest(str(body.get("password", "")).encode(), PASSWORD.encode()):
        await asyncio.sleep(1.5)
        raise HTTPException(401, "Contraseña incorrecta")
    request.session["auth"] = True
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


# ---------- contenedores / entradas ----------

@app.get("/api/containers", dependencies=[Depends(require_auth)])
def containers():
    try:
        return dockerops.containers_list()
    except Exception as exc:
        raise _err(exc)


def _entry_view(e, containers_by_name):
    img = dockerops.local_image(e["image"])
    img_id = img["Id"] if img else None
    digests = dockerops.digests_of(img)
    remote = e.get("remote_digest")
    cinfo = []
    for name in e.get("containers") or []:
        c = containers_by_name.get(name)
        cinfo.append({
            "name": name,
            "exists": bool(c),
            "state": c["state"] if c else None,
            "outdated": bool(c and img_id and c["image_id"] != img_id),
            "is_self": bool(c and c.get("is_self")),
        })
    job = jobs.get(e["id"])
    update_available = (remote not in digests) if (remote and img) else None
    if job and job["state"] == "running":
        status = "running"
    elif not img:
        status = "missing"
    elif e.get("last_check_error") and not remote:
        status = "error"
    elif update_available:
        status = "available"
    elif any(c["outdated"] for c in cinfo) and e.get("recreate", True):
        status = "pending"
    elif remote is None:
        status = "unknown"
    else:
        status = "uptodate"
    interval = store.interval_seconds(e)
    next_auto = None
    if (e.get("auto") or {}).get("enabled"):
        next_auto = (e.get("last_auto_at") or e.get("created_at") or time.time()) + interval
    return {
        **e,
        "local_image_id": img_id,
        "local_digest": sorted(digests)[0] if digests else None,
        "image_created": img.get("Created") if img else None,
        "update_available": update_available,
        "containers_info": cinfo,
        "status": status,
        "next_auto_at": next_auto,
        "job": job,
    }


@app.get("/api/entries", dependencies=[Depends(require_auth)])
def entries():
    try:
        by_name = {c["name"]: c for c in dockerops.containers_list()}
        docker_error = None
    except Exception as exc:
        by_name, docker_error = {}, str(exc)
    out = []
    for e in store.list_entries():
        try:
            out.append(_entry_view(e, by_name))
        except Exception as exc:
            out.append({**e, "status": "error", "containers_info": [], "job": jobs.get(e["id"]),
                        "view_error": str(exc)})
    return {"entries": out, "docker_error": docker_error, "busy": jobs.any_running()}


@app.post("/api/entries", dependencies=[Depends(require_auth)])
async def create_entry(request: Request):
    try:
        return store.add_entry(await request.json())
    except ValueError as exc:
        raise _err(exc)


@app.put("/api/entries/{eid}", dependencies=[Depends(require_auth)])
async def edit_entry(eid: str, request: Request):
    if jobs.is_running(eid):
        raise HTTPException(409, "Esperá a que termine la tarea en curso")
    try:
        return store.edit_entry(eid, await request.json())
    except KeyError:
        raise HTTPException(404, "No existe")
    except ValueError as exc:
        raise _err(exc)


@app.delete("/api/entries/{eid}", dependencies=[Depends(require_auth)])
def remove_entry(eid: str):
    if jobs.is_running(eid):
        raise HTTPException(409, "Esperá a que termine la tarea en curso")
    if not store.delete_entry(eid):
        raise HTTPException(404, "No existe")
    return {"ok": True}


@app.post("/api/entries/{eid}/run", dependencies=[Depends(require_auth)])
async def run_entry(eid: str, request: Request):
    body = await request.json()
    action = body.get("action")
    if action not in ("check", "pull", "update"):
        raise HTTPException(400, "Acción inválida")
    if not store.get_entry(eid):
        raise HTTPException(404, "No existe")
    try:
        return jobs.start(eid, action)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/check-all", dependencies=[Depends(require_auth)])
def check_all():
    started = 0
    for e in store.list_entries():
        if not jobs.is_running(e["id"]):
            jobs.start(e["id"], "check")
            started += 1
    return {"started": started}


# ---------- imágenes ----------

@app.get("/api/images", dependencies=[Depends(require_auth)])
def images():
    try:
        return dockerops.images_list()
    except Exception as exc:
        raise _err(exc)


@app.post("/api/images/delete", dependencies=[Depends(require_auth)])
async def delete_images(request: Request):
    body = await request.json()
    results = []
    for image_id in body.get("ids") or []:
        try:
            dockerops.delete_image(image_id)
            results.append({"id": image_id, "ok": True})
        except Exception as exc:
            results.append({"id": image_id, "ok": False,
                            "error": getattr(exc, "explanation", None) or str(exc)})
    return {"results": results}


# ---------- ajustes ----------

@app.get("/api/settings", dependencies=[Depends(require_auth)])
def get_settings():
    info = {"version": VERSION}
    try:
        v = dockerops.client().version()
        info["docker_version"] = v.get("Version")
        sid = dockerops.self_id()
        info["self_container"] = dockerops.api().inspect_container(sid)["Name"].lstrip("/") if sid else None
    except Exception as exc:
        info["docker_error"] = str(exc)
    return {"registries": store.get_registries_public(), "info": info}


@app.put("/api/settings", dependencies=[Depends(require_auth)])
async def put_settings(request: Request):
    body = await request.json()
    store.set_registries(body.get("registries") or [])
    return {"registries": store.get_registries_public()}


# ---------- programador automático ----------

async def scheduler():
    await asyncio.sleep(10)
    while True:
        try:
            now = time.time()
            for e in store.list_entries():
                auto = e.get("auto") or {}
                if not auto.get("enabled"):
                    continue
                last = e.get("last_auto_at") or e.get("created_at") or now
                if now - last >= store.interval_seconds(e) and not jobs.is_running(e["id"]):
                    store.update_entry(e["id"], last_auto_at=now)
                    jobs.start(e["id"], "auto", trigger="auto")
        except Exception:  # noqa: BLE001
            log.exception("Error en el programador")
        await asyncio.sleep(30)


