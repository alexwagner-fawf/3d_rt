import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # tests/synthetic.py helpers

from voxsol import ensure_local_env  # noqa: E402

ensure_local_env()
