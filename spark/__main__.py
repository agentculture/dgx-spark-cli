"""Entry point for ``python -m spark``."""

from __future__ import annotations

import sys

from spark.cli import main

if __name__ == "__main__":
    sys.exit(main())
