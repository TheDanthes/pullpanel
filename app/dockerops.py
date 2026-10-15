"""Operaciones contra Docker: chequeo de digest, pull, recreación y limpieza."""
import copy
import os
import re
import time

import docker
from docker.errors import ImageNotFound, NotFound

from .refs import split_ref

_client = None


def client():
    global _client
    if _client is None:
        _client = docker.DockerClient(
            base_url=os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock"), timeout=300
        )
    return _client


def api():
    return client().api


# ---------- identidad propia ----------

_self_id = None


def self_id():
    """ID del contenedor donde corre PullPanel (None si no corre en Docker)."""
    global _self_id
    if _self_id is not None:
        return _self_id or None
    found = ""
    for path in ("/proc/self/mountinfo", "/proc/self/cgroup"):
        try:
            with open(path) as f:
                m = re.search(r"/(?:docker/)?containers/([0-9a-f]{64})/", f.read())
                if m:
                    found = m.group(1)
                    break
        except OSError:
            pass
    if not found:
        try:
            host = open("/etc/hostname").read().strip()
            found = api().inspect_container(host)["Id"]
        except Exception:
            found = ""
    _self_id = found
    return found or None


# ---------- consultas ----------

def local_image(ref):
    try:
        return api().inspect_image(ref)
    except (ImageNotFound, NotFound):
        return None


def digests_of(image_attrs):
    out = set()
    for d in (image_attrs or {}).get("RepoDigests") or []:
        if "@" in d:
            out.add(d.split("@", 1)[1])
    return out


def remote_digest(ref, auth=None):
    rd = client().images.get_registry_data(ref, auth_config=auth)
    return rd.id


def containers_list():
    out = []
    for c in api().containers(all=True):
        try:
            info = api().inspect_container(c["Id"])
        except NotFound:
            continue
        labels = info["Config"].get("Labels") or {}
        out.append({
            "id": info["Id"][:12],
            "name": info["Name"].lstrip("/"),
            "image": info["Config"].get("Image") or c.get("Image"),
            "image_id": info["Image"],
            "state": info["State"].get("Status"),
            "project": labels.get("com.docker.compose.project"),
            "is_self": info["Id"] == self_id(),
        })
    out.sort(key=lambda x: x["name"].lower())
    return out


def container_brief(name):
    try:
        info = api().inspect_container(name)
    except NotFound:
        return None
    return {"name": info["Name"].lstrip("/"), "id": info["Id"], "image_id": info["Image"],
            "state": info["State"].get("Status")}


# ---------- pull ----------

_NOISY = {"Downloading", "Extracting", "Waiting", "Verifying Checksum", "Pulling fs layer"}


def pull(ref, auth, log):
    repo, tag = split_ref(ref)
    seen = set()
    for ev in api().pull(repo, tag=tag, stream=True, decode=True, auth_config=auth):
        if ev.get("error"):
            raise RuntimeError(ev["error"])
        status = ev.get("status") or ""
        lid = ev.get("id")
        if status in _NOISY:
            continue
        key = (lid, status)
        if key in seen:
            continue
        seen.add(key)
        if lid and lid != tag and not status.startswith("Pulling from"):
            log(f"  {lid}: {status}")
        else:
            log(status)
    img = local_image(ref)
    if not img:
        raise RuntimeError("El pull terminó pero la imagen no aparece localmente")
    return img


# ---------- recreación (estilo Watchtower) ----------

def _strip_image_defaults(cfg, img_cfg):
    """Quita del Config lo que venía de la imagen vieja, para que la nueva
    imagen aporte sus propios valores por defecto."""
    img_cfg = img_cfg or {}

    img_env = set(img_cfg.get("Env") or [])
    env = [e for e in (cfg.get("Env") or []) if e not in img_env]
    cfg["Env"] = env or None

    img_labels = img_cfg.get("Labels") or {}
    labels = {k: v for k, v in (cfg.get("Labels") or {}).items() if img_labels.get(k) != v}
    cfg["Labels"] = labels or None

    if cfg.get("Entrypoint") == img_cfg.get("Entrypoint"):
        cfg["Entrypoint"] = None
        if cfg.get("Cmd") == img_cfg.get("Cmd"):
            cfg["Cmd"] = None
    # si el entrypoint fue sobreescrito, Cmd queda tal cual

    for key in ("WorkingDir", "User", "StopSignal"):
        if cfg.get(key) == img_cfg.get(key):
            cfg.pop(key, None)
    if cfg.get("Healthcheck") == img_cfg.get("Healthcheck"):
        cfg.pop("Healthcheck", None)

    for key in ("ExposedPorts", "Volumes"):
        img_keys = set((img_cfg.get(key) or {}).keys())
        rest = {k: v for k, v in (cfg.get(key) or {}).items() if k not in img_keys}
        cfg[key] = rest or None
    return cfg


