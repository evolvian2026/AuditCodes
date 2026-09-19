"""Per-language toolchain specs: how to compile, how to run, and how to apply limits."""

from __future__ import annotations

import re
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
    # argv templates: ``{mem_mb}`` is the memory limit in MiB, ``{sources}`` expands to the source
    # file list, ``{main_class}`` is the Java class to launch
    compile_argv: tuple[str, ...] | None
    run_argv: tuple[str, ...]
    program_filename: str  # file name for a complete stdio program (Java: derived from its public class)
    # Whether RLIMIT_AS can be applied (false for JVM / V8, which get a heap flag instead)
    address_space_limit: bool
    # Judges give interpreted / VM languages more time than C; multiplier and startup allowance
    time_factor: float
    time_offset: float
    # executables that must exist for this language to be usable
    required_tools: tuple[str, ...]

    def compile_command(self, limits: Limits, sources: list[str]) -> list[str] | None:
        if self.compile_argv is None:
            return None
        out: list[str] = []
        for a in self.compile_argv:
            if a == "{sources}":
                out.extend(sources)
            else:
                out.append(a.format(mem_mb=_mib(limits)))
        return out

    def run_command(self, limits: Limits, main_class: str = "Main") -> list[str]:
        return [a.format(mem_mb=_mib(limits), main_class=main_class) for a in self.run_argv]

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
        compile_argv=("gcc", "-O2", "-std=gnu11", "-Wall", "{sources}", "-o", "main", "-lm"),
        run_argv=("./main",),
        program_filename="main.c",
        address_space_limit=True,
        time_factor=1.0,
        time_offset=0.0,
        required_tools=("gcc",),
    ),
    Language.CPP: LanguageSpec(
        language=Language.CPP,
        display_name="C++",
        compile_argv=("g++", "-O2", "-std=gnu++17", "-Wall", "{sources}", "-o", "main"),
        run_argv=("./main",),
        program_filename="main.cpp",
        address_space_limit=True,
        time_factor=1.0,
        time_offset=0.0,
        required_tools=("g++",),
    ),
    Language.JAVA: LanguageSpec(
        language=Language.JAVA,
        display_name="Java",
        compile_argv=("javac", "-J-Xmx512m", "-encoding", "UTF-8", "{sources}"),
        run_argv=(
            "java", "-Xmx{mem_mb}m", "-Xss64m", "-XX:+UseSerialGC", "-XX:TieredStopAtLevel=1",
            "-Dfile.encoding=UTF-8", "-cp", ".", "{main_class}",
        ),
        program_filename="Main.java",
        address_space_limit=False,
        time_factor=2.0,
        time_offset=1.0,
        required_tools=("javac", "java"),
    ),
    Language.PYTHON: LanguageSpec(
        language=Language.PYTHON,
        display_name="Python",
        compile_argv=(sys.executable, "-m", "py_compile", "{sources}"),
        run_argv=(sys.executable, "main.py"),
        program_filename="main.py",
        address_space_limit=True,
        time_factor=3.0,
        time_offset=0.5,
        required_tools=(sys.executable,),
    ),
    Language.JAVASCRIPT: LanguageSpec(
        language=Language.JAVASCRIPT,
        display_name="JavaScript",
        compile_argv=("node", "--check", "{sources}"),
        run_argv=("node", "--max-old-space-size={mem_mb}", "--stack-size=65500", "main.js"),
        program_filename="main.js",
        address_space_limit=False,
        time_factor=2.0,
        time_offset=0.5,
        required_tools=("node",),
    ),
}


def get_spec(language: Language) -> LanguageSpec:
    return LANGUAGES[language]


_JAVA_PUBLIC_CLASS = re.compile(r"\bpublic\s+(?:final\s+)?class\s+([A-Za-z_$][\w$]*)")
_JAVA_ANY_CLASS = re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)")


def java_main_class(code: str) -> str:
    """Name of the class to launch for a complete Java program (its file must carry this name)."""
    m = _JAVA_PUBLIC_CLASS.search(code) or _JAVA_ANY_CLASS.search(code)
    return m.group(1) if m else "Main"


def program_filename(language: Language, code: str) -> str:
    if language is Language.JAVA:
        return f"{java_main_class(code)}.java"
    return LANGUAGES[language].program_filename


def available_languages() -> list[Language]:
    return [lang for lang, spec in LANGUAGES.items() if spec.available()]
