"""Put this checkout's sandbox back into the shared state (papertrail/dev/sandbox_seed.py), whatever was done in it.

    .venv/Scripts/python sandbox.py reset

Stop the sandbox API first: the server holds the archive open and caches what it read, so the sandbox is
never rebuilt under a running one (the script says so and changes nothing). Start it again afterwards; the
web app can keep running. Only <checkout>/.sandbox is touched, never the live archive.
"""
import socket
import sys
from pathlib import Path

from papertrail.dev.ports import NotSetUp, checkout_ports

REPO = Path(__file__).resolve().parents[2]


def main() -> int:
    try:
        port = checkout_ports(REPO).sandbox_api
    except NotSetUp as not_set_up:
        print(not_set_up, file=sys.stderr)
        return 1
    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            print(f"The sandbox API is running on port {port}: stop it, then run this again.", file=sys.stderr)
            return 1

    from papertrail.dev import sandbox_seed as seed
    seed.prepare(REPO / ".sandbox", fresh=True)
    print("The sandbox is back in the shared state. Start the sandbox API again to use it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
