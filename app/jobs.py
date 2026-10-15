"""Tareas en segundo plano (una por entrada) y las acciones chequear / pull / actualizar."""
import threading
import time
import uuid

from docker.errors import NotFound

from . import dockerops, store

MAX_LOG = 400
_jobs = {}
_lock = threading.Lock()

ACTION_LABELS = {"check": "Chequeo", "pull": "Pull", "update": "Actualización", "auto": "Actualización automática"}


def get(eid):
    return _jobs.get(eid)


def is_running(eid):
    j = _jobs.get(eid)
    return bool(j and j["state"] == "running")


def any_running():
    return any(j["state"] == "running" for j in _jobs.values())


def start(eid, action, trigger="manual"):
    fn = {"check": do_check, "pull": do_pull, "update": do_update, "auto": do_auto}[action]
    with _lock:
        if is_running(eid):
            raise RuntimeError("Ya hay una tarea en curso para esta entrada")
        job = {"id": uuid.uuid4().hex[:8], "action": action, "label": ACTION_LABELS[action],
               "trigger": trigger, "state": "running", "started": time.time(),
               "finished": None, "log": []}
        _jobs[eid] = job

    def log(msg):
        for line in str(msg).splitlines() or [""]:
            job["log"].append(f"[{time.strftime('%H:%M:%S')}] {line}")
        if len(job["log"]) > MAX_LOG:
            del job["log"][: len(job["log"]) - MAX_LOG]

    def runner():
        try:
            entry = store.get_entry(eid)
            if not entry:
                raise RuntimeError("La entrada ya no existe")
            fn(entry, log)
            job["state"] = "ok"
        except Exception as exc:  # noqa: BLE001
            log(f"ERROR: {exc}")
            job["state"] = "error"
            store.update_entry(eid, last_result="error", last_error=str(exc), last_result_at=time.time())
        finally:
            job["finished"] = time.time()

    threading.Thread(target=runner, daemon=True, name=f"job-{eid}").start()
    return job


# ---------- acciones ----------

def _check(entry, log):
    ref = entry["image"]
    log(f"Consultando el registro por {ref}…")
    try:
        remote = dockerops.remote_digest(ref, store.auth_for(ref))
    except Exception as exc:
        store.update_entry(entry["id"], last_check_at=time.time(), last_check_error=str(exc))
        raise RuntimeError(f"No se pudo consultar el registro: {exc}") from exc
    img = dockerops.local_image(ref)
    available = remote not in dockerops.digests_of(img)
    store.update_entry(entry["id"], remote_digest=remote, last_check_at=time.time(), last_check_error=None)
    if not img:
        log("La imagen todavía no está descargada en este equipo.")
    elif available:
        log("Hay una versión nueva disponible en el registro.")
    else:
        log("La imagen local coincide con la del registro.")
    return available, img


def _outdated_containers(entry, image_id):
    out = []
    for cname in entry.get("containers") or []:
        c = dockerops.container_brief(cname)
        if c and c["image_id"] != image_id:
            out.append(c)
    return out


def do_check(entry, log):
    available, img = _check(entry, log)
    if img and not available:
        stale = _outdated_containers(entry, img["Id"])
        for c in stale:
            log(f"Ojo: {c['name']} todavía corre una imagen anterior (falta recrearlo).")
    store.update_entry(entry["id"], last_result="ok", last_error=None, last_result_at=time.time())


def _pull(entry, log):
    ref = entry["image"]
    log(f"Descargando {ref}…")
    img = dockerops.pull(ref, store.auth_for(ref), log)
    digests = dockerops.digests_of(img)
    fields = {"last_pull_at": time.time()}
    current = store.get_entry(entry["id"]) or {}
    if digests and current.get("remote_digest") not in digests:
        fields["remote_digest"] = sorted(digests)[0]
    fields["last_check_at"] = time.time()
    fields["last_check_error"] = None
    store.update_entry(entry["id"], **fields)
    return img


def do_pull(entry, log):
    _pull(entry, log)
    log("Pull terminado. Los contenedores no se tocaron.")
    store.update_entry(entry["id"], last_result="ok", last_error=None, last_result_at=time.time())


def _recreate_all(entry, img, log):
    if not entry.get("containers"):
        log("No hay contenedores asociados: la imagen queda descargada.")
        return
    if not entry.get("recreate", True):
        log("Recrear contenedores está desactivado para esta entrada: la imagen queda descargada.")
        return
    me = dockerops.self_id()
    failures = []
    for cname in entry["containers"]:
        try:
            c = dockerops.container_brief(cname)
            if not c:
                log(f"{cname}: no existe en este equipo, se omite.")
                continue
            if c["image_id"] == img["Id"]:
                log(f"{cname}: ya usa la última imagen.")
                continue
            if me and c["id"] == me:
                dockerops.launch_self_update(entry["image"], log)
                continue
            dockerops.recreate(c["id"], entry["image"], log)
        except NotFound as exc:
            failures.append(cname)
            log(f"{cname}: no encontrado ({exc})")
        except Exception as exc:  # noqa: BLE001
            failures.append(cname)
            log(f"{cname}: {exc}")
    if failures:
        raise RuntimeError("No se pudo actualizar: " + ", ".join(failures))


def do_update(entry, log):
    img = _pull(entry, log)
    _recreate_all(entry, img, log)
    store.update_entry(entry["id"], last_update_at=time.time(), last_result="ok",
                       last_error=None, last_result_at=time.time())
    log("Listo.")


def do_auto(entry, log):
    available, img = _check(entry, log)
    stale = _outdated_containers(entry, img["Id"]) if img else []
    if img and not available and not (stale and entry.get("recreate", True)):
        log("Nada para actualizar.")
        store.update_entry(entry["id"], last_result="ok", last_error=None, last_result_at=time.time())
        return
    do_update(entry, log)
