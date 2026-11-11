"""CLI wrapper for the read-only chirp reference quality audit."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from laboratory.quality_audit import main

if __name__ == "__main__":
    raise SystemExit(main())
