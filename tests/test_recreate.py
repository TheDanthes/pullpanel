"""Tests de la lógica pura de recreación (no necesitan Docker)."""
from app.dockerops import build_create_body
from app.refs import normalize_ref, registry_host

SHORT = "abcdef123456"
INFO = {
    "Id": SHORT + "0" * 52,
    "Name": "/jsdr-api",
    "Image": "sha256:old",
    "Config": {
        "Hostname": SHORT,
        "Image": "ghcr.io/thedanthes/jsdr-api:latest",
        "Env": ["PATH=/usr/bin", "NODE_ENV=production", "PORT=3000"],
        "Cmd": ["node", "server.js"],
        "Entrypoint": ["docker-entrypoint.sh"],
        "Labels": {"com.docker.compose.project": "jsdr-api", "org.opencontainers.image.version": "1.0"},
        "ExposedPorts": {"3000/tcp": {}},
        "WorkingDir": "/app",
    },
    "HostConfig": {
        "NetworkMode": "jsdr-api_default",
        "Binds": ["/DATA/AppData/jsdr-api:/data:rw"],
        "Mounts": [{"Type": "volume", "Target": "/cache", "VolumeOptions": {}}],
        "RestartPolicy": {"Name": "unless-stopped"},
        "PortBindings": {"3000/tcp": [{"HostIp": "", "HostPort": "3000"}]},
    },
    "Mounts": [
        {"Type": "bind", "Source": "/DATA/AppData/jsdr-api", "Destination": "/data", "RW": True},
        {"Type": "volume", "Name": "f" * 64, "Destination": "/cache", "RW": True},
        {"Type": "volume", "Name": "e" * 64, "Destination": "/img-vol", "RW": True},
    ],
    "NetworkSettings": {"Networks": {
        "jsdr-api_default": {"Aliases": ["jsdr-api", "api", SHORT], "IPAddress": "172.18.0.2",
                              "NetworkID": "x", "EndpointID": "y", "MacAddress": "02:42"},
        "otra": {"Aliases": None, "IPAMConfig": {"IPv4Address": "10.0.0.5"}},
    }},
}
IMG_CFG = {
    "Env": ["PATH=/usr/bin", "NODE_ENV=production"],
    "Cmd": ["node", "server.js"],
    "Entrypoint": ["docker-entrypoint.sh"],
    "Labels": {"org.opencontainers.image.version": "1.0"},
    "ExposedPorts": {"3000/tcp": {}},
    "WorkingDir": "/app",
    "Volumes": {"/img-vol": {}},
}


def test_body_keeps_user_config_and_drops_image_defaults():
    body, extra = build_create_body(INFO, IMG_CFG, "ghcr.io/thedanthes/jsdr-api:latest")
    assert body["Env"] == ["PORT=3000"]
    assert body["Cmd"] is None and body["Entrypoint"] is None
    assert body["Labels"] == {"com.docker.compose.project": "jsdr-api"}
    assert "Hostname" not in body
    assert "WorkingDir" not in body
    assert body["HostConfig"]["PortBindings"]["3000/tcp"][0]["HostPort"] == "3000"
    assert body["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped"


def test_anonymous_volumes_are_preserved():
    body, _ = build_create_body(INFO, IMG_CFG, "x:latest")
    hc = body["HostConfig"]
    assert hc["Mounts"][0]["Source"] == "f" * 64          # declarado por compose sin nombre
    assert f"{'e' * 64}:/img-vol" in hc["Binds"]          # volumen de la imagen
    assert "/DATA/AppData/jsdr-api:/data:rw" in hc["Binds"]


def test_networks():
    body, extra = build_create_body(INFO, IMG_CFG, "x:latest")
    ep = body["NetworkingConfig"]["EndpointsConfig"]["jsdr-api_default"]
    assert ep == {"Aliases": ["jsdr-api", "api"]}
    assert extra == [("otra", {"IPAMConfig": {"IPv4Address": "10.0.0.5"}})]


def test_host_network_mode():
    info = {**INFO, "HostConfig": {**INFO["HostConfig"], "NetworkMode": "host"}}
    body, extra = build_create_body(info, IMG_CFG, "x:latest")
    assert "NetworkingConfig" not in body and extra == []
    assert "Hostname" not in body


def test_refs():
    assert normalize_ref("ghcr.io/thedanthes/jsdr-api") == "ghcr.io/thedanthes/jsdr-api:latest"
    assert normalize_ref("localhost:5000/app") == "localhost:5000/app:latest"
    assert registry_host("ghcr.io/thedanthes/jsdr-api:latest") == "ghcr.io"
    assert registry_host("nginx:latest") == "docker.io"
    assert registry_host("library/nginx:1") == "docker.io"