_EP_KEEP = ("IPAMConfig", "Links", "Aliases", "DriverOpts")


def _clean_endpoint(ep, short_id, name):
    out = {k: copy.deepcopy(ep[k]) for k in _EP_KEEP if ep.get(k)}
    if out.get("Aliases"):
        seen = []
        for a in out["Aliases"]:
            if a != short_id and a not in seen:
                seen.append(a)
        out["Aliases"] = seen or None
        if out["Aliases"] is None:
            out.pop("Aliases")
    return out


def build_create_body(info, old_image_cfg, image_ref):
    """Arma el cuerpo de /containers/create a partir del inspect del contenedor viejo.
    Devuelve (body, redes_extra) donde redes_extra = [(red, endpoint_config)]."""
    short_id = info["Id"][:12]
    name = info["Name"].lstrip("/")
    cfg = _strip_image_defaults(copy.deepcopy(info["Config"]), old_image_cfg)
    cfg["Image"] = image_ref
    host = copy.deepcopy(info["HostConfig"])
    net_mode = host.get("NetworkMode") or "default"

    if cfg.get("Hostname") == short_id:
        cfg.pop("Hostname", None)
    shared_net = net_mode == "host" or net_mode.startswith("container:") or net_mode == "none"
    if shared_net:
        for k in ("Hostname", "Domainname", "MacAddress"):
            cfg.pop(k, None)
        if net_mode != "none":
            cfg.pop("ExposedPorts", None)
    cfg.pop("MacAddress", None)

    # Preservar volúmenes anónimos (si no, el contenedor nuevo crea volúmenes vacíos)
    actual_by_dest = {m.get("Destination"): m for m in info.get("Mounts") or []}
    for m in host.get("Mounts") or []:
        if m.get("Type") == "volume" and not m.get("Source"):
            real = actual_by_dest.get(m.get("Target"))
            if real and real.get("Name"):
                m["Source"] = real["Name"]  # compose los declara sin nombre
    declared = set()
    for b in host.get("Binds") or []:
        parts = b.split(":")
        if len(parts) >= 2:
            declared.add(parts[1])
    for m in host.get("Mounts") or []:
        declared.add(m.get("Target"))
    extra_binds = []
    for m in info.get("Mounts") or []:
        if m.get("Type") == "volume" and m.get("Name") and m.get("Destination") not in declared:
            extra_binds.append(f"{m['Name']}:{m['Destination']}" + ("" if m.get("RW", True) else ":ro"))
    if extra_binds:
        host["Binds"] = (host.get("Binds") or []) + extra_binds

    networks = (info.get("NetworkSettings") or {}).get("Networks") or {}
    body = dict(cfg)
    body["HostConfig"] = host
    extra = []
    if not shared_net and networks:
        primary = net_mode if net_mode in networks else ("bridge" if net_mode == "default" and "bridge" in networks else next(iter(networks)))
        body["NetworkingConfig"] = {"EndpointsConfig": {primary: _clean_endpoint(networks[primary], short_id, name)}}
        for net, ep in networks.items():
            if net != primary:
                extra.append((net, _clean_endpoint(ep, short_id, name)))
    return body, extra


def _create_raw(body, name):
    a = api()
    res = a._post_json(a._url("/containers/create"), data=body, params={"name": name})
    return a._result(res, True)["Id"]


def _connect_raw(net, cid, ep):
    a = api()
    res = a._post_json(a._url("/networks/{0}/connect", net), data={"Container": cid, "EndpointConfig": ep})
    a._raise_for_status(res)


