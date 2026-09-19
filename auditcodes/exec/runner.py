"""Process runner with resource limits.

``Runner`` is the seam the rest of the pipeline depends on. ``LocalRunner`` runs the command as a
direct child with POSIX rlimits, a scrubbed environment, a private working directory, a wall-clock
kill switch on the whole process group, and network isolation via ``unshare -n`` when the host
allows it. That is *containment* for trusted question banks, not isolation: code still runs as the
current user. A Docker-backed ``Runner`` can replace it without touching anything else.
"""

from __future__ import annotations

import math
import os
import re
import resource
import shutil
import signal
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence


class Status(str, Enum):
    OK = "ok"
    TIMEOUT = "timeout"
    MEMORY_LIMIT = "memory_limit"
    OUTPUT_LIMIT = "output_limit"
    RUNTIME_ERROR = "runtime_error"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class Limits:
    cpu_seconds: float = 2.0
    wall_seconds: float = 10.0
    memory_bytes: int = 256 * 1024 * 1024
    output_bytes: int = 16 * 1024 * 1024
    file_size_bytes: int = 32 * 1024 * 1024
    # RLIMIT_NPROC is per *user*, not per process, so it is off by default: a low value would make
    # every spawn fail on a busy host. Set it for fork-bomb protection on a dedicated worker.
    processes: int | None = None
    # RLIMIT_AS breaks the JVM and V8 (they reserve huge virtual ranges); those languages pass a heap
    # flag instead and switch this off.
    address_space_limit: bool = True

    def scaled(self, time_factor: float = 1.0, time_offset: float = 0.0) -> "Limits":
        return replace(
            self,
            cpu_seconds=self.cpu_seconds * time_factor + time_offset,
            wall_seconds=self.wall_seconds * time_factor + time_offset,
        )


@dataclass
class RunResult:
    status: Status
    exit_code: int | None
    signal: int | None
    stdout: bytes
    stderr: bytes
    wall_seconds: float
    cpu_seconds: float
    max_rss_bytes: int
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status is Status.OK

    def stderr_text(self, limit: int = 4000) -> str:
        return self.stderr[:limit].decode("utf-8", errors="replace")

    def stdout_text(self, limit: int = 4000) -> str:
        return self.stdout[:limit].decode("utf-8", errors="replace")


class Runner(ABC):
    @abstractmethod
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        stdin: bytes = b"",
        limits: Limits = Limits(),
        env: Mapping[str, str] | None = None,
    ) -> RunResult: ...

    @property
    @abstractmethod
    def network_isolated(self) -> bool: ...


_MEMORY_PATTERNS = re.compile(
    rb"MemoryError|OutOfMemoryError|std::bad_alloc|heap out of memory|Cannot allocate memory"
    rb"|out of memory|Killed",
    re.IGNORECASE,
)

_BASE_ENV = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    "NODE_OPTIONS": "",
}


class _Drain(threading.Thread):
    """Reads a pipe to exhaustion, keeping at most ``limit`` bytes; calls ``on_overflow`` once."""

    def __init__(self, stream, limit: int, on_overflow) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._limit = limit
        self._on_overflow = on_overflow
        self.data = bytearray()
        self.overflow = False

    def run(self) -> None:
        try:
            while True:
                chunk = self._stream.read1(65536)
                if not chunk:
                    break
                if self.overflow:
                    continue
                room = self._limit - len(self.data)
                if len(chunk) > room:
                    self.data += chunk[:room]
                    self.overflow = True
                    self._on_overflow()
                else:
                    self.data += chunk
        finally:
            self._stream.close()


