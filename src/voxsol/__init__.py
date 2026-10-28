"""voxsol: ULS point cloud based below-canopy insolation as a microclimate covariate."""

import os
from pathlib import Path

__version__ = "0.1.0"

REPO_ROOT = Path(__file__).resolve().parents[2]


def ensure_local_env() -> None:
    """Point caches and temp dirs into the repository (mirror of env.sh, for runs without it).

    Must run before numba/pyproj are imported to take full effect.
    """
    cache = REPO_ROOT / ".cache"
    defaults = {
        "TMPDIR": cache / "tmp",
        "NUMBA_CACHE_DIR": cache / "numba",
        "XDG_CACHE_HOME": cache / "xdg",
        "MPLCONFIGDIR": cache / "xdg" / "matplotlib",
        "PROJ_USER_WRITABLE_DIRECTORY": cache / "proj",
    }
    for key, path in defaults.items():
        if key not in os.environ:
            path.mkdir(parents=True, exist_ok=True)
            os.environ[key] = str(path)
    os.environ.setdefault("PROJ_NETWORK", "OFF")
    from voxsol.netconfig import apply_proxy_env

    apply_proxy_env()  # secrets/proxy.env, for CAMS/CDS downloads when env.sh was not sourced
