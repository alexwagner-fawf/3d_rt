"""TMS height labels: file and database spellings map to the same sensor, in Python and in R."""

import json
import shutil
import subprocess

import pytest

from voxsol import REPO_ROOT
from voxsol.config import CalibrationParams
from voxsol.io.loggers import height_key, sensor_by_height_key

CASES = {
    "15": "15", "+15 cm": "15", "air 15 cm": "15", "Air 15cm": "15", "15.0": "15", "Luft 15 cm": "15",
    "2": "2", "air 2 cm": "2",
    "-8": "-8", "soil 8 cm": "-8", "Boden 8 cm": "-8",
    "-0-10cm": "-0-10", "soil 0-10 cm": "-0-10", "soil 0-15 cm": "-0-15",
    "moisture": "moisture", "T 3": "t3",
}


def test_height_key():
    assert {c: height_key(c) for c in CASES} == CASES


def test_default_sensors_cover_file_and_database_labels():
    by_key = sensor_by_height_key(CalibrationParams().sensors)
    for label, sensor in {"15": "T3", "air 15 cm": "T3", "air 2 cm": "T2", "soil 8 cm": "T1", "-8": "T1",
                          "-0-10cm": "VWC", "soil 0-15 cm": "VWC"}.items():
        assert by_key[height_key(label)] == sensor


def test_one_sensor_per_label():
    with pytest.raises(ValueError, match="given for T3 and T2"):
        sensor_by_height_key({"T3": "15", "T2": "air 15 cm"})


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R not installed")
def test_r_height_key_matches_python():
    labels = json.dumps(list(CASES))
    code = f"""
    source("R/loggers.R")
    x <- jsonlite::fromJSON('{labels}')
    cat(jsonlite::toJSON(stats::setNames(as.list(height_key(x)), x), auto_unbox = TRUE))
    """
    if subprocess.run(["Rscript", "-e", "quit(status = !requireNamespace('jsonlite', quietly = TRUE))"],
                      cwd=REPO_ROOT).returncode:
        pytest.skip("jsonlite not installed")
    out = subprocess.run(["Rscript", "-e", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == CASES
