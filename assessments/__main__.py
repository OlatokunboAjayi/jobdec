"""CLI entry point for `python -m assessments`."""

import sys
from assessments.orchestrator import main

if __name__ == "__main__":
    sys.exit(main())

