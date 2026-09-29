"""The sandbox: the API on a throwaway archive of invented scans, with fake OCR/LLM models.

    .venv/Scripts/python sandbox.py            start the sandbox API; --fresh starts from the shared state
    .venv/Scripts/python sandbox.py reset      put the sandbox back into the shared state (stop the API first)

Then `npm --prefix frontend run dev:sandbox` for its web app. What it runs on, and why it never touches the
live archive: papertrail/dev/sandbox_server.py.
"""
import argparse
import runpy
import sys

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "reset"])
    parser.add_argument("--fresh", action="store_true", help="serve: rebuild the shared state first")
    args = parser.parse_args()
    if args.command == "reset":
        from papertrail.dev.sandbox_reset import main
        sys.exit(main())
    runpy.run_module("papertrail.dev.sandbox_server", run_name="__main__")   # reads --fresh itself
