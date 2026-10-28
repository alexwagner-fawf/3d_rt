"""Proxy configuration: parsing, application in Python/bash/R, and routing through the proxy.

The routing tests point the proxy at a closed local port, so a ProxyError proves that traffic would
go through the configured proxy; no external connection is made.
"""

import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
import requests

from voxsol import REPO_ROOT, netconfig

PROXY_KEYS = ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
              *netconfig.CA_TARGETS]


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def clean_env(monkeypatch):
    for key in PROXY_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def write_proxy_file(path: Path, proxy: str, ca: Path | None = None) -> Path:
    lines = ["# comment", f"export HTTP_PROXY={proxy}", f"https_proxy='{proxy}'",
             "NO_PROXY=localhost,.corp.local", "UNRELATED=ignored", ""]
    if ca:
        lines.append(f"CA_BUNDLE={ca}")
    path.write_text("\n".join(lines))
    return path


def test_read_and_mask(tmp_path):
    f = write_proxy_file(tmp_path / "proxy.env", "http://user:p%40ss@proxy.corp:8080")
    cfg = netconfig.read_proxy_file(f)
    assert cfg == {"HTTP_PROXY": "http://user:p%40ss@proxy.corp:8080",
                   "HTTPS_PROXY": "http://user:p%40ss@proxy.corp:8080", "NO_PROXY": "localhost,.corp.local"}
    assert netconfig.mask(cfg["HTTP_PROXY"]) == "http://***@proxy.corp:8080"


def test_apply_overrides_inherited_and_builds_ca_bundle(tmp_path, clean_env):
    clean_env.setattr(netconfig, "REPO_ROOT", tmp_path)
    clean_env.setenv("HTTPS_PROXY", "http://inherited:3128")
    ca = tmp_path / "corp-ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\nCORPORATE\n-----END CERTIFICATE-----\n")
    applied = netconfig.apply_proxy_env(write_proxy_file(tmp_path / "proxy.env", "http://proxy.corp:8080", ca))
    assert os.environ["HTTPS_PROXY"] == os.environ["https_proxy"] == "http://proxy.corp:8080"
    assert os.environ["no_proxy"] == "localhost,.corp.local"
    bundle = Path(applied["CA_BUNDLE"])
    assert bundle == tmp_path / ".cache" / "ca-bundle.pem"
    text = bundle.read_text()
    assert "CORPORATE" in text and text.count("BEGIN CERTIFICATE") > 1  # system/certifi roots + corporate CA
    assert all(os.environ[k] == str(bundle) for k in netconfig.CA_TARGETS)


def test_missing_file_changes_nothing(tmp_path, clean_env):
    assert netconfig.apply_proxy_env(tmp_path / "absent.env") == {}
    assert "HTTPS_PROXY" not in os.environ


def test_python_requests_are_routed_through_proxy(tmp_path, clean_env):
    netconfig.apply_proxy_env(write_proxy_file(tmp_path / "proxy.env", f"http://127.0.0.1:{closed_port()}"))
    with pytest.raises(requests.exceptions.ProxyError):
        requests.head("https://api.soda-solardata.com/", timeout=5)


def test_env_sh_exports_proxy(tmp_path):
    f = write_proxy_file(tmp_path / "proxy.env", "http://u:secret@proxy.corp:8080")
    out = subprocess.run(
        ["bash", "-c", 'source env.sh >/dev/null; echo "$https_proxy|$HTTP_PROXY|$no_proxy|${REQUESTS_CA_BUNDLE:-}"'],
        cwd=REPO_ROOT, env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "VOXSOL_PROXY_FILE": str(f)},
        capture_output=True, text=True, check=True).stdout.strip()
    assert out == "http://u:secret@proxy.corp:8080|http://u:secret@proxy.corp:8080|localhost,.corp.local|"


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R not installed")
def test_rprofile_routes_r_downloads_through_proxy(tmp_path):
    f = write_proxy_file(tmp_path / "proxy.env", f"http://127.0.0.1:{closed_port()}")
    code = ('cat(Sys.getenv("https_proxy"), "\\n");'
            'r <- tryCatch(curlGetHeaders("https://cloud.r-project.org/"),'
            '              error = function(e) gsub("\\\\s+", " ", conditionMessage(e)));'
            'cat(if (is.character(r) && length(r) == 1) r else "connected", "\\n")')
    env = {**os.environ, "VOXSOL_PROXY_FILE": str(f)}
    for key in PROXY_KEYS:
        env.pop(key, None)
    out = subprocess.run(["Rscript", "-e", code], cwd=REPO_ROOT, env=env, capture_output=True, text=True,
                         timeout=60).stdout
    lines = out.strip().splitlines()
    assert lines[0].strip().startswith("http://127.0.0.1:")
    # libcurl error 7 (could not connect) to the proxy address: the request went to the proxy
    assert lines[1].strip() != "connected" and "127.0.0.1" in lines[1]
