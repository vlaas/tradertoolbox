"""The backend: ``python -m s3t [--config PATH]``.

It reads the configuration, opens its socket, the broker and the stores, and serves until SIGINT or
SIGTERM. It is an ordinary user process (SEC-06). What keeps it from starting is one line on the
standard error, and the exit status 1.
"""

import argparse
import asyncio
import contextlib
import logging
import os
import socket
import sys
from pathlib import Path

from s3t.api.app import create_app
from s3t.api.server import listen, serve
from s3t.broker.interface import BrokerError
from s3t.config import Config, ConfigError, load_config
from s3t.secrets import SecretsError, install_redaction, redact
from s3t.store.migrate import StoreError
from s3t.system.backend import open_backend

LOG = logging.getLogger("s3t")


def install_logging() -> None:
    """One handler for the whole process, which redacts (SEC-05). uvicorn's loggers reach it too."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    install_redaction(handler)
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # One line per request to the broker says nothing that the status does not.
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def run(config: Config, sock: socket.socket) -> None:
    async with open_backend(config) as backend:
        await serve(create_app(backend), sock)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="s3t", description="The Self-3-Trader backend.")
    parser.add_argument("--config", type=Path, help="the configuration file")
    args = parser.parse_args(argv)
    install_logging()
    try:
        config = load_config(args.config)
        server = config.server
        try:
            sock = listen(server)
        except OSError as e:
            where = f"{server.listen_address} port {server.port}"
            print(f"cannot listen on {where}: {os.strerror(e.errno or 0)}", file=sys.stderr)
            return 1
        with contextlib.closing(sock):
            LOG.info("listening on %s port %d", server.listen_address, server.port)
            asyncio.run(run(config, sock))
    except (ConfigError, SecretsError, BrokerError, StoreError) as e:
        print(redact(str(e)), file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # before the server took over the signals
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
