#!/usr/bin/env python3
"""Supervise bounded, resumable ProcessOn archive batches.

This wrapper is intentionally host-independent.  It starts the existing
skill-owned batch runner only, never attaches to a user's normal Chrome, and
persists a small state file after every batch.  A non-zero batch exit is not
automatically treated as safe to skip: the only automatic state transition is
the exact, per-artifact XMind menu absence already recorded in that batch's
JSON receipt.  Any other unresolved pending item stops the supervisor.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
BATCH_SCRIPT = SCRIPT_DIR / "processon_archive_batch.py"
STATE_SCRIPT = SCRIPT_DIR / "processon_archive_state.py"
MAX_SUPERVISOR_BATCHES = 200
MAX_SUPERVISOR_LIMIT = 12
MAX_SUPERVISOR_WORKERS = 3
ARTIFACT_ID_RE = re.compile(r"^[0-9a-f]{64}$")
XMind_MENU_ABSENCE = "BatchError: no visible ProcessOn download menu matched: Xmind文件"


class SupervisorError(RuntimeError):
    """Raised when safe continuation cannot be proven."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_object(raw: str, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SupervisorError(f"{label} returned non-JSON output") from exc
    if not isinstance(payload, dict):
        raise SupervisorError(f"{label} JSON result must be an object")
    return payload


def run_json(command: list[str], *, label: str, allow_nonzero: bool = False) -> tuple[dict[str, Any], int]:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode and not allow_nonzero:
        detail = completed.stdout.strip() or completed.stderr.strip()
        raise SupervisorError(f"{label} failed ({completed.returncode}): {detail[:1000]}")
    return json_object(completed.stdout, label=label), completed.returncode


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path = path.expanduser()
    if path.is_symlink():
        raise SupervisorError(f"supervisor state file must not be a symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def known_xmind_pending_ids(payload: dict[str, Any]) -> list[str]:
    """Return only explicitly identified, safe-to-classify XMind failures."""

    pending = payload.get("pending", [])
    if not isinstance(pending, list):
        raise SupervisorError("batch pending field must be a list")
    result: list[str] = []
    for item in pending:
        if not isinstance(item, dict):
            raise SupervisorError("batch pending entry must be an object")
        artifact_id = item.get("artifact_id")
        if item.get("error") != XMind_MENU_ABSENCE:
            continue
        if not isinstance(artifact_id, str) or not ARTIFACT_ID_RE.fullmatch(artifact_id):
            raise SupervisorError("XMind menu absence is missing a valid artifact_id")
        result.append(artifact_id)
    if len(result) != len(set(result)):
        raise SupervisorError("batch repeats an XMind menu-absence artifact_id")
    return result


def make_batch_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        str(BATCH_SCRIPT),
        "--plan",
        str(args.plan),
        "--progress",
        str(args.progress),
        "--team-url",
        args.team_url,
        "--profile-dir",
        str(args.profile_dir),
        "--workers",
        str(args.workers),
        "--limit",
        str(args.limit),
        "--timeout-ms",
        str(args.timeout_ms),
    ]
    if args.config:
        command.extend(["--config", str(args.config)])
    if args.concurrency_proof:
        command.extend(["--concurrency-proof", str(args.concurrency_proof)])
    return command


def audit_command(args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        str(STATE_SCRIPT),
        "audit",
        "--plan",
        str(args.plan),
        "--progress",
        str(args.progress),
    ]


