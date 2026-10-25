"""CLI shim: regenerate the demo database (PRD §6 module layout).

Run with ``--reset`` to rebuild from the seeded constants:

    python agent/scripts/generate_data.py --reset
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow ``python agent/scripts/generate_data.py`` from anywhere: the store
# module lives one directory up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from store import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
