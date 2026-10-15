"""Usado por el contenedor auxiliar de auto-actualización:
python -m app.recreate_cli <id_contenedor> <imagen>"""
import sys
import time

from . import dockerops


def main():
    target, image = sys.argv[1], sys.argv[2]
    time.sleep(3)  # dejar que PullPanel termine de responder
    dockerops.recreate(target, image, lambda m: print(m, flush=True), settle_seconds=8)


if __name__ == "__main__":
    main()
