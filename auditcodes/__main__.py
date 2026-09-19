"""``python -m auditcodes`` — operational commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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


def extract_cmd(pdf: str, out: str | None, assets: str | None) -> int:
    from .ingest import extract
    from .models import Question
    from pydantic import TypeAdapter

    result = extract(pdf, Path(assets) if assets else None)
    for w in result.warnings:
        print(f"document: {w}", file=sys.stderr)
    for q in result.questions:
        for w in q.provenance.warnings:
            print(f"{q.id}: {w}", file=sys.stderr)
    data = TypeAdapter(list[Question]).dump_json(result.questions, indent=2)
    if out:
        Path(out).write_bytes(data)
        print(f"{len(result.questions)} question(s) -> {out}", file=sys.stderr)
    else:
        sys.stdout.write(data.decode())
    return 0


def serve(host: str, port: int, data_dir: str | None) -> int:
    import uvicorn

    from .app import create_app

    uvicorn.run(create_app(data_dir), host=host, port=port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="auditcodes")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-toolchains", help="report which language toolchains are usable on this host")
    ex = sub.add_parser("extract", help="extract questions from a PDF to JSON")
    ex.add_argument("pdf")
    ex.add_argument("-o", "--out", help="write JSON here instead of stdout")
    ex.add_argument("--assets", help="directory for extracted images")
    sv = sub.add_parser("serve", help="run the review web app")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--data-dir", help="job storage directory (default: data/jobs or $AUDITCODES_DATA)")
    args = parser.parse_args(argv)
    if args.command == "check-toolchains":
        return check_toolchains()
    if args.command == "extract":
        return extract_cmd(args.pdf, args.out, args.assets)
    if args.command == "serve":
        return serve(args.host, args.port, args.data_dir)
    return 2


if __name__ == "__main__":
    sys.exit(main())