def recreate(container, image_ref, log, settle_seconds=4):
    """Recrea `container` con `image_ref` conservando su configuración.
    Si el contenedor nuevo no arranca, vuelve al viejo."""
    a = api()
    info = a.inspect_container(container)
    name = info["Name"].lstrip("/")
    was_running = bool(info["State"].get("Running"))
    try:
        old_img_cfg = a.inspect_image(info["Image"]).get("Config") or {}
    except (ImageNotFound, NotFound):
        old_img_cfg = {}
    body, extra_nets = build_create_body(info, old_img_cfg, image_ref)
    stop_timeout = info["Config"].get("StopTimeout") or 10
    backup_name = f"{name}-pullpanel-old-{int(time.time())}"

    if was_running:
        log(f"{name}: deteniendo contenedor actual…")
        a.stop(info["Id"], timeout=stop_timeout)
    a.rename(info["Id"], backup_name)

    new_id = None
    try:
        log(f"{name}: creando contenedor con la imagen nueva…")
        new_id = _create_raw(body, name)
        for net, ep in extra_nets:
            _connect_raw(net, new_id, ep)
        if was_running:
            a.start(new_id)
            time.sleep(settle_seconds)
            st = a.inspect_container(new_id)["State"]
            if not st.get("Running") or st.get("Restarting"):
                logs = a.logs(new_id, tail=15).decode("utf-8", "replace").strip()
                if logs:
                    log("Últimas líneas del contenedor nuevo:\n" + logs)
                raise RuntimeError(f"el contenedor nuevo no quedó corriendo (estado {st.get('Status')}, código {st.get('ExitCode')})")
    except Exception as exc:
        log(f"{name}: falló ({exc}). Restaurando el contenedor anterior…")
        if new_id:
            try:
                a.remove_container(new_id, force=True)
            except Exception:
                pass
        a.rename(info["Id"], name)
        if was_running:
            a.start(info["Id"])
        log(f"{name}: contenedor anterior restaurado.")
        raise

    a.remove_container(info["Id"], v=False)
    log(f"{name}: actualizado ✓" + ("" if was_running else " (estaba detenido, se dejó detenido)"))
    return new_id


def launch_self_update(image_ref, log):
    """PullPanel no puede recrearse a sí mismo desde adentro: lanza un contenedor
    auxiliar efímero (con la imagen nueva) que hace la recreación."""
    a = api()
    me = self_id()
    info = a.inspect_container(me)
    sock_bind = None
    for m in info.get("Mounts") or []:
        if m.get("Destination") == "/var/run/docker.sock":
            sock_bind = f"{m['Source']}:/var/run/docker.sock"
    sock_bind = sock_bind or "/var/run/docker.sock:/var/run/docker.sock"
    helper_name = f"pullpanel-selfupdate-{int(time.time())}"
    hc = a.create_host_config(binds=[sock_bind], auto_remove=True)
    helper = a.create_container(
        image=image_ref, name=helper_name, host_config=hc,
        command=["python", "-m", "app.recreate_cli", me, image_ref],
        labels={"pullpanel.helper": "1"},
    )
    a.start(helper["Id"])
    log("PullPanel se va a reiniciar con la versión nueva. Recargá la página en unos 30 segundos.")


# ---------- imágenes / limpieza ----------

def images_list():
    a = api()
    used = {}
    for c in a.containers(all=True):
        used.setdefault(c["ImageID"], []).append(c["Names"][0].lstrip("/"))
    out = []
    for im in a.images():
        tags = [t for t in (im.get("RepoTags") or []) if t and t != "<none>:<none>"]
        repos = sorted({d.split("@", 1)[0] for d in (im.get("RepoDigests") or []) if "@" in d})
        users = used.get(im["Id"], [])
        out.append({
            "id": im["Id"],
            "short_id": im["Id"].split(":")[-1][:12],
            "tags": tags,
            "repos": repos,
            "size": im.get("Size") or 0,
            "created": im.get("Created") or 0,
            "containers": users,
            "dangling": not tags,
        })
    out.sort(key=lambda x: (bool(x["containers"]), not x["dangling"], -x["created"]))
    return out


def delete_image(image_id):
    a = api()
    info = a.inspect_image(image_id)
    tags = [t for t in (info.get("RepoTags") or []) if t and t != "<none>:<none>"]
    if len(tags) > 1:
        # una imagen con varios tags solo se borra quitando cada tag
        for t in tags:
            a.remove_image(t, force=False, noprune=False)
    else:
        a.remove_image(info["Id"], force=False, noprune=False)
