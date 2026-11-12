"""CLI for the non-destructive quality-audit sensitivity experiment."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from laboratory.sensitivity_audit import main
if __name__ == "__main__":
    raise SystemExit(main())
