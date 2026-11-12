"""Launch the resumable local annotation QA campaign on Windows."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from laboratory.local_campaign import main

if __name__=="__main__":
    raise SystemExit(main())
