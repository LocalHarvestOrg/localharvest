"""Entry point of the frozen sidecar: `table-extract serve`, `table-extract run run.json`.

The same command line as `python -m table_extract`; the sidecar starts runs by
calling this binary again with `run` (table_extract/server/runs.py).
"""

import sys

from table_extract.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
