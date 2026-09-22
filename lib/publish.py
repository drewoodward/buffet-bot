#!/usr/bin/env python3
"""
Publish one cycle report to the local publish directory.

This is the one-way door between buffet-bot and the OpenClaw dashboard, which
runs as a separate Linux user. buffet-bot writes here; OpenClaw only reads.
OpenClaw has no route back -- no write bit, no credentials, no way to reach
anything else this repo owns.

Everything published crosses a trust boundary, so the report is filtered
through PUBLIC_KEYS rather than written out whole. A field that is not on the
list is dropped and named in the result, because the failure this guards
against is a future field quietly carrying something -- an API response, a
token, a path -- into a directory another account can read.
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG_PATH = os.environ.get("TA_CONFIG", os.path.join(ROOT, "config.json"))

# The report shape in DASHBOARD.md, and nothing else.
PUBLIC_KEYS = {
    "cycle_id", "phase", "generated_at", "execution_mode", "sources",
    "matters", "account", "risk", "positions", "signals", "calls",
    "kevin_record", "flags",
}

REQUIRED_KEYS = ("cycle_id", "phase", "generated_at")

DIR_MODE = 0o2750   # setgid: files land in the reader's group, not millie's
FILE_MODE = 0o640   # owner writes, reader's group reads, nobody else


def publish_dir():
    with open(CONFIG_PATH) as f:
        configured = json.load(f).get("publish_dir", "/srv/buffet-bot")
    return os.environ.get("TA_PUBLISH_DIR", configured)


def safe_name(value):
    """A cycle_id becomes a filename, so strip it to something that cannot
    escape the directory it is written into."""
    cleaned = "".join(c if (c.isalnum() or c in "._-") else "-" for c in str(value))
    cleaned = cleaned.lstrip(".")
    if not cleaned:
        raise ValueError("cycle_id is empty once sanitised")
    return cleaned[:120]


def write_atomic(path, payload):
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        os.chmod(tmp, FILE_MODE)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def copy_charts(report, cycle, charts_dir):
    """Charts live in millie's home, which the dashboard user cannot read, so
    the PNG is copied across and the reference rewritten to a relative path.
    The destination name comes from the cycle id and an index, never from the
    source filename."""
    copied = 0
    for i, signal in enumerate(report.get("signals") or []):
        source = signal.pop("chart_path", None)
        if not source:
            continue
        if not source.lower().endswith(".png") or not os.path.isfile(source):
            signal["chart_error"] = f"not a readable PNG: {source}"
            continue
        name = f"{cycle}-{i}.png"
        destination = os.path.join(charts_dir, name)
        shutil.copyfile(source, destination)
        os.chmod(destination, FILE_MODE)
        signal["chart_url"] = f"charts/{name}"
        copied += 1
    return copied


def publish(report, root=None):
    missing = [k for k in REQUIRED_KEYS if not report.get(k)]
    if missing:
        raise ValueError(f"report is missing required keys: {', '.join(missing)}")

    root = root or publish_dir()
    reports_dir = os.path.join(root, "reports")
    charts_dir = os.path.join(root, "charts")
    for d in (reports_dir, charts_dir):
        os.makedirs(d, mode=DIR_MODE, exist_ok=True)

    dropped = sorted(set(report) - PUBLIC_KEYS)
    public = {k: v for k, v in report.items() if k in PUBLIC_KEYS}

    cycle = safe_name(public["cycle_id"])
    charts = copy_charts(public, cycle, charts_dir)

    report_path = os.path.join(reports_dir, f"{cycle}.json")
    write_atomic(report_path, public)
    write_atomic(os.path.join(root, "latest.json"), public)

    return {"ok": True, "report": report_path, "latest": os.path.join(root, "latest.json"),
            "charts_copied": charts, "dropped_keys": dropped}


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else None
    raw = open(source).read() if source else sys.stdin.read()
    try:
        result = publish(json.loads(raw))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