class LocalRunner(Runner):
    def __init__(self, isolate_network: bool = True) -> None:
        self._prefix: list[str] = _detect_network_isolation() if isolate_network else []

    @property
    def network_isolated(self) -> bool:
        return bool(self._prefix)

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        stdin: bytes = b"",
        limits: Limits = Limits(),
        env: Mapping[str, str] | None = None,
    ) -> RunResult:
        full_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(cwd), "TMPDIR": str(cwd), **_BASE_ENV}
        if env:
            full_env.update(env)

        def preexec() -> None:
            cpu = max(1, math.ceil(limits.cpu_seconds))
            _setrlimit(resource.RLIMIT_CPU, cpu, cpu + 1)
            if limits.address_space_limit and limits.memory_bytes:
                _setrlimit(resource.RLIMIT_AS, limits.memory_bytes, limits.memory_bytes)
            _setrlimit(resource.RLIMIT_FSIZE, limits.file_size_bytes, limits.file_size_bytes)
            _setrlimit(resource.RLIMIT_CORE, 0, 0)
            if limits.processes:
                _setrlimit(resource.RLIMIT_NPROC, limits.processes, limits.processes)

        exe = _resolve_executable(argv[0], cwd, full_env["PATH"])
        if exe is None:
            return RunResult(Status.INTERNAL_ERROR, None, None, b"", b"", 0.0, 0.0, 0, f"executable not found: {argv[0]!r}")

        start = time.monotonic()
        try:
            proc = subprocess.Popen(
                [*self._prefix, *argv],
                cwd=str(cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=full_env,
                preexec_fn=preexec,
                start_new_session=True,
            )
        except OSError as e:
            return RunResult(Status.INTERNAL_ERROR, None, None, b"", b"", 0.0, 0.0, 0, f"could not start {argv[0]!r}: {e}")

        timed_out = threading.Event()

        def kill_group() -> None:
            _killpg(proc.pid)

        def on_wall_timeout() -> None:
            timed_out.set()
            kill_group()

        out = _Drain(proc.stdout, limits.output_bytes, kill_group)
        err = _Drain(proc.stderr, limits.output_bytes, kill_group)
        out.start()
        err.start()
        feeder = threading.Thread(target=_feed_stdin, args=(proc.stdin, stdin), daemon=True)
        feeder.start()
        timer = threading.Timer(limits.wall_seconds, on_wall_timeout)
        timer.start()
        try:
            _, wait_status, rusage = os.wait4(proc.pid, 0)
        finally:
            timer.cancel()
            kill_group()  # reap anything the command left behind in its group
        wall = time.monotonic() - start
        out.join()
        err.join()
        feeder.join()

        exit_code: int | None = None
        sig: int | None = None
        if os.WIFSIGNALED(wait_status):
            sig = os.WTERMSIG(wait_status)
            proc.returncode = -sig
        else:
            exit_code = os.WEXITSTATUS(wait_status)
            proc.returncode = exit_code

        cpu = rusage.ru_utime + rusage.ru_stime
        max_rss = rusage.ru_maxrss * 1024
        stderr = bytes(err.data)
        status, reason = _classify(timed_out.is_set(), out.overflow or err.overflow, exit_code, sig, cpu, limits, stderr)
        return RunResult(status, exit_code, sig, bytes(out.data), stderr, wall, cpu, max_rss, reason)


def _classify(timed_out: bool, overflow: bool, exit_code, sig, cpu: float, limits: Limits, stderr: bytes) -> tuple[Status, str]:
    if timed_out:
        return Status.TIMEOUT, f"wall-clock limit of {limits.wall_seconds:g}s exceeded"
    if overflow:
        return Status.OUTPUT_LIMIT, f"output exceeded {limits.output_bytes} bytes"
    if sig == signal.SIGXCPU or cpu > limits.cpu_seconds:
        return Status.TIMEOUT, f"CPU limit of {limits.cpu_seconds:g}s exceeded (used {cpu:.2f}s)"
    if sig is not None:
        if _MEMORY_PATTERNS.search(stderr):
            return Status.MEMORY_LIMIT, f"killed by signal {sig} ({_signame(sig)}) after memory exhaustion"
        return Status.RUNTIME_ERROR, f"killed by signal {sig} ({_signame(sig)})"
    if exit_code:
        if _MEMORY_PATTERNS.search(stderr):
            return Status.MEMORY_LIMIT, f"exit code {exit_code} after memory exhaustion"
        return Status.RUNTIME_ERROR, f"exit code {exit_code}"
    return Status.OK, ""


def _signame(sig: int) -> str:
    try:
        return signal.Signals(sig).name
    except ValueError:
        return "unknown"


def _setrlimit(which: int, soft: int, hard: int) -> None:
    _, cur_hard = resource.getrlimit(which)
    if cur_hard != resource.RLIM_INFINITY:
        soft = min(soft, cur_hard)
        hard = min(hard, cur_hard)
    try:
        resource.setrlimit(which, (soft, hard))
    except (ValueError, OSError):
        pass


def _resolve_executable(name: str, cwd: Path, path: str) -> str | None:
    """Locate the program the way exec would, so a missing binary is reported as our error, not the code's."""
    if "/" in name:
        candidate = Path(name) if name.startswith("/") else cwd / name
        return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None
    return shutil.which(name, path=path)


def _killpg(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        pass


def _feed_stdin(pipe, data: bytes) -> None:
    try:
        pipe.write(data)
        pipe.close()
    except (BrokenPipeError, OSError):
        pass


def _detect_network_isolation() -> list[str]:
    if shutil.which("unshare") is None:
        return []
    for prefix in (["unshare", "-n"], ["unshare", "-Un"]):
        try:
            r = subprocess.run([*prefix, "true"], capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            return prefix
    return []