def mark_known_xmind_failure(args: argparse.Namespace, artifact_id: str, receipt_file: str) -> dict[str, Any]:
    receipt = Path(receipt_file)
    if receipt.is_symlink() or not receipt.is_file():
        raise SupervisorError("batch XMind diagnostic has no safe immutable receipt file")
    command = [
        sys.executable,
        str(STATE_SCRIPT),
        "mark",
        "--plan",
        str(args.plan),
        "--progress",
        str(args.progress),
        "--artifact-id",
        artifact_id,
        "--outcome",
        "failed",
        "--reason",
        "mindmap_xmind_menu_unavailable_after_direct_attribute_export_control",
        "--evidence-file",
        str(receipt),
    ]
    payload, _ = run_json(command, label="archive state mark")
    if payload.get("status") != "failed":
        raise SupervisorError("archive state did not confirm XMind failed outcome")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--team-url", required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--profile-dir", type=Path, required=True)
    parser.add_argument("--concurrency-proof", type=Path)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--timeout-ms", type=int, default=15_000)
    parser.add_argument("--max-batches", type=int, required=True)
    parser.add_argument(
        "--state-file",
        type=Path,
        help="Private supervisor checkpoint; defaults beside download-progress.json.",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if not BATCH_SCRIPT.is_file() or not STATE_SCRIPT.is_file():
        raise SupervisorError("required ProcessOn archive scripts are missing")
    if not 1 <= args.workers <= MAX_SUPERVISOR_WORKERS:
        raise SupervisorError(f"--workers must be within 1..{MAX_SUPERVISOR_WORKERS}")
    if args.workers > 1 and not args.concurrency_proof:
        raise SupervisorError("--workers > 1 requires --concurrency-proof")
    if not 1 <= args.limit <= MAX_SUPERVISOR_LIMIT:
        raise SupervisorError(f"--limit must be within 1..{MAX_SUPERVISOR_LIMIT}")
    if not 250 <= args.timeout_ms <= 300_000:
        raise SupervisorError("--timeout-ms must be within 250..300000")
    if not 1 <= args.max_batches <= MAX_SUPERVISOR_BATCHES:
        raise SupervisorError(f"--max-batches must be within 1..{MAX_SUPERVISOR_BATCHES}")
    if not args.plan.is_file() or args.plan.is_symlink():
        raise SupervisorError("--plan must be an existing regular file")
    if not args.progress.is_file() or args.progress.is_symlink():
        raise SupervisorError("--progress must be an existing regular file")
    if args.config and (not args.config.is_file() or args.config.is_symlink()):
        raise SupervisorError("--config must be an existing regular file")
    if not args.profile_dir.is_dir() or args.profile_dir.is_symlink():
        raise SupervisorError("--profile-dir must be an existing non-symlink directory")
    if args.concurrency_proof and (
        not args.concurrency_proof.is_file() or args.concurrency_proof.is_symlink()
    ):
        raise SupervisorError("--concurrency-proof must be an existing regular file")


def supervise(args: argparse.Namespace) -> dict[str, Any]:
    state_file = args.state_file or args.progress.parent / "archive-supervisor-state.json"
    state_file = state_file.expanduser()
    history: list[dict[str, Any]] = []
    final_status = "batch_limit_reached"
    final_audit: dict[str, Any] | None = None
    for index in range(1, args.max_batches + 1):
        batch, exit_code = run_json(
            make_batch_command(args), label="archive batch", allow_nonzero=True
        )
        record: dict[str, Any] = {
            "index": index,
            "at": utc_now(),
            "batch_status": batch.get("status"),
            "batch_exit_code": exit_code,
            "receipt_file": batch.get("receipt_file"),
            "selected": batch.get("selected"),
            "completed_count": batch.get("completed_count"),
            "blocked_count": batch.get("blocked_count"),
            "pending_count": batch.get("pending_count"),
        }
        xmind_ids = known_xmind_pending_ids(batch)
        if xmind_ids:
            receipt_file = batch.get("receipt_file")
            if not isinstance(receipt_file, str) or not receipt_file:
                raise SupervisorError("XMind menu absence has no receipt path")
            record["auto_failed_xmind_artifact_ids"] = xmind_ids
            for artifact_id in xmind_ids:
                mark_known_xmind_failure(args, artifact_id, receipt_file)

        audit, _ = run_json(
            audit_command(args), label="archive state audit", allow_nonzero=True
        )
        record["audit_status"] = audit.get("status")
        record["counts"] = audit.get("counts")
        history.append(record)
        payload = {
            "schema_version": 1,
            "status": "running",
            "updated_at": utc_now(),
            "plan": str(args.plan.resolve()),
            "progress": str(args.progress.resolve()),
            "history": history,
            "last_audit": audit,
        }
        atomic_write_json(state_file, payload)
        final_audit = audit
        if audit.get("status") != "passed":
            final_status = "stopped_audit_failed"
            break
        if batch.get("status") == "nothing_to_do":
            final_status = "nothing_to_do"
            break
        if batch.get("status") == "collision_confirmation_required":
            final_status = "stopped_collision_confirmation_required"
            break
        pending = batch.get("pending", [])
        if not isinstance(pending, list):
            raise SupervisorError("batch pending field must be a list")
        unresolved = [
            item
            for item in pending
            if not isinstance(item, dict) or item.get("artifact_id") not in xmind_ids
        ]
        if batch.get("status") == "failed" or unresolved:
            final_status = "stopped_unclassified_batch_failure"
            break
    final_payload = {
        "schema_version": 1,
        "status": final_status,
        "updated_at": utc_now(),
        "plan": str(args.plan.resolve()),
        "progress": str(args.progress.resolve()),
        "history": history,
        "last_audit": final_audit,
    }
    atomic_write_json(state_file, final_payload)
    return final_payload


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        validate_args(args)
        payload = supervise(args)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["status"] in {"nothing_to_do", "batch_limit_reached"} else 1
    except (SupervisorError, OSError, ValueError) as exc:
        payload = {
            "schema_version": 1,
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "updated_at": utc_now(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
