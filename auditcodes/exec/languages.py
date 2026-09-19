"""Per-language toolchain specs: how to compile, how to run, and how to apply limits."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from functools import lru_cache

from ..models import Language
from .runner import Limits

MAIN_FILES = {
    Language.C: "main.c",
    Language.CPP: "main.cpp",
    Language.JAVA: "Main.java",
    Language.PYTHON: "main.py",
    Language.JAVASCRIPT: "main.js",
}


@dataclass(frozen=True)
class LanguageSpec:
    language: Language
    display_name: str
    # argv templates; ``{mem_mb}`` is substituted with the memory limit in MiB
    compile_argv: tuple[str, ...] | None
    run_argv: tuple[str, ...]
    # Whether RLIMIT_AS can be applied (false for JVM / V8, which get a heap flag instead)
    address_space_limit: bool
    # Judges give interpreted / VM languages more time than C; multiplier and startup allowance
    time_factor: float
    time_offset: float
    # executables that must exist for this language to be usable
    required_tools: tuple[str, ...]

    def compile_command(self, limits: Limits) -> list[str] | None:
        if self.compile_argv is None:
            return None
        return [a.format(mem_mb=_mib(limits)) for a in self.compile_argv]

    def run_command(self, limits: Limits) -> list[str]:
        return [a.format(mem_mb=_mib(limits)) for a in self.run_argv]

    def run_limits(self, base: Limits) -> Limits:
        scaled = base.scaled(self.time_factor, self.time_offset)
        return Limits(**{**scaled.__dict__, "address_space_limit": self.address_space_limit and base.address_space_limit})

    def compile_limits(self, base: Limits) -> Limits:
        return Limits(**{**base.__dict__, "address_space_limit": self.address_space_limit and base.address_space_limit})

    def available(self) -> bool:
        return _tools_available(self.required_tools)


def _mib(limits: Limits) -> int:
    return max(16, limits.memory_bytes // (1024 * 1024))


@lru_cache(maxsize=None)
def _tools_available(tools: tuple[str, ...]) -> bool:
    return all(shutil.which(t) is not None for t in tools)


DEFAULT_RUN_LIMITS = Limits(cpu_seconds=2.0, wall_seconds=10.0, memory_bytes=256 * 1024 * 1024)
DEFAULT_COMPILE_LIMITS = Limits(
    cpu_seconds=30.0, wall_seconds=60.0, memory_bytes=1024 * 1024 * 1024, output_bytes=4 * 1024 * 1024
)

LANGUAGES: dict[Language, LanguageSpec] = {
    Language.C: LanguageSpec(
        language=Language.C,
        display_name="C",
        compile_argv=("gcc", "-O2", "-std=gnu11", "-Wall", "main.c", "-o", "main", "-lm"),
        run_argv=("./main",),
        address_space_limit=True,
        time_factor=1.0,
        time_offset=0.0,
        required_tools=("gcc",),
    ),
    Language.CPP: LanguageSpec(
        language=Language.CPP,
        display_name="C++",
        compile_argv=("g++", "-O2", "-std=gnu++17", "-Wall", "main.cpp", "-o", "main"),
        run_argv=("./main",),
        address_space_limit=True,
        time_factor=1.0,
        time_offset=0.0,
        required_tools=("g++",),
    ),
    Language.JAVA: LanguageSpec(
        language=Language.JAVA,
        display_name="Java",
        compile_argv=("javac", "-J-Xmx512m", "-encoding", "UTF-8", "Main.java", "Solution.java"),
        run_argv=(
            "java", "-Xmx{mem_mb}m", "-Xss64m", "-XX:+UseSerialGC", "-XX:TieredStopAtLevel=1",
            "-Dfile.encoding=UTF-8", "-cp", ".", "Main",
        ),
        address_space_limit=False,
        time_factor=2.0,
        time_offset=1.0,
        required_tools=("javac", "java"),
    ),
    Language.PYTHON: LanguageSpec(
        language=Language.PYTHON,
        display_name="Python",
        compile_argv=(sys.executable, "-m", "py_compile", "solution.py", "main.py"),
        run_argv=(sys.executable, "main.py"),
        address_space_limit=True,
        time_factor=3.0,
        time_offset=0.5,
        required_tools=(sys.executable,),
    ),
    Language.JAVASCRIPT: LanguageSpec(
        language=Language.JAVASCRIPT,
        display_name="JavaScript",
        compile_argv=("node", "--check", "main.js"),
        run_argv=("node", "--max-old-space-size={mem_mb}", "--stack-size=65500", "main.js"),
        address_space_limit=False,
        time_factor=2.0,
        time_offset=0.5,
        required_tools=("node",),
    ),
}


def get_spec(language: Language) -> LanguageSpec:
    return LANGUAGES[language]


def available_languages() -> list[Language]:
    return [lang for lang, spec in LANGUAGES.items() if spec.available()]
