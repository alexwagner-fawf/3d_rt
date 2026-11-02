"""Corporate proxy / CA settings from secrets/proxy.env (see config/proxy.example.env).

The same file is read by env.sh (shell, pip, git, curl), .Rprofile (R) and here (CAMS/CDS downloads
via requests). Values from the file override inherited proxy variables, so the repository setting
wins everywhere.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from voxsol import REPO_ROOT

PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")
CA_TARGETS = ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE", "PIP_CERT", "GIT_SSL_CAINFO")
SYSTEM_CA = ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/cert.pem")
TEST_URLS = {
    "PyPI": "https://pypi.org/simple/",
    "PyPI files": "https://files.pythonhosted.org/",
    "CRAN": "https://cloud.r-project.org/",
    "CAMS (SoDa)": "https://api.soda-solardata.com/",
    "CDS (ERA5)": "https://cds.climate.copernicus.eu/api",
    "PROJ grids": "https://cdn.proj.org/",
}


def proxy_file() -> Path:
    return Path(os.environ.get("VOXSOL_PROXY_FILE", REPO_ROOT / "secrets" / "proxy.env"))


def read_proxy_file(path: Path | None = None) -> dict[str, str]:
    """KEY=VALUE lines (comments and unknown keys ignored, keys case-insensitive)."""
    path = path or proxy_file()
    if not path.exists():
        return {}
    known = set(PROXY_KEYS) | {"CA_BUNDLE"}
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (s.strip() for s in line.split("=", 1))
        key = key.removeprefix("export ").strip().upper()
        value = value.strip("'\"")
        if key in known and value:
            out[key] = value
    return out


def mask(url: str) -> str:
    """Hide credentials in proxy URLs."""
    return re.sub(r"(://)[^/@]+@", r"\1***@", url)


def ca_bundle(extra_ca: str | Path) -> Path:
    """System CA bundle (or certifi) plus the corporate CA, written to .cache/ca-bundle.pem."""
    extra = Path(extra_ca)
    if not extra.exists():
        raise FileNotFoundError(f"CA_BUNDLE not found: {extra}")
    base = next((Path(p) for p in SYSTEM_CA if Path(p).exists()), None)
    if base is None:
        import certifi

        base = Path(certifi.where())
    out = REPO_ROOT / ".cache" / "ca-bundle.pem"
    stale = not out.exists() or any(p.stat().st_mtime > out.stat().st_mtime for p in (extra, base))
    if stale:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(base.read_text() + "\n" + extra.read_text())
    return out


def apply_proxy_env(path: Path | None = None) -> dict[str, str]:
    """Export the file's settings (upper- and lower-case variants). Returns what was applied."""
    cfg = read_proxy_file(path)
    applied = {}
    for key in PROXY_KEYS:
        if key in cfg:
            os.environ[key] = os.environ[key.lower()] = cfg[key]
            applied[key] = cfg[key]
    if "CA_BUNDLE" in cfg:
        bundle = str(ca_bundle(cfg["CA_BUNDLE"]))
        for key in CA_TARGETS:
            os.environ[key] = bundle
        applied["CA_BUNDLE"] = bundle
    return applied


def describe() -> dict[str, str]:
    """Effective network settings as seen by this process (credentials masked)."""
    keys = PROXY_KEYS + tuple(k.lower() for k in PROXY_KEYS) + CA_TARGETS
    out = {"proxy_file": f"{proxy_file()} ({'found' if proxy_file().exists() else 'not found'})"}
    out.update({k: mask(os.environ[k]) for k in keys if os.environ.get(k)})
    return out


def check_urls(timeout: float = 15.0) -> dict[str, str]:
    """Reach the endpoints the software uses through the configured proxy (any HTTP status = reachable)."""
    import requests

    results = {}
    for name, url in TEST_URLS.items():
        try:
            r = requests.head(url, timeout=timeout, allow_redirects=True)
            results[name] = f"ok (HTTP {r.status_code})"
        except requests.exceptions.ProxyError as exc:
            results[name] = f"PROXY ERROR: {type(exc.__cause__ or exc).__name__}"
        except requests.exceptions.SSLError:
            results[name] = "TLS ERROR (corporate CA missing? set CA_BUNDLE)"
        except requests.exceptions.RequestException as exc:
            results[name] = f"FAILED: {type(exc).__name__}"
    return results
