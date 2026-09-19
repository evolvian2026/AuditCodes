import sys
from pathlib import Path

import pytest

from auditcodes.exec.runner import Limits, LocalRunner, Status

PY = sys.executable


def _run_py(runner, tmp_path: Path, code: str, stdin=b"", **limit_kw):
    (tmp_path / "prog.py").write_text(code)
    return runner.run([PY, "prog.py"], cwd=tmp_path, stdin=stdin, limits=Limits(**limit_kw))


def test_ok_and_io(runner, tmp_path):
    r = _run_py(runner, tmp_path, "import sys; d = sys.stdin.buffer.read(); sys.stdout.write(d.decode()[::-1]); sys.stderr.write('warn')", b"abc")
    assert r.status is Status.OK
    assert r.stdout == b"cba"
    assert r.stderr == b"warn"
    assert r.exit_code == 0
    assert r.cpu_seconds >= 0 and r.max_rss_bytes > 0


def test_large_stdin_does_not_deadlock(runner, tmp_path):
    data = b"x" * (4 * 1024 * 1024)
    r = _run_py(runner, tmp_path, "import sys; print(len(sys.stdin.buffer.read()))", data)
    assert r.status is Status.OK
    assert r.stdout.strip() == str(len(data)).encode()


def test_wall_timeout_kills_process(runner, tmp_path):
    r = _run_py(runner, tmp_path, "import time; time.sleep(30)", wall_seconds=1.0, cpu_seconds=5)
    assert r.status is Status.TIMEOUT
    assert r.wall_seconds < 5


def test_cpu_timeout(runner, tmp_path):
    r = _run_py(runner, tmp_path, "while True: pass", wall_seconds=20, cpu_seconds=1.0)
    assert r.status is Status.TIMEOUT
    assert "CPU" in r.reason


def test_memory_limit(runner, tmp_path):
    r = _run_py(runner, tmp_path, "x = bytearray(600 * 1024 * 1024); print(len(x))", memory_bytes=128 * 1024 * 1024)
    assert r.status is Status.MEMORY_LIMIT


def test_runtime_error(runner, tmp_path):
    r = _run_py(runner, tmp_path, "raise SystemExit(3)")
    assert r.status is Status.RUNTIME_ERROR
    assert r.exit_code == 3
    r = _run_py(runner, tmp_path, "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)")
    assert r.status is Status.RUNTIME_ERROR
    assert r.signal == 11 and "SIGSEGV" in r.reason


def test_output_limit(runner, tmp_path):
    r = _run_py(runner, tmp_path, "import sys\nwhile True: sys.stdout.write('y' * 65536)", output_bytes=1024 * 1024, wall_seconds=10)
    assert r.status is Status.OUTPUT_LIMIT
    assert len(r.stdout) == 1024 * 1024


def test_missing_binary_is_internal_error(runner, tmp_path):
    r = runner.run(["/nonexistent/binary"], cwd=tmp_path)
    assert r.status is Status.INTERNAL_ERROR


def test_environment_is_scrubbed(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "hunter2")
    r = _run_py(runner, tmp_path, "import os; print(os.environ.get('SECRET_TOKEN'), os.environ['HOME'])")
    assert r.stdout.decode().split() == ["None", str(tmp_path)]


def test_child_processes_are_killed_with_group(runner, tmp_path):
    code = "import subprocess, time; subprocess.Popen(['sleep', '60']); time.sleep(30)"
    r = _run_py(runner, tmp_path, code, wall_seconds=1.0, cpu_seconds=5)
    assert r.status is Status.TIMEOUT
    import subprocess

    out = subprocess.run(["pgrep", "-f", "^sleep 60$"], capture_output=True, text=True).stdout
    assert out.strip() == ""


@pytest.mark.skipif(not LocalRunner().network_isolated, reason="network isolation not available on this host")
def test_network_is_isolated(runner, tmp_path):
    code = "import socket\ns = socket.socket()\ns.settimeout(3)\ntry:\n    s.connect(('1.1.1.1', 53)); print('connected')\nexcept OSError as e:\n    print('blocked')"
    r = _run_py(runner, tmp_path, code, wall_seconds=10)
    assert r.stdout.strip() == b"blocked"
