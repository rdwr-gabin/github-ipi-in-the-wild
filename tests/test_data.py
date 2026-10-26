import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_tables_agree_with_findings():
    """The published tables must be reproducible from the published findings."""
    run = subprocess.run([sys.executable, str(ROOT / "scripts/summarize.py"), "--check"], capture_output=True, text=True,
                         encoding="utf-8")
    assert run.returncode == 0, run.stdout[-2000:]
