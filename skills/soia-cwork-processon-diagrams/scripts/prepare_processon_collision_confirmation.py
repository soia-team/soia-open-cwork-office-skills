#!/usr/bin/env python3
"""Prepare an explicit, plan-bound inventory-order collision confirmation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

from processon_archive_batch import (
    ARCHIVE_STATE,
    BatchError,
    load_collision_confirmation,
    load_json,
    run_json,
    sha256,
    utc_now,
    validate_plan,
)


def build_payload(plan_path: Path, progress_path: Path) -> dict[str, Any]:
    plan = load_json(plan_path)
    progress = load_json(progress_path)
    validate_plan(plan, progress)
    run_json(
        [
            os.fspath(Path(sys.executable)),
            str(ARCHIVE_STATE),
            "audit",
            "--plan",
            str(plan_path),
            "--progress",
            str(progress_path),
        ]
    )
    plan_sha256 = sha256(plan_path.expanduser().resolve(strict=True))
    if plan_sha256 != str(progress.get("plan", {}).get("sha256") or ""):
        raise BatchError("current archive plan SHA-256 differs from progress")

    groups: OrderedDict[tuple[str, str], list[dict[str, Any]]] = OrderedDict()
    for entry in plan["entries"]:
        if entry.get("collision_risk") in {None, "", "none_detected"}:
            continue
        key = (str(entry.get("source_directory", "")), str(entry.get("title", "")))
        groups.setdefault(key, []).append(entry)

    done = {
        str(item.get("artifact_id", "")) for item in progress.get("completed", [])
    }
    entries: list[dict[str, Any]] = []
    for entry in plan["entries"]:
        artifact_id = str(entry.get("artifact_id", ""))
        if (
            not artifact_id
            or artifact_id in done
            or entry.get("confirmation_required")
            or entry.get("type") == "unknown"
            or entry.get("collision_risk") in {None, "", "none_detected"}
        ):
            continue
        source_directory = str(entry.get("source_directory", ""))
        title = str(entry.get("title", ""))
        group = groups[(source_directory, title)]
        occurrence = next(
            index
            for index, candidate in enumerate(group)
            if str(candidate.get("artifact_id", "")) == artifact_id
        )
        entries.append(
            {
                "artifact_id": artifact_id,
                "source_directory": source_directory,
                "title": title,
                "occurrence": occurrence,
                "group_size": len(group),
            }
        )
    if not entries:
        raise BatchError("there are no unresolved known collision artifacts to confirm")
    return {
        "schema_version": 1,
        "kind": "processon_collision_confirmation",
        "confirmation_method": "inventory_order",
        "plan_sha256": plan_sha256,
        "entries": entries,
        "created_at": utc_now(),
    }


def write_payload(path: Path, payload: dict[str, Any]) -> Path:
    target = path.expanduser().resolve(strict=False)
    if target.is_symlink():
        raise BatchError(f"collision confirmation target must not be a symlink: {target}")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--confirm-inventory-order",
        action="store_true",
        help="Explicitly authorize selecting duplicate-title rows by the audited plan order.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.confirm_inventory_order:
        parser.error("--confirm-inventory-order is required")
    try:
        payload = build_payload(args.plan, args.progress)
        target = write_payload(args.output, payload)
        # Replay the same strict loader that the browser batch uses.
        plan = load_json(args.plan)
        progress = load_json(args.progress)
        confirmed = load_collision_confirmation(
            target,
            plan_path=args.plan,
            plan=plan,
            progress=progress,
        )
        result = {
            "status": "prepared",
            "path": str(target),
            "plan_sha256": payload["plan_sha256"],
            "confirmed_count": len(confirmed),
            "created_at": payload["created_at"],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except BatchError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
