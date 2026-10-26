"""Run the play page's JavaScript against DOM stubs (skipped when node is not installed)."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PAGE = ROOT / "src" / "pco" / "play" / "static" / "index.html"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_page_flows_against_a_fake_server():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "ui_check.js"), str(PAGE)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr + out.stdout
    assert "ui checks passed" in out.stdout
