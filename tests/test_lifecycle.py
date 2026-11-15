"""End-to-end: the real entry point exits cleanly on SIGTERM (session logout) and saves settings."""

import json
import os
import signal
import subprocess
import sys
import time


def test_sigterm_quits_cleanly(tmp_path):
    cfg = tmp_path / "config.json"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", HOME=str(tmp_path),
               XDG_CONFIG_HOME=str(tmp_path / "cfg"), XDG_DATA_HOME=str(tmp_path / "data"))
    p = subprocess.Popen([sys.executable, "-m", "jaba_da_hat", "--config", str(cfg)], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.monotonic() + 15
    out = ""
    while time.monotonic() < deadline and "starting" not in out:
        line = p.stdout.readline()
        out += line
        if not line and p.poll() is not None:
            break
    time.sleep(1.0)
    p.send_signal(signal.SIGTERM)
    rest, _ = p.communicate(timeout=10)
    assert p.returncode == 0, out + rest
    assert "termination signal received" in rest
    assert json.loads(cfg.read_text())["theme"] == "system"


def test_version_flag():
    from jaba_da_hat import __version__

    r = subprocess.run([sys.executable, "-m", "jaba_da_hat", "--version"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout.strip() == f"jaba-da-hat {__version__}"
