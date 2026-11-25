"""Ammeter QA framework.

Every entry point imports this package early, so an unsupported interpreter fails here with
a clear message instead of a confusing error deep inside the code.
"""

import sys

MIN_PYTHON = (3, 10)

if sys.version_info < MIN_PYTHON:
    sys.exit(
        f"This project needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer; "
        f"this is Python {sys.version.split()[0]} ({sys.executable}).\n"
        "Create the virtual environment with a newer Python, e.g. on Windows:\n"
        "    py -3.11 -m venv .venv"
    )
