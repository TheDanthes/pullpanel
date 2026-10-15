"""Utilidades para referencias de imagen (registro/repo:tag)."""


def normalize_ref(ref: str) -> str:
    ref = (ref or "").strip()
    if not ref:
        raise ValueError("La imagen no puede estar vacía")
    if "@" in ref:
        raise ValueError("Usá una referencia por tag (repo:tag), no por digest (@sha256)")
    if " " in ref:
        raise ValueError("La referencia de imagen no puede tener espacios")
    last = ref.rsplit("/", 1)[-1]
    if ":" not in last:
        ref += ":latest"
    return ref


def split_ref(ref: str):
    """Devuelve (repo, tag) de una referencia ya normalizada."""
    repo, tag = ref.rsplit(":", 1)
    return repo, tag


def registry_host(ref: str) -> str:
    first = ref.split("/", 1)[0]
    if "/" in ref and ("." in first or ":" in first or first == "localhost"):
        return first.lower()
    return "docker.io"
