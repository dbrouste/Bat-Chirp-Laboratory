"""CLI for second-generation perpendicular continuous-ridge acoustic audit."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from laboratory.acoustic_audit_v2 import main

if __name__ == "__main__":
    raise SystemExit(main())
