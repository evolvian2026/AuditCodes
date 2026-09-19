"""``python -m auditcodes`` — small operational commands."""

from __future__ import annotations

import argparse
import sys

from .exec.languages import LANGUAGES
from .exec.runner import LocalRunner


def check_toolchains() -> int:
    runner = LocalRunner()
    print(f"network isolation: {'yes (unshare)' if runner.network_isolated else 'NO - solutions can reach the network'}")
    missing = 0
    for lang, spec in LANGUAGES.items():
        ok = spec.available()
        missing += not ok
        print(f"{spec.display_name:<11} {'ok' if ok else 'MISSING'}   needs: {', '.join(spec.required_tools)}")
    return 1 if missing else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="auditcodes")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-toolchains", help="report which language toolchains are usable on this host")
    args = parser.parse_args(argv)
    if args.command == "check-toolchains":
        return check_toolchains()
    return 2


if __name__ == "__main__":
    sys.exit(main())
