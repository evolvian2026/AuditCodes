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


def audit_cmd(pdf_or_json: str, out: str | None, no_llm: bool) -> int:
    from pydantic import TypeAdapter

    from .audit import run_audit
    from .llm.client import default_llm
    from .models import Question

    path = Path(pdf_or_json)
    if path.suffix.lower() == ".json":
        questions = TypeAdapter(list[Question]).validate_json(path.read_bytes())
    else:
        from .ingest import extract

        questions = extract(path).questions
    llm = None if no_llm else default_llm()
    if llm is None and not no_llm:
        print("no ANTHROPIC_API_KEY: running execution-based checks only", file=sys.stderr)
    reports = []
    for q in questions:
        print(f"== {q.id} {q.title}", file=sys.stderr)
        rep = run_audit(q, llm=llm, progress=lambda name: print(f"   {name}...", file=sys.stderr))
        reports.append(rep)
        for f in rep.sorted_findings:
            print(f"   [{f.severity.value:7}] {f.rule_id} {f.component}: {f.title}" + ("  (patch)" if f.patch else ""), file=sys.stderr)
        if rep.error:
            print("   error: " + rep.error.strip().splitlines()[-1], file=sys.stderr)
    data = TypeAdapter(list[type(reports[0])]).dump_json(reports, indent=2) if reports else b"[]"
    if out:
        Path(out).write_bytes(data)
    else:
        sys.stdout.write(data.decode())
    return 0


def check_llm() -> int:
    from .llm.client import default_llm
    from pydantic import BaseModel

    llm = default_llm()
    if llm is None:
        print("no ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN in the environment")
        return 1

    class Pong(BaseModel):
        word: str

    out = llm.structured(task="ping", system="Answer with a JSON object.", user="Set word to 'ready'.", schema=Pong, max_tokens=200, effort="low")
    print(f"{llm.model}: {out.word}")
    return 0


def export_cmd(source: str, fmt: str, out: str, no_hidden: bool, no_audit: bool, title: str | None) -> int:
    from pydantic import TypeAdapter

    from .audit.report import AuditReport
    from .export import ExportOptions, export_docx, export_html, export_pdf, export_zip
    from .models import Question

    src = Path(source)
    reports: dict[str, AuditReport] = {}
    assets = None
    if src.is_dir():  # a job directory
        questions = TypeAdapter(list[Question]).validate_json((src / "questions.json").read_bytes())
        for f in (src / "audit").glob("*.json") if (src / "audit").is_dir() else []:
            r = AuditReport.model_validate_json(f.read_bytes())
            reports[r.question_id] = r
        assets = src / "assets"
    elif src.suffix.lower() == ".json":
        questions = TypeAdapter(list[Question]).validate_json(src.read_bytes())
    else:
        from .ingest import extract

        assets = Path(out).parent / "assets"
        questions = extract(src, assets).questions
    options = ExportOptions(title=title or "Audited Coding Questions", source_name=src.name, include_hidden=not no_hidden, include_audit=not no_audit)
    if fmt == "pdf":
        data = export_pdf(questions, reports, options, assets)
    elif fmt == "docx":
        data = export_docx(questions, reports, options, assets)
    elif fmt == "html":
        data = export_html(questions, reports, options, assets).encode()
    else:
        data = export_zip(questions, reports, options, assets)
    Path(out).write_bytes(data)
    print(f"{len(questions)} question(s) -> {out}", file=sys.stderr)
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
    au = sub.add_parser("audit", help="audit questions from a PDF or an exported questions.json; prints reports as JSON")
    au.add_argument("source")
    au.add_argument("-o", "--out")
    au.add_argument("--no-llm", action="store_true", help="execution-based checks only")
    sub.add_parser("check-llm", help="confirm the Claude API is reachable with the configured credentials")
    ex2 = sub.add_parser("export", help="export a job directory, questions.json or PDF as pdf/docx/html/zip")
    ex2.add_argument("source")
    ex2.add_argument("-f", "--format", choices=["pdf", "docx", "html", "zip"], default="pdf")
    ex2.add_argument("-o", "--out", required=True)
    ex2.add_argument("--no-hidden", action="store_true")
    ex2.add_argument("--no-audit", action="store_true")
    ex2.add_argument("--title")
    sv = sub.add_parser("serve", help="run the review web app")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--data-dir", help="job storage directory (default: data/jobs or $AUDITCODES_DATA)")
    args = parser.parse_args(argv)
    if args.command == "check-toolchains":
        return check_toolchains()
    if args.command == "extract":
        return extract_cmd(args.pdf, args.out, args.assets)
    if args.command == "audit":
        return audit_cmd(args.source, args.out, args.no_llm)
    if args.command == "check-llm":
        return check_llm()
    if args.command == "export":
        return export_cmd(args.source, args.format, args.out, args.no_hidden, args.no_audit, args.title)
    if args.command == "serve":
        return serve(args.host, args.port, args.data_dir)
    return 2


if __name__ == "__main__":
    sys.exit(main())
