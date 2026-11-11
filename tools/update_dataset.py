"""CLI for safely refreshing corrected manual annotations in an existing dataset."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from laboratory.dataset_refresh import main

if __name__ == "__main__":
    raise SystemExit(main())
