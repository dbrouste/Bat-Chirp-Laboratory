"""CLI for synthetic stress tests of continuous perpendicular tracking."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from laboratory.synthetic_ridge_stress import main
if __name__ == "__main__":
    raise SystemExit(main())
