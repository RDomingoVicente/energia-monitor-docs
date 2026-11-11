"""Punto de entrada del contenedor.

Esqueleto: mantiene el proceso vivo hasta que se implemente el colector.
"""

import logging
import signal
import threading

from energia_monitor import __version__


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.info("energia-monitor %s: esqueleto, colector sin implementar", __version__)

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait()
    logging.info("parada solicitada")


if __name__ == "__main__":
    main()
