"""The invented-library timing script documents its optional model measurements."""

import subprocess
import sys


def test_timing_help_explains_model_benchmarks_without_running_the_app():
    result = subprocess.run(
        [sys.executable, "scripts/time_app.py", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--models" in result.stdout
    assert "Launch to first paint" not in result.stdout
