"""Persistencia simple en JSON (/data/config.json)."""
import copy
import json
import os
import secrets
import threading
import time
import uuid

from .refs import normalize_ref, registry_host

DATA_DIR = os.environ.get("PULLPANEL_DATA", "/data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
SECRET_PATH = os.path.join(DATA_DIR, ".session_secret")

UNITS = {"m": 60, "h": 3600, "d": 86400}
MIN_INTERVAL = 5 * 60  # 5 minutos

_lock = threading.RLock()
_cache = None


def _default():
    return {"entries": [], "registries": {}}


def _load():
    global _cache
    if _cache is None:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                _cache = json.load(f)
        except FileNotFoundError:
            _cache = _default()
        _cache.setdefault("entries", [])
        _cache.setdefault("registries", {})
    return _cache


def _save():
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_cache, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)


def get_secret() -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(SECRET_PATH, "r") as f:
            s = f.read().strip()
            if s:
                return s
    except FileNotFoundError:
        pass
    s = secrets.token_hex(32)
    with open(SECRET_PATH, "w") as f:
        f.write(s)
    try:
        os.chmod(SECRET_PATH, 0o600)
    except OSError:
        pass
    return s


# ---------- entradas ----------

def validate_entry(data: dict) -> dict:
    name = (data.get("name") or "").strip()
    image = normalize_ref(data.get("image") or "")
    if not name:
        name = image.split("/")[-1].rsplit(":", 1)[0]
    containers = [c.strip() for c in (data.get("containers") or []) if c and c.strip()]
    auto = data.get("auto") or {}
    every = int(auto.get("every") or 6)
    unit = auto.get("unit") or "h"
    if unit not in UNITS:
        raise ValueError("Unidad de tiempo inválida")
    if every < 1:
        raise ValueError("El intervalo debe ser mayor a 0")
    enabled = bool(auto.get("enabled"))
    if enabled and every * UNITS[unit] < MIN_INTERVAL:
        raise ValueError("El intervalo automático mínimo es de 5 minutos")
    return {
        "name": name[:80],
        "image": image,
        "containers": containers,
        "recreate": bool(data.get("recreate", True)),
        "auto": {"enabled": enabled, "every": every, "unit": unit},
    }


def list_entries():
    with _lock:
        return copy.deepcopy(_load()["entries"])


def get_entry(eid):
    with _lock:
        for e in _load()["entries"]:
            if e["id"] == eid:
                return copy.deepcopy(e)
    return None


def add_entry(data):
    clean = validate_entry(data)
    with _lock:
        cfg = _load()
        entry = {"id": uuid.uuid4().hex[:10], "created_at": time.time(), **clean}
        cfg["entries"].append(entry)
        _save()
        return copy.deepcopy(entry)


def edit_entry(eid, data):
    clean = validate_entry(data)
    with _lock:
        for e in _load()["entries"]:
            if e["id"] == eid:
                if e["image"] != clean["image"]:
                    # cambió la imagen: el estado del chequeo anterior ya no aplica
                    for k in ("remote_digest", "last_check_at", "last_check_error"):
                        e.pop(k, None)
                if not e.get("auto", {}).get("enabled") and clean["auto"]["enabled"]:
                    e["last_auto_at"] = time.time()  # primer ciclo a partir de ahora
                e.update(clean)
                _save()
                return copy.deepcopy(e)
    raise KeyError(eid)


def update_entry(eid, **fields):
    with _lock:
        for e in _load()["entries"]:
            if e["id"] == eid:
                e.update(fields)
                _save()
                return copy.deepcopy(e)
    return None


def delete_entry(eid):
    with _lock:
        cfg = _load()
        before = len(cfg["entries"])
        cfg["entries"] = [e for e in cfg["entries"] if e["id"] != eid]
        _save()
        return len(cfg["entries"]) != before


# ---------- registros / credenciales ----------

def get_registries_public():
    with _lock:
        out = []
        for host, r in _load()["registries"].items():
            out.append({"host": host, "username": r.get("username", ""), "has_token": bool(r.get("password"))})
        return out


def set_registries(items):
    """items: [{host, username, token}] — token vacío conserva el guardado."""
    with _lock:
        cfg = _load()
        old = cfg["registries"]
        new = {}
        for it in items:
            host = (it.get("host") or "").strip().lower()
            if not host:
                continue
            username = (it.get("username") or "").strip()
            token = it.get("token") or ""
            if not token and host in old:
                token = old[host].get("password", "")
            new[host] = {"username": username, "password": token}
        cfg["registries"] = new
        _save()


def auth_for(ref):
    host = registry_host(ref)
    with _lock:
        r = _load()["registries"].get(host)
        if r and r.get("username") and r.get("password"):
            return {"username": r["username"], "password": r["password"]}
    return None


def interval_seconds(entry):
    a = entry.get("auto") or {}
    return int(a.get("every") or 0) * UNITS.get(a.get("unit") or "h", 3600)
