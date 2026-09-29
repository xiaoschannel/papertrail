"""Deploy the live app: bring the main checkout up to date with main and restart it, safely (Windows).

    <main checkout>/.venv/Scripts/python deploy.py [restart | stop | status | install | autostart on|off]

What each command does, and what makes a deploy refuse, is in papertrail/dev/deploy.py.
"""
import sys

from papertrail.dev.deploy import main

if __name__ == "__main__":
    sys.exit(main())
