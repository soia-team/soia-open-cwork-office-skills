#!/usr/bin/env python3
"""Download and archive a bounded ProcessOn batch with fixed headless workers.

The script uses one skill-owned persistent browser context and 1-3 fixed pages.
It never attaches to a user's normal Chrome. Every source popup closes in
``finally``; every worker page and the whole context close on every exit path.
Downloads may run concurrently, while finalization, metadata, source-link and
archive-progress writes are serialized by one writer in the parent process.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import zipfile
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote_plus, urlparse
from xml.etree import ElementTree

from processon_browser_runner import (
    BrowserRunnerError,
    default_profile_dir,
    ensure_dedicated_profile,
    target_reached,
    validate_processon_url,
    validate_profile_dir,
)
from finalize_processon_download import DownloadError, ensure_paths, load_settings
from inspect_processon_export import inspect_pos


SCRIPT_DIR = Path(__file__).resolve().parent
ARCHIVE_STATE = SCRIPT_DIR / "processon_archive_state.py"
FINALIZER = SCRIPT_DIR / "finalize_processon_download.py"
MAX_WORKERS = 3
MAX_BATCH = 60
READY_ATTEMPTS = 2
# Fixed provider row markers accepted for flowcharts.  ``222`` is the newer
# red UI/flowchart marker observed in ProcessOn's current team list; ``444``
# remains the legacy blue flowchart marker.
FLOWCHART_ICON_SELECTORS = (".icon-a-444_huaban1", ".icon-a-222_huaban1")
MAX_ZIP_ENTRIES = 10_000
MAX_ZIP_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ZIP_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
COMMON_TITLE_WORDS = (
    "生产环境",
    "测试环境",
    "新测试环境",
    "部署架构图",
    "部署图",
    "架构图",
    "流程图",
    "示意图",
    "系统",
    "未上生产",
)
SENSITIVE_TEXT_PATTERNS = (
    ("chinese_password_assignment", re.compile(r"密码\s*[:：=]\s*[^\s,，;；]+")),
    (
        "english_password_assignment",
        re.compile(r"\b(?:password|passwd|pwd)\s*[:=]\s*[^\s,;]+", re.IGNORECASE),
    ),
    (
        "aws_presigned_url_parameter",
        re.compile(r"[?&]X-Amz-(?:Credential|Signature)=", re.IGNORECASE),
    ),
)
SENSITIVE_REDACTION_PATTERNS = (
    (
        "chinese_password_assignment",
        re.compile(r"(?P<key>密码)\s*[:：=]\s*[^\s,，;；]+"),
    ),
    (
        "english_password_assignment",
        re.compile(
            r"(?P<key>\b(?:password|passwd|pwd))\s*[:=]\s*[^\s,;]+",
            re.IGNORECASE,
        ),
    ),
    (
        "aws_presigned_url_parameter",
        re.compile(
            r"(?P<separator>[?&])(?P<key>X-Amz-(?:Credential|Signature))=[^&\s]+",
            re.IGNORECASE,
        ),
    ),
)
VSDX_DOWNLOAD_MENU_CANDIDATES = (
    "导出全部画布 （.vsdx）",
    "导出全部画布 (.vsdx)",
    "VISIO文件",
    "VISIO文件 beta",
)
POS_DOWNLOAD_MENU_CANDIDATES = ("POS文件",)
EDITOR_FILE_MENU = "文件"
EDITOR_EXPORT_MENU = "导出为"
TEAM_SEARCH_INPUT_SELECTORS = (
    "input[placeholder*='搜索文件']",
    "input[placeholder*='文件/文件夹']",
)
SEMANTIC_CONTROL_SELECTORS = {
    "文件": (
        "[aria-label='文件']",
        "[title='文件']",
        "[data-title='文件']",
        "[data-tooltip='文件']",
    ),
    "导出为": (
        "[aria-label='导出为']",
        "[title='导出为']",
        "[data-title='导出为']",
        "[data-tooltip='导出为']",
    ),
    "导出全部画布 （.vsdx）": (
        "[aria-label='导出全部画布 （.vsdx）']",
        "[title='导出全部画布 （.vsdx）']",
        "[data-title='导出全部画布 （.vsdx）']",
        "[data-tooltip='导出全部画布 （.vsdx）']",
    ),
    "导出全部画布 (.vsdx)": (
        "[aria-label='导出全部画布 (.vsdx)']",
        "[title='导出全部画布 (.vsdx)']",
        "[data-title='导出全部画布 (.vsdx)']",
        "[data-tooltip='导出全部画布 (.vsdx)']",
    ),
    "VISIO文件": (
        "[aria-label='VISIO文件']",
        "[title='VISIO文件']",
        "[data-title='VISIO文件']",
        "[data-tooltip='VISIO文件']",
    ),
    "VISIO文件 beta": (
        "[aria-label='VISIO文件 beta']",
        "[title='VISIO文件 beta']",
        "[data-title='VISIO文件 beta']",
        "[data-tooltip='VISIO文件 beta']",
    ),
    "Xmind文件": (
        "[aria-label='Xmind文件']",
        "[title='Xmind文件']",
        "[data-title='Xmind文件']",
        "[data-tooltip='Xmind文件']",
        # The current mindmap editor renders this provider label as
        # ``XMind文件`` (capital M), while list/export views historically
        # used ``Xmind文件``.  Keep the variant in the fixed provider
        # allowlist; never accept a caller-supplied selector.
        "[aria-label='XMind文件']",
        "[title='XMind文件']",
        "[data-title='XMind文件']",
        "[data-tooltip='XMind文件']",
    ),
    "POS文件": (
        "[aria-label='POS文件']",
        "[title='POS文件']",
        "[data-title='POS文件']",
        "[data-tooltip='POS文件']",
    ),
}

SEMANTIC_TEXT_VARIANTS = {
    # Closed provider-label compatibility, intentionally not a
    # case-insensitive match: diagram titles must not become controls.
    "Xmind文件": ("XMind文件",),
}


class BatchError(RuntimeError):
    """Fail-closed batch error."""


@dataclass
class BrowserReceipt:
    pages_seen_at_start: int = 0
    stale_pages_closed: int = 0
    worker_pages_opened: int = 0
    worker_pages_closed: int = 0
    scoped_pages_opened: int = 0
    scoped_pages_closed: int = 0
    pages_closed_at_exit: int = 0
    downloaded_files: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pages_seen_at_start": self.pages_seen_at_start,
            "stale_pages_closed": self.stale_pages_closed,
            "worker_pages_opened": self.worker_pages_opened,
            "worker_pages_closed": self.worker_pages_closed,
            "scoped_pages_opened": self.scoped_pages_opened,
            "scoped_pages_closed": self.scoped_pages_closed,
            "pages_closed_at_exit": self.pages_closed_at_exit,
            "downloaded_files": self.downloaded_files,
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise BatchError(f"JSON root must be an object: {path}")
    return value


def run_json(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = completed.stdout.strip() or completed.stderr.strip()
        raise BatchError(f"command failed ({completed.returncode}): {detail[:2000]}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BatchError(f"command returned non-JSON output: {completed.stdout[:1000]}") from exc
    if not isinstance(payload, dict):
        raise BatchError("command JSON result must be an object")
    return payload


def progress_done_ids(progress: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for key in ("completed", "failed", "blocked"):
        values = progress.get(key, [])
        if not isinstance(values, list):
            raise BatchError(f"progress.{key} must be a list")
        for item in values:
            if isinstance(item, dict) and item.get("artifact_id"):
                result.add(str(item["artifact_id"]))
    return result


def reconciliation_skip_ids(
    progress: dict[str, Any], *, explicitly_retried_ids: set[str]
) -> set[str]:
    """Keep terminal state immutable unless this run explicitly retries it.

    A half-commit may leave a valid archive file and metadata before the state
    record is updated.  Completed artifacts must always remain immutable.
    Failed/blocked artifacts may be reconciled only when the caller has named
    them through the validated retry flow for this run.
    """

    completed = {
        str(item.get("artifact_id", ""))
        for item in progress.get("completed", [])
        if isinstance(item, dict) and item.get("artifact_id")
    }
    terminal = {
        str(item.get("artifact_id", ""))
        for key in ("failed", "blocked")
        for item in progress.get(key, [])
        if isinstance(item, dict) and item.get("artifact_id")
    }
    return completed | (terminal - explicitly_retried_ids)


def failed_ids(progress: dict[str, Any]) -> set[str]:
    """Return the only terminal state that may enter an explicit retry."""

    values = progress.get("failed", [])
    if not isinstance(values, list):
        raise BatchError("progress.failed must be a list")
    return {
        str(item["artifact_id"])
        for item in values
        if isinstance(item, dict) and item.get("artifact_id")
    }


def validate_plan(plan: dict[str, Any], progress: dict[str, Any]) -> None:
    entries = plan.get("entries")
    if plan.get("schema_version") != 1 or not isinstance(entries, list):
        raise BatchError("archive plan must be schema 1 with entries")
    expected_sha = progress.get("plan", {}).get("sha256")
    if not expected_sha:
        raise BatchError("progress is missing plan.sha256")
    # The state CLI performs the authoritative plan fingerprint verification.


@contextmanager
def exclusive_lock(path: Path):
    """Hold one cross-platform writer lock for the full orchestrator run."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise BatchError(f"lock file must not be a symlink: {path}")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise BatchError(f"cannot safely open lock file: {path}") from exc
    handle = os.fdopen(descriptor, "r+b", buffering=0)
    locked = False
    try:
        descriptor_stat = os.fstat(handle.fileno())
        path_stat = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(descriptor_stat.st_mode):
            raise BatchError(f"lock file is not a regular file: {path}")
        if descriptor_stat.st_nlink != 1:
            raise BatchError(f"lock file must have exactly one hard link: {path}")
        if (descriptor_stat.st_dev, descriptor_stat.st_ino) != (path_stat.st_dev, path_stat.st_ino):
            raise BatchError(f"lock file changed while opening: {path}")
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise BatchError(f"another archive orchestrator holds the lock: {path}") from exc
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise BatchError(f"another archive orchestrator holds the lock: {path}") from exc
        locked = True
        # The lock file is deliberately never written. This makes an unexpected
        # hard-link race non-destructive even after the preflight identity check.
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def validate_concurrency_proof(
    path: Path | None, *, workers: int, plan: dict[str, Any], progress: dict[str, Any]
) -> dict[str, Any] | None:
    if workers == 1:
        return None
    if path is None:
        raise BatchError("--concurrency-proof is required when --workers is greater than 1")
    proof = load_json(path)
    if proof.get("schema_version") != 1 or proof.get("status") != "passed":
        raise BatchError("concurrency proof is not a passed schema-1 proof")
    if int(proof.get("max_workers", 0)) < workers:
        raise BatchError(f"concurrency proof permits fewer than {workers} workers")
    if proof.get("plan_sha256") != progress.get("plan", {}).get("sha256"):
        raise BatchError("concurrency proof belongs to another archive plan")
    samples = proof.get("samples")
    if not isinstance(samples, list) or len(samples) < workers:
        raise BatchError("concurrency proof has too few independently verified samples")
    if any(sample.get("semantic_status") != "matched" for sample in samples):
        raise BatchError("concurrency proof contains a sample without semantic matching")
    for identity_key in ("artifact_id", "source_url", "sha256"):
        values = [str(sample.get(identity_key, "")) for sample in samples[:workers]]
        if any(not value for value in values) or len(set(values)) != workers:
            raise BatchError(
                f"concurrency proof samples must have {workers} distinct {identity_key} values"
            )
    plan_by_id = {
        str(entry.get("artifact_id", "")): entry
        for entry in plan.get("entries", [])
        if entry.get("artifact_id")
    }
    for sample in samples[:workers]:
        artifact_id = str(sample.get("artifact_id", ""))
        entry = plan_by_id.get(artifact_id)
        if entry is None:
            raise BatchError(f"concurrency proof sample is not in the current plan: {artifact_id}")
        if str(sample.get("title", "")) != str(entry.get("title", "")):
            raise BatchError(f"concurrency proof title differs from the plan: {artifact_id}")
        completed = next(
            (
                item
                for item in progress.get("completed", [])
                if str(item.get("artifact_id", "")) == artifact_id
            ),
            None,
        )
        if not completed:
            raise BatchError(
                f"concurrency proof sample has no completed archive evidence: {artifact_id}"
            )
        destination = Path(str(completed.get("archive_destination", "")))
        if not destination.is_file() or destination.is_symlink():
            raise BatchError(f"concurrency proof archive file is unavailable: {artifact_id}")
        actual_sha256 = sha256(destination)
        if (
            str(sample.get("sha256", "")) != actual_sha256
            or str(completed.get("sha256", "")) != actual_sha256
        ):
            raise BatchError(f"concurrency proof SHA-256 is not replayable: {artifact_id}")
        inspection = inspect_download(destination, entry)
        if inspection.get("semantic_status") != "matched":
            raise BatchError(f"concurrency proof semantic evidence did not replay: {artifact_id}")
        metadata_path = destination.parent / "metadata.yml"
        if not metadata_path.is_file() or metadata_path.is_symlink():
            raise BatchError(f"concurrency proof metadata is unavailable: {artifact_id}")
        metadata = read_top_level_metadata(metadata_path)
        if (
            str(metadata.get("artifact_id", "")) != artifact_id
            or str(metadata.get("sha256", "")) != actual_sha256
            or str(metadata.get("title", "")) != str(entry.get("title", ""))
        ):
            raise BatchError(f"concurrency proof metadata differs from the archive: {artifact_id}")
        sample_url = str(sample.get("source_url", ""))
        sample_remote_id = str(sample.get("remote_id", ""))
        observed_remote_id = verify_source_identity(
            {"source_url": sample_url, "remote_id": sample_remote_id}, sample_url
        )
        expected_url = str(metadata.get("source_url") or "").strip()
        expected_remote_id = str(metadata.get("remote_id") or "").strip()
        plan_url = str(entry.get("source_url") or "").strip()
        plan_remote_id = str(entry.get("remote_id") or "").strip()
        if plan_url and normalized_processon_source_url(plan_url) != normalized_processon_source_url(
            expected_url
        ):
            raise BatchError(f"plan source URL differs from archived evidence: {artifact_id}")
        if plan_remote_id and plan_remote_id != expected_remote_id:
            raise BatchError(f"plan remote id differs from archived evidence: {artifact_id}")
        if normalized_processon_source_url(sample_url) != normalized_processon_source_url(
            expected_url
        ) or observed_remote_id != expected_remote_id:
            raise BatchError(
                f"concurrency proof source identity differs from archived evidence: {artifact_id}"
            )
    lifecycle = proof.get("lifecycle", {})
    scoped_opened = int(lifecycle.get("scoped_pages_opened", 0))
    scoped_closed = int(lifecycle.get("scoped_pages_closed", 0))
    if scoped_opened != scoped_closed or scoped_opened < workers:
        raise BatchError("concurrency proof has unmatched popup lifecycle counts")
    worker_opened = int(lifecycle.get("worker_pages_opened", 0))
    worker_closed = int(lifecycle.get("worker_pages_closed", 0))
    if worker_opened != worker_closed or worker_opened < workers:
        raise BatchError("concurrency proof has unmatched worker-page lifecycle counts")
    if "pages_remaining" in lifecycle and int(lifecycle["pages_remaining"]) != 0:
        raise BatchError("concurrency proof left browser pages open")
    if "pages_closed_at_exit" in lifecycle and int(lifecycle["pages_closed_at_exit"]) != 0:
        raise BatchError("concurrency proof relied on context-exit cleanup for live pages")
    if "pages_remaining" not in lifecycle and "pages_closed_at_exit" not in lifecycle:
        raise BatchError("concurrency proof is missing final page lifecycle evidence")
    return proof


def safe_relative_parts(source_path: str) -> tuple[str, ...]:
    pure = PurePosixPath(source_path)
    if pure.is_absolute() or not pure.parts:
        raise BatchError(f"invalid source_path: {source_path!r}")
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise BatchError(f"unsafe source_path: {source_path!r}")
    return pure.parts


def output_folder(output_root: Path, entry: dict[str, Any]) -> Path:
    parts = list(safe_relative_parts(str(entry["source_directory"])))
    title = str(entry["title"])
    title_component = provider_safe_filename_stem(title).strip()
    if title_component in {"", ".", ".."}:
        raise BatchError(f"unsafe archive title: {title!r}")
    if (
        title_component != title
        or entry.get("collision_risk") not in {None, "", "none_detected"}
    ):
        title_component = f"{title_component}--{str(entry['artifact_id'])[:8]}"
    parts.append(title_component)
    root = output_root.expanduser().resolve(strict=False)
    target = root.joinpath(*parts).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise BatchError(f"archive target escapes output root: {target}") from exc
    return target


def choose_entries(
    plan: dict[str, Any],
    progress: dict[str, Any],
    limit: int,
    *,
    workers: int,
    retry_failed: bool = False,
    retry_blocked: bool = False,
    artifact_ids: list[str] | None = None,
    collision_confirmations: OrderedDict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Choose normal pending work or a caller-whitelisted failed retry.

    A failed retry is deliberately not a queue-wide switch: the caller must
    name every planned artifact.  This preserves the immutable failure
    evidence and prevents a transient UI change from turning into a blind
    retry storm.
    """

    requested_ids = [str(item).strip() for item in artifact_ids or []]
    confirmed_collisions = collision_confirmations or OrderedDict()
    if confirmed_collisions:
        if workers != 1:
            raise BatchError("collision confirmation requires --workers 1")
        if retry_failed or retry_blocked or requested_ids:
            raise BatchError(
                "collision confirmation is a dedicated flow and cannot be combined with failed retry"
            )
        # A current explicit collision confirmation may retry a formerly
        # blocked/failed item after the provider capability changed.  Never
        # redownload an already completed artifact.
        done = {
            str(item.get("artifact_id", ""))
            for item in progress.get("completed", [])
        }
        selected: list[dict[str, Any]] = []
        for artifact_id, confirmation in confirmed_collisions.items():
            if artifact_id in done:
                continue
            entry = dict(confirmation["entry"])
            entry["_collision_occurrence"] = int(confirmation["occurrence"])
            entry["_collision_group_size"] = int(confirmation["group_size"])
            entry["_collision_plan_group_size"] = int(confirmation["plan_group_size"])
            entry["_collision_confirmation_method"] = "inventory_order"
            entry["_collision_selection_scope"] = str(confirmation["selection_scope"])
            selected.append(entry)
            if len(selected) >= limit:
                break
        return selected
    if retry_failed and retry_blocked:
        raise BatchError("--retry-failed and --retry-blocked are mutually exclusive")
    if requested_ids and not (retry_failed or retry_blocked):
        raise BatchError("--artifact-id requires --retry-failed or --retry-blocked")
    if retry_failed and not requested_ids:
        raise BatchError("--retry-failed requires one or more --artifact-id values")
    if retry_blocked and not requested_ids:
        raise BatchError("--retry-blocked requires one or more --artifact-id values")
    if len(set(requested_ids)) != len(requested_ids):
        raise BatchError("--artifact-id values must be unique")

    plan_by_id = {
        str(entry.get("artifact_id", "")): entry
        for entry in plan["entries"]
        if entry.get("artifact_id")
    }
    requested_set = set(requested_ids)
    unknown_ids = requested_set - set(plan_by_id)
    if unknown_ids:
        raise BatchError(f"--artifact-id is not in the current plan: {sorted(unknown_ids)[0]}")
    if retry_failed:
        retryable_ids = failed_ids(progress)
        not_failed_ids = requested_set - retryable_ids
        if not_failed_ids:
            raise BatchError(
                "--retry-failed may only name artifacts currently in progress.failed: "
                f"{sorted(not_failed_ids)[0]}"
            )
        for artifact_id in requested_ids:
            entry = plan_by_id[artifact_id]
            if entry.get("confirmation_required") or entry.get("type") == "unknown":
                raise BatchError(
                    f"--retry-failed cannot name an unconfirmed artifact: {artifact_id}"
                )
            if entry.get("collision_risk") not in {None, "", "none_detected"}:
                raise BatchError(
                    f"--retry-failed cannot name a collision-risk artifact: {artifact_id}"
                )
    if retry_blocked:
        retryable_ids = {
            str(item.get("artifact_id", "")) for item in progress.get("blocked", [])
        }
        not_blocked_ids = requested_set - retryable_ids
        if not_blocked_ids:
            raise BatchError(
                "--retry-blocked may only name artifacts currently in progress.blocked: "
                f"{sorted(not_blocked_ids)[0]}"
            )
        for artifact_id in requested_ids:
            entry = plan_by_id[artifact_id]
            if entry.get("confirmation_required") or entry.get("type") == "unknown":
                raise BatchError(
                    f"--retry-blocked cannot name an unconfirmed artifact: {artifact_id}"
                )
            if entry.get("collision_risk") not in {None, "", "none_detected"}:
                raise BatchError(
                    f"--retry-blocked cannot name a collision-risk artifact: {artifact_id}"
                )

    done = progress_done_ids(progress)
    if retry_failed or retry_blocked:
        done -= requested_set
    selected: list[dict[str, Any]] = []
    for entry in plan["entries"]:
        if entry.get("confirmation_required") or entry.get("type") == "unknown":
            continue
        artifact_id = str(entry.get("artifact_id", ""))
        if requested_set and artifact_id not in requested_set:
            continue
        if not artifact_id or artifact_id in done:
            continue
        if entry.get("collision_risk") not in {None, "", "none_detected"}:
            continue
        selected.append(entry)
        if len(selected) >= limit:
            break
    return selected


def deferred_collision_entries(
    plan: dict[str, Any],
    progress: dict[str, Any],
    *,
    confirmed_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    done = progress_done_ids(progress)
    authorized = confirmed_ids or set()
    return [
        entry
        for entry in plan["entries"]
        if str(entry.get("artifact_id", "")) not in done
        and str(entry.get("artifact_id", "")) not in authorized
        and not entry.get("confirmation_required")
        and entry.get("type") != "unknown"
        and entry.get("collision_risk") not in {None, "", "none_detected"}
    ]


def load_collision_confirmation(
    path: Path | None,
    *,
    plan_path: Path,
    plan: dict[str, Any],
    progress: dict[str, Any],
) -> OrderedDict[str, dict[str, Any]]:
    """Load a fail-closed, plan-bound confirmation for duplicate-title rows."""

    if path is None:
        return OrderedDict()
    confirmation_path = path.expanduser().resolve(strict=False)
    if confirmation_path.is_symlink() or not confirmation_path.is_file():
        raise BatchError(
            f"collision confirmation must be a regular file: {confirmation_path}"
        )
    payload = load_json(confirmation_path)
    expected_sha = str(progress.get("plan", {}).get("sha256") or "")
    actual_sha = sha256(plan_path.expanduser().resolve(strict=True))
    if not expected_sha or actual_sha != expected_sha:
        raise BatchError("current archive plan SHA-256 differs from progress")
    if (
        payload.get("schema_version") != 1
        or payload.get("kind") != "processon_collision_confirmation"
        or payload.get("confirmation_method") != "inventory_order"
        or str(payload.get("plan_sha256") or "") != expected_sha
    ):
        raise BatchError("collision confirmation is not bound to the current plan")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise BatchError("collision confirmation must contain at least one entry")

    plan_by_id = {
        str(entry.get("artifact_id", "")): entry
        for entry in plan["entries"]
        if entry.get("artifact_id")
    }
    groups: OrderedDict[tuple[str, str], list[dict[str, Any]]] = OrderedDict()
    for entry in plan["entries"]:
        if entry.get("collision_risk") in {None, "", "none_detected"}:
            continue
        key = (str(entry.get("source_directory", "")), str(entry.get("title", "")))
        groups.setdefault(key, []).append(entry)

    confirmed: OrderedDict[str, dict[str, Any]] = OrderedDict()
    plan_indexes = {
        str(entry.get("artifact_id", "")): index
        for index, entry in enumerate(plan["entries"])
    }
    previous_plan_index = -1
    for item in raw_entries:
        if not isinstance(item, dict):
            raise BatchError("collision confirmation entries must be objects")
        artifact_id = str(item.get("artifact_id") or "")
        if not artifact_id or artifact_id in confirmed:
            raise BatchError("collision confirmation artifact ids must be non-empty and unique")
        entry = plan_by_id.get(artifact_id)
        if entry is None:
            raise BatchError(f"collision confirmation artifact is not in the plan: {artifact_id}")
        if entry.get("confirmation_required") or entry.get("type") == "unknown":
            raise BatchError(f"collision confirmation cannot authorize an unknown artifact: {artifact_id}")
        if entry.get("collision_risk") in {None, "", "none_detected"}:
            raise BatchError(f"collision confirmation named a non-collision artifact: {artifact_id}")
        source_directory = str(entry.get("source_directory", ""))
        title = str(entry.get("title", ""))
        group = groups[(source_directory, title)]
        expected_occurrence = next(
            index
            for index, candidate in enumerate(group)
            if str(candidate.get("artifact_id", "")) == artifact_id
        )
        if (
            str(item.get("source_directory", "")) != source_directory
            or str(item.get("title", "")) != title
            or item.get("occurrence") != expected_occurrence
            or item.get("group_size") != len(group)
        ):
            raise BatchError(
                f"collision confirmation order or group metadata differs from the plan: {artifact_id}"
            )
        current_plan_index = plan_indexes[artifact_id]
        if current_plan_index <= previous_plan_index:
            raise BatchError("collision confirmation entries must follow archive plan order")
        previous_plan_index = current_plan_index
        selection_group = [
            candidate
            for candidate in group
            if all(
                str(candidate.get(key, "")) == str(entry.get(key, ""))
                for key in ("type", "owner", "remote_updated_at")
            )
        ]
        selection_occurrence = next(
            index
            for index, candidate in enumerate(selection_group)
            if str(candidate.get("artifact_id", "")) == artifact_id
        )
        confirmed[artifact_id] = {
            "entry": entry,
            "occurrence": selection_occurrence,
            "group_size": len(selection_group),
            "plan_group_size": len(group),
            "selection_scope": "type_owner_remote_updated_at",
        }
    return confirmed


def legacy_flat_download_review(progress: dict[str, Any]) -> dict[str, Any]:
    downloads_root = (Path.home() / "Downloads").resolve(strict=False)
    flat: list[dict[str, Any]] = []
    numbered: list[dict[str, Any]] = []
    for item in progress.get("completed", []):
        if not isinstance(item, dict) or not item.get("download_source"):
            continue
        source = Path(str(item["download_source"])).expanduser().resolve(strict=False)
        if source.parent != downloads_root:
            continue
        summary = {
            "artifact_id": str(item.get("artifact_id", "")),
            "source_path": str(item.get("source_path", "")),
            "download_source": str(source),
            "archive_destination": str(item.get("archive_destination", "")),
        }
        flat.append(summary)
        if re.search(r" \(\d+\)$", source.stem):
            numbered.append(summary)
    completed_count = len(progress.get("completed", []))
    return {
        "flat_downloads_completed_count": len(flat),
        "revalidation_required_count": len(flat),
        "numbered_suffix_review_count": len(numbered),
        "trusted_completed_count": max(completed_count - len(flat), 0),
        "claim_status": "revalidation_required" if flat else "trusted",
        "revalidation_items": flat,
        "numbered_suffix_items": numbered,
    }


def build_jobs(entries: list[dict[str, Any]], workers: int) -> list[tuple[str, list[dict[str, Any]]]]:
    by_directory: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for entry in entries:
        by_directory.setdefault(str(entry["source_directory"]), []).append(entry)
    jobs: list[tuple[str, list[dict[str, Any]]]] = []
    for directory, items in by_directory.items():
        shard_count = min(workers, len(items)) if len(items) >= workers else 1
        shards = [[] for _ in range(shard_count)]
        for index, item in enumerate(items):
            shards[index % shard_count].append(item)
        jobs.extend((directory, shard) for shard in shards if shard)
    return jobs


def directory_segments(root_path: str, source_directory: str) -> list[str]:
    root_parts = safe_relative_parts(root_path)
    directory_parts = safe_relative_parts(source_directory)
    if tuple(directory_parts[: len(root_parts)]) != root_parts:
        raise BatchError(f"directory is outside plan root: {source_directory}")
    return list(directory_parts[len(root_parts) :])


async def wait_visible_text(page: Any, text: str, timeout_ms: int) -> Any:
    locator = page.get_by_text(text, exact=True).filter(visible=True).nth(0)
    await locator.wait_for(state="visible", timeout=timeout_ms)
    return locator


async def scroll_processon_file_list(page: Any) -> None:
    """Aim wheel input at ProcessOn's virtualized file-list container.

    Some deep folders render their rows inside an internal ``file_list``
    scroller while ``window.scrollY`` remains unchanged.  Hovering the fixed
    provider container first preserves the existing page-wheel fallback and
    lets the virtual list materialize rows that are below the viewport.
    """

    try:
        container = page.locator("div.file_list, ul.file_list, .file_list").filter(
            visible=True
        ).nth(0)
        if not await container.count():
            # The list wrapper is not stable across ProcessOn views, but a
            # visible row is.  Hovering the row still routes wheel input to
            # its nearest scrollable ancestor.
            container = page.locator("div.file_list_item").filter(visible=True).nth(0)
        if await container.count():
            await container.hover(timeout=500)
    except Exception:
        # The page-wheel fallback below remains valid for non-virtual layouts
        # and for provider markup changes where the fixed container is absent.
        pass
    await page.mouse.move(720, 850)
    await page.mouse.wheel(0, 900)


async def wait_folder_row(page: Any, text: str, timeout_ms: int) -> Any:
    deadline = time.monotonic() + timeout_ms / 1000
    previous_marker: tuple[int, str] | None = None
    unchanged = 0
    while time.monotonic() < deadline:
        candidates = page.get_by_text(text, exact=True).filter(visible=True)
        count = await candidates.count()
        matches: list[Any] = []
        for index in range(count):
            candidate = candidates.nth(index)
            row = candidate.locator(
                "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' file_list_item ')][1]"
            )
            if await row.count():
                matches.append(candidate)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise BatchError(f"folder row is ambiguous: {text!r}")
        marker = (
            int(await page.evaluate("() => Math.round(window.scrollY || 0)")),
            (await page.locator("body").inner_text())[-500:],
        )
        unchanged = unchanged + 1 if marker == previous_marker else 0
        previous_marker = marker
        if unchanged >= 2:
            break
        await scroll_processon_file_list(page)
        await page.wait_for_timeout(300)
    raise BatchError(f"folder row did not become visible: {text!r}")


async def wait_folder_path_row(
    page: Any, segments: list[str], start: int, timeout_ms: int
) -> tuple[Any, int]:
    """Find a folder using the longest slash-containing name first.

    ProcessOn permits `/` in a folder name, while the inventory path uses `/`
    as its logical separator.  Trying the longest joined candidate preserves
    the actual folder boundary without weakening exact row matching.
    """

    candidates_by_length: list[tuple[str, int]] = []
    for end in range(len(segments), start, -1):
        name = "/".join(segments[start:end])
        if name and (name, end) not in candidates_by_length:
            candidates_by_length.append((name, end))
    deadline = time.monotonic() + timeout_ms / 1000
    previous_marker: tuple[int, str] | None = None
    unchanged = 0
    while time.monotonic() < deadline:
        for name, end in candidates_by_length:
            candidates = page.get_by_text(name, exact=True).filter(visible=True)
            count = await candidates.count()
            matches: list[Any] = []
            for index in range(count):
                candidate = candidates.nth(index)
                row = candidate.locator(
                    "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' file_list_item ')][1]"
                )
                if await row.count():
                    matches.append(candidate)
            if len(matches) == 1:
                return matches[0], end
            if len(matches) > 1:
                raise BatchError(f"folder row is ambiguous: {name!r}")
        marker = (
            int(await page.evaluate("() => Math.round(window.scrollY || 0)")),
            (await page.locator("body").inner_text())[-500:],
        )
        unchanged = unchanged + 1 if marker == previous_marker else 0
        previous_marker = marker
        if unchanged >= 2:
            break
        await scroll_processon_file_list(page)
        await page.wait_for_timeout(300)
    raise BatchError(f"folder row did not become visible: {segments[start]!r}")


async def reset_to_team_root(page: Any, root_label: str, timeout_ms: int) -> None:
    breadcrumb = page.locator("div.breadc").filter(visible=True).nth(0)
    await breadcrumb.wait_for(state="visible", timeout=timeout_ms)
    crumbs = breadcrumb.locator("div.wrap_bre")
    if await crumbs.count() < 1:
        raise BatchError("ProcessOn breadcrumb has no root item")
    first = crumbs.nth(0)
    if (await first.inner_text()).strip() != root_label:
        raise BatchError("ProcessOn breadcrumb root differs from archive plan root")
    if await crumbs.count() > 1:
        link = first.locator("div.wrap_link")
        await link.click(timeout=timeout_ms)
        await page.wait_for_timeout(1200)
    refreshed = page.locator("div.breadc").filter(visible=True).nth(0).locator("div.wrap_bre")
    if await refreshed.count() != 1 or (await refreshed.nth(0).inner_text()).strip() != root_label:
        raise BatchError("failed to reset ProcessOn breadcrumb to the team root")


async def async_target_accessible(page: Any, target_url: str) -> bool:
    if not target_reached(page.url, target_url):
        return False
    selectors = (
        "input[type='password']",
        "input[autocomplete='current-password']",
        "input[placeholder*='手机号']",
        "input[placeholder*='邮箱']",
        "form[action*='login']",
    )
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if await locator.count() and await locator.first.is_visible():
                return False
        except Exception:
            continue
    return True


async def async_safe_close_page(page: Any) -> bool:
    try:
        if not page.is_closed():
            await page.close(run_before_unload=False)
        return True
    except Exception:
        return False


async def navigate_directory(
    page: Any,
    *,
    team_url: str,
    root_path: str,
    source_directory: str,
    settle_ms: int,
    timeout_ms: int,
) -> None:
    root_label = safe_relative_parts(root_path)[-1]
    segments = directory_segments(root_path, source_directory)
    last_error: Exception | None = None
    for attempt in range(READY_ATTEMPTS):
        try:
            await page.goto(team_url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(settle_ms + attempt * 1000)
            if not await async_target_accessible(page, team_url):
                raise BatchError("dedicated ProcessOn profile is not logged in")
            await reset_to_team_root(page, root_label, min(timeout_ms, 20_000))
            segment_index = 0
            while segment_index < len(segments):
                locator, next_index = await wait_folder_path_row(
                    page, segments, segment_index, min(timeout_ms, 20_000)
                )
                await locator.click(click_count=2, timeout=timeout_ms)
                await page.wait_for_timeout(1200)
                segment_index = next_index
            return
        except Exception as exc:
            last_error = exc
            if attempt + 1 < READY_ATTEMPTS:
                continue
    raise BatchError(
        f"directory did not become ready after {READY_ATTEMPTS} attempts: "
        f"{source_directory}; {type(last_error).__name__}: {last_error}"
    )


async def prepare_exact_team_search(
    page: Any,
    *,
    team_url: str,
    entry: dict[str, Any],
    settle_ms: int,
    timeout_ms: int,
) -> str:
    """Use only the provider's team search with the exact planned title.

    This fallback is intentionally limited to a non-sensitive plan title and
    runs only after the audited source path no longer resolves.  The normal
    row/type/owner/update and editor-title checks still run before download.
    """

    await page.goto(team_url, wait_until="domcontentloaded", timeout=timeout_ms)
    await page.wait_for_timeout(max(settle_ms, 1_000))
    if not await async_target_accessible(page, team_url):
        raise BatchError("dedicated ProcessOn profile is not logged in")
    search = None
    for selector in TEAM_SEARCH_INPUT_SELECTORS:
        candidate = page.locator(selector).filter(visible=True).nth(0)
        if await candidate.count() and await candidate.is_visible():
            search = candidate
            break
    if search is None:
        raise BatchError("ProcessOn team search input is not visible")
    title = str(entry["title"])
    await search.fill(title, timeout=timeout_ms)
    await search.press("Enter", timeout=timeout_ms)
    await page.wait_for_timeout(1_500)
    try:
        await find_title(
            page,
            title,
            timeout_ms,
            document_type=str(entry.get("type", "")),
            owner=str(entry.get("owner", "")),
            remote_updated_at=str(entry.get("remote_updated_at", "")),
            allow_unique_owner_type_update_drift=True,
        )
        return "exact_team_search_with_inventory_metadata"
    except BatchError:
        # The provider may have changed owner/relative-update metadata after
        # the old inventory.  Fall back only when the exact title and fixed
        # document type identify one row across the whole team search.
        await find_title(
            page,
            title,
            timeout_ms,
            document_type=str(entry.get("type", "")),
        )
        return "exact_team_search_unique_title_and_type"


def relative_update_matches(expected: str, observed: str) -> bool:
    """Allow only the narrow month-boundary drift seen in relative UI labels."""

    if expected == observed:
        return True
    expected_match = re.fullmatch(r"(\d+)月前", expected)
    observed_match = re.fullmatch(r"(\d+)月前", observed)
    return bool(
        expected_match
        and observed_match
        and abs(int(expected_match.group(1)) - int(observed_match.group(1))) <= 1
    )


async def find_title(
    page: Any,
    title: str,
    timeout_ms: int,
    *,
    occurrence: int = 0,
    expected_matches: int = 1,
    document_type: str = "",
    owner: str = "",
    remote_updated_at: str = "",
    allow_additional_matches: bool = False,
    allow_unique_owner_type_update_drift: bool = False,
) -> Any:
    if occurrence < 0 or expected_matches < 1 or occurrence >= expected_matches:
        raise BatchError("invalid confirmed collision occurrence")
    deadline = time.monotonic() + timeout_ms / 1000
    previous_marker: tuple[int, str, tuple[str, ...]] | None = None
    unchanged = 0
    while time.monotonic() < deadline:
        relaxed_update_matches: list[Any] = []
        try:
            # Count concrete rows, not matching text nodes.  ProcessOn may
            # render the same visible title twice inside one row (for example
            # a label plus a tooltip clone); treating both nodes as files makes
            # a confirmed group of N rows look like more than N artifacts.
            visible_rows = page.locator("div.file_list_item").filter(visible=True)
            row_count = min(await visible_rows.count(), 512)
            matches: list[Any] = []
            for index in range(row_count):
                row = visible_rows.nth(index)
                if not await row.is_visible():
                    continue
                title_nodes = row.get_by_text(title, exact=True).filter(visible=True)
                if not await title_nodes.count():
                    continue
                if document_type == "flowchart":
                    flowchart_icon_found = False
                    for selector in FLOWCHART_ICON_SELECTORS:
                        if await row.locator(selector).count():
                            flowchart_icon_found = True
                            break
                    if not flowchart_icon_found:
                        continue
                elif document_type == "mindmap":
                    if not await row.locator(".icon-a-siweidaotu1_huaban1").count():
                        continue
                elif document_type:
                    raise BatchError(
                        f"unsupported confirmed collision document type: {document_type}"
                    )
                if owner or remote_updated_at:
                    visible_lines = {
                        line.strip()
                        for line in (await row.inner_text()).splitlines()
                        if line.strip()
                    }
                    if owner and owner not in visible_lines:
                        continue
                    if remote_updated_at:
                        relaxed_update_matches.append(title_nodes.nth(0))
                        observed_updates = [
                            line.removeprefix("更新于")
                            for line in visible_lines
                            if line.startswith("更新于")
                        ]
                        if not any(
                            relative_update_matches(remote_updated_at, observed)
                            for observed in observed_updates
                        ):
                            continue
                matches.append(title_nodes.nth(0))
            if len(matches) > expected_matches and not allow_additional_matches:
                raise BatchError(
                    f"visible duplicate-title row count exceeds confirmation: {title!r}"
                )
            if len(matches) >= expected_matches:
                return matches[occurrence]
        except BatchError:
            raise
        except Exception:
            pass
        visible_rows = page.locator("div.file_list_item").filter(visible=True)
        row_count = min(await visible_rows.count(), 6)
        row_texts_list: list[str] = []
        for index in range(row_count):
            row_texts_list.append((await visible_rows.nth(index).inner_text()).strip()[:160])
        row_texts = tuple(row_texts_list)
        marker = (
            int(await page.evaluate("() => Math.round(window.scrollY || 0)")),
            (await page.locator("body").inner_text())[-500:],
            row_texts,
        )
        unchanged = unchanged + 1 if marker == previous_marker else 0
        previous_marker = marker
        # A virtual list may update rows while window.scrollY and the body
        # tail remain unchanged.  Require several unchanged row snapshots
        # before giving up, but keep the overall timeout bounded.
        if unchanged >= 4:
            if (
                allow_unique_owner_type_update_drift
                and owner
                and document_type in {"flowchart", "mindmap"}
                and expected_matches == 1
                and len(relaxed_update_matches) == 1
            ):
                return relaxed_update_matches[0]
            break
        await scroll_processon_file_list(page)
        await page.wait_for_timeout(350)
    if expected_matches > 1:
        raise BatchError(
            "confirmed duplicate-title rows were not simultaneously visible after bounded "
            f"virtual-list scroll: {title!r}; expected {expected_matches}"
        )
    raise BatchError(f"title is not visible after bounded virtual-list scroll: {title}")


def safe_download_path(download_dir: Path, artifact_id: str, suggested_filename: str) -> Path:
    name = Path(suggested_filename).name
    if name in {"", ".", ".."}:
        raise BatchError("ProcessOn returned an invalid filename")
    artifact_dir = download_dir / artifact_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    destination = artifact_dir / name
    if destination.exists():
        destination = artifact_dir / f"{Path(name).stem}--{time.time_ns()}{Path(name).suffix}"
    return destination


def staging_receipt_root(progress_path: Path) -> Path:
    """Return the private, per-run journal for downloaded-but-unfinalized files."""

    return progress_path.expanduser().resolve(strict=False).parent / "staging-receipts"


def staging_receipt_path(progress_path: Path, artifact_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", artifact_id):
        raise BatchError("staging receipt requires a SHA-256 artifact id")
    root = staging_receipt_root(progress_path)
    if root.is_symlink():
        raise BatchError(f"staging receipt root must not be a symlink: {root}")
    return root / f"{artifact_id}.json"


def write_staging_receipt(progress_path: Path, result: dict[str, Any]) -> Path:
    """Atomically checkpoint a verified browser download before finalization.

    The checkpoint contains only source-binding metadata and the managed
    artifact-isolated path. It makes an interrupted batch recoverable without
    trusting an arbitrary file later found in staging.
    """

    artifact_id = str(result.get("artifact_id", ""))
    download = result.get("download")
    if not isinstance(download, dict):
        raise BatchError("cannot checkpoint a browser result without download metadata")
    required = ("source_path", "title", "requested_format", "source_url", "source_title", "remote_id")
    if any(not str(result.get(key, "")).strip() for key in required):
        raise BatchError("cannot checkpoint a browser result without source-binding metadata")
    if not str(download.get("path", "")).strip() or not str(download.get("suggested_filename", "")).strip():
        raise BatchError("cannot checkpoint a browser result without download path and filename")
    target = staging_receipt_path(progress_path, artifact_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise BatchError(f"staging receipt target must not be a symlink: {target}")
    payload = {
        "schema_version": 1,
        "kind": "processon_staging_download",
        "artifact_id": artifact_id,
        "source_path": str(result["source_path"]),
        "title": str(result["title"]),
        "requested_format": str(result["requested_format"]),
        "actual_format": str(result.get("actual_format", result["requested_format"])),
        "fallback_used": bool(result.get("fallback_used", False)),
        "fallback_reason": str(result.get("fallback_reason", "")),
        "source_url": str(result["source_url"]),
        "source_title": str(result["source_title"]),
        "remote_id": str(result["remote_id"]),
        "download_menu": str(result.get("download_menu", "")),
        "source_lookup_method": str(
            result.get("source_lookup_method", "planned_directory")
        ),
        "empty_source_confirmed": bool(result.get("empty_source_confirmed", False)),
        "empty_source_editor_signal": str(
            result.get("empty_source_editor_signal", "")
        ),
        "collision_binding": result.get("collision_binding"),
        "download": {
            "path": str(download["path"]),
            "bytes": int(download.get("bytes", 0)),
            "suggested_filename": str(download["suggested_filename"]),
        },
        "created_at": utc_now(),
    }
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    return target


def remove_staging_receipt(progress_path: Path, artifact_id: str) -> None:
    target = staging_receipt_path(progress_path, artifact_id)
    if not target.exists():
        return
    if target.is_symlink() or not target.is_file():
        raise BatchError(f"staging receipt target is not a regular file: {target}")
    target.unlink()


def load_staging_result(
    receipt_path: Path, entry: dict[str, Any], *, args: argparse.Namespace
) -> dict[str, Any]:
    """Fail closed unless a journal binds one regular staged file to one plan entry."""

    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise BatchError(f"staging receipt is not a regular file: {receipt_path}")
    payload = load_json(receipt_path)
    artifact_id = str(entry.get("artifact_id", ""))
    if (
        receipt_path.name != f"{artifact_id}.json"
        or payload.get("schema_version") != 1
        or payload.get("kind") != "processon_staging_download"
        or str(payload.get("artifact_id", "")) != artifact_id
    ):
        raise BatchError(f"staging receipt does not bind the expected artifact: {receipt_path}")
    for receipt_key, plan_key in (
        ("source_path", "source_path"),
        ("title", "title"),
        ("requested_format", "primary_format"),
    ):
        if str(payload.get(receipt_key, "")) != str(entry.get(plan_key, "")):
            raise BatchError(f"staging receipt {receipt_key} differs from the plan: {receipt_path}")
    source_url = str(payload.get("source_url", ""))
    source_title = str(payload.get("source_title", ""))
    remote_id = str(payload.get("remote_id", ""))
    if not source_title_matches(str(entry["title"]), source_title):
        raise BatchError(f"staging receipt source title differs from the plan: {receipt_path}")
    observed_remote_id = verify_source_identity(entry, source_url)
    if observed_remote_id != remote_id:
        raise BatchError(f"staging receipt remote id differs from source URL: {receipt_path}")
    expected_collision = None
    if "_collision_occurrence" in entry:
        expected_collision = {
            "confirmation_method": str(entry["_collision_confirmation_method"]),
            "occurrence": int(entry["_collision_occurrence"]),
            "group_size": int(entry["_collision_group_size"]),
        }
    if payload.get("collision_binding") != expected_collision:
        raise BatchError(f"staging receipt collision binding differs from the plan: {receipt_path}")
    download = payload.get("download")
    if not isinstance(download, dict):
        raise BatchError(f"staging receipt has no download object: {receipt_path}")
    source = Path(str(download.get("path", ""))).expanduser().resolve(strict=False)
    expected_parent = (args.download_dir / artifact_id).expanduser().resolve(strict=False)
    if source.parent != expected_parent or source.is_symlink() or not source.is_file():
        raise BatchError(f"staging receipt download is not an isolated regular file: {receipt_path}")
    if int(download.get("bytes", 0)) != source.stat().st_size or source.stat().st_size <= 0:
        raise BatchError(f"staging receipt byte count differs from staged file: {receipt_path}")
    suggested = str(download.get("suggested_filename", ""))
    if Path(suggested).name != source.name:
        raise BatchError(f"staging receipt filename differs from staged file: {receipt_path}")
    result = {
        "artifact_id": artifact_id,
        "source_path": str(entry["source_path"]),
        "title": str(entry["title"]),
        "requested_format": str(entry["primary_format"]),
        "actual_format": str(
            payload.get("actual_format") or Path(suggested).suffix.lower().lstrip(".")
        ),
        "fallback_used": bool(payload.get("fallback_used", False)),
        "fallback_reason": str(payload.get("fallback_reason", "")),
        "source_url": source_url,
        "source_title": source_title,
        "remote_id": remote_id,
        "download_menu": str(payload.get("download_menu", "")),
        "source_lookup_method": str(
            payload.get("source_lookup_method", "planned_directory")
        ),
        "empty_source_confirmed": bool(payload.get("empty_source_confirmed", False)),
        "empty_source_editor_signal": str(
            payload.get("empty_source_editor_signal", "")
        ),
        "download": {
            "path": str(source),
            "bytes": source.stat().st_size,
            "suggested_filename": suggested,
        },
        "ok": True,
    }
    if expected_collision is not None:
        result["collision_binding"] = expected_collision
    allowed_formats = {
        str(entry.get("primary_format", "")).lower(),
        *(str(item).lower() for item in entry.get("fallback_formats", [])),
    }
    if result["actual_format"] not in allowed_formats:
        raise BatchError(
            f"staging receipt actual format is not allowed by the plan: {receipt_path}"
        )
    if result["fallback_used"] != (
        result["actual_format"] != str(entry.get("primary_format", "")).lower()
    ):
        raise BatchError(f"staging receipt fallback flag is inconsistent: {receipt_path}")
    return result


def collision_entry_authorized(entry: dict[str, Any], args: argparse.Namespace) -> bool:
    if entry.get("collision_risk") in {None, "", "none_detected"}:
        return True
    confirmations = getattr(args, "collision_confirmations", OrderedDict())
    return str(entry.get("artifact_id", "")) in confirmations


def entry_with_collision_confirmation(
    entry: dict[str, Any], args: argparse.Namespace
) -> dict[str, Any]:
    confirmation = getattr(args, "collision_confirmations", OrderedDict()).get(
        str(entry.get("artifact_id", ""))
    )
    if confirmation is None:
        return entry
    decorated = dict(entry)
    decorated["_collision_occurrence"] = int(confirmation["occurrence"])
    decorated["_collision_group_size"] = int(confirmation["group_size"])
    decorated["_collision_plan_group_size"] = int(confirmation["plan_group_size"])
    decorated["_collision_confirmation_method"] = "inventory_order"
    decorated["_collision_selection_scope"] = str(confirmation["selection_scope"])
    return decorated


def download_menu_candidates(entry: dict[str, Any]) -> list[str]:
    """Return ordered, exact ProcessOn menu labels for the requested format."""

    primary_format = str(entry.get("primary_format") or "").strip().lower()
    primary_menu = str(entry.get("primary_menu") or "").strip()
    raw_candidates: list[str]
    if primary_format == "vsdx":
        raw_candidates = [*VSDX_DOWNLOAD_MENU_CANDIDATES, primary_menu]
    else:
        raw_candidates = [primary_menu]
    if "pos" in {
        str(item).strip().lower() for item in entry.get("fallback_formats", [])
    }:
        raw_candidates.extend(POS_DOWNLOAD_MENU_CANDIDATES)
    candidates: list[str] = []
    for label in raw_candidates:
        if label and label not in candidates:
            candidates.append(label)
    if not candidates:
        raise BatchError(
            f"archive entry has no download menu for format {primary_format or '<missing>'}"
        )
    return candidates


def download_menu_format(entry: dict[str, Any], menu_label: str) -> tuple[str, bool, str]:
    """Resolve the observed provider menu to an explicitly plan-authorized format."""

    primary = str(entry.get("primary_format", "")).strip().lower()
    if menu_label in POS_DOWNLOAD_MENU_CANDIDATES:
        actual = "pos"
        reason = "primary_export_menu_unavailable"
    elif menu_label in VSDX_DOWNLOAD_MENU_CANDIDATES:
        actual = "vsdx"
        reason = ""
    else:
        actual = primary
        reason = ""
    allowed = {
        primary,
        *(str(item).strip().lower() for item in entry.get("fallback_formats", [])),
    }
    if actual not in allowed:
        raise BatchError(
            f"download menu {menu_label!r} resolves to format {actual!r}, which is not plan-authorized"
        )
    fallback_used = actual != primary
    return actual, fallback_used, reason if fallback_used else ""


def semantic_control_locators(page: Any, label: str) -> list[Any]:
    """Return fixed, provider-controlled semantic locators for a menu label.

    The browser runner exposes visible text plus standard accessible/title
    attributes.  Keep the batch executor aligned without accepting arbitrary
    selectors: attribute selectors are an allowlist for known ProcessOn menu
    labels only, while every caller-provided plan label remains text-only.
    """

    locators = [page.get_by_text(label, exact=True).filter(visible=True).nth(0)]
    for variant in SEMANTIC_TEXT_VARIANTS.get(label, ()):
        locators.append(page.get_by_text(variant, exact=True).filter(visible=True).nth(0))
    for selector in SEMANTIC_CONTROL_SELECTORS.get(label, ()):
        try:
            locators.append(page.locator(selector).filter(visible=True).nth(0))
        except (AttributeError, TypeError):
            continue
    return locators


async def visible_semantic_control(page: Any, label: str) -> Any | None:
    for locator in semantic_control_locators(page, label):
        try:
            if await locator.count() and await locator.is_visible():
                return locator
        except Exception:
            continue
    return None


async def find_download_menu(
    page: Any, entry: dict[str, Any], timeout_ms: int
) -> tuple[str, Any]:
    """Find the first visible exact menu label without serial full-timeout waits."""

    candidates = download_menu_candidates(entry)
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for label in candidates:
            locator = await visible_semantic_control(page, label)
            if locator is not None:
                return label, locator
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        await page.wait_for_timeout(min(100, remaining_ms))
    raise BatchError(
        "no visible ProcessOn download menu matched: " + ", ".join(candidates)
    )


def is_processon_editor_url(value: str) -> bool:
    """Return whether a ProcessOn URL is a concrete diagram editor URL."""

    try:
        parsed = urlparse(validate_processon_url(value))
    except BrowserRunnerError:
        return False
    return bool(re.fullmatch(r"/diagraming/[^/]+", parsed.path.rstrip("/")))


async def open_source_editor(
    page: Any, entry: dict[str, Any], timeout_ms: int, receipt: BrowserReceipt
) -> tuple[Any, Any | None]:
    """Open one listed document, accepting an in-page editor or a popup.

    ProcessOn uses both behaviours across team-space views. Bind the listener
    to this worker page, then poll for an in-page editor navigation so workers
    never cross-capture each other's transient pages.
    """

    title = str(entry["title"])
    title_locator = await find_title(
        page,
        title,
        timeout_ms,
        occurrence=int(entry.get("_collision_occurrence", 0)),
        expected_matches=int(entry.get("_collision_group_size", 1)),
        document_type=str(entry.get("type", "")),
        owner=(
            ""
            if entry.get("_search_unique_title_type")
            else str(entry.get("owner", ""))
        ),
        remote_updated_at=(
            ""
            if entry.get("_search_unique_title_type")
            else str(entry.get("remote_updated_at", ""))
        ),
        allow_additional_matches="_collision_occurrence" in entry,
        allow_unique_owner_type_update_drift=True,
    )
    popup_task = asyncio.create_task(page.wait_for_event("popup", timeout=timeout_ms))
    try:
        await title_locator.click(timeout=timeout_ms)
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if popup_task.done():
                popup = popup_task.result()
                receipt.scoped_pages_opened += 1
                await popup.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                await popup.wait_for_timeout(900)
                return popup, popup
            if is_processon_editor_url(page.url):
                await page.wait_for_timeout(900)
                return page, None
            await page.wait_for_timeout(100)
        if popup_task.done():
            popup = popup_task.result()
            receipt.scoped_pages_opened += 1
            await popup.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            await popup.wait_for_timeout(900)
            return popup, popup
        raise BatchError(f"title did not open a ProcessOn editor: {title!r}")
    finally:
        if not popup_task.done():
            popup_task.cancel()
            try:
                await popup_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass


async def open_editor_export_menu(
    page: Any, entry: dict[str, Any], timeout_ms: int
) -> tuple[str, Any]:
    """Open the official editor export menu using visible semantic controls."""

    deadline = time.monotonic() + timeout_ms / 1000

    async def wait_for_visible(label: str) -> Any | None:
        while time.monotonic() < deadline:
            locator = await visible_semantic_control(page, label)
            if locator is not None:
                return locator
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            await page.wait_for_timeout(min(100, remaining_ms))
        return None

    async def controls_diagnostic(phase: str) -> str:
        controls: dict[str, bool] = {}
        for label in (EDITOR_FILE_MENU, EDITOR_EXPORT_MENU):
            controls[label] = (await visible_semantic_control(page, label)) is not None
        editor_route = urlparse(str(getattr(page, "url", ""))).path.split("/", 2)[1:2]
        return json.dumps(
            {
                "kind": "editor_export_controls_unavailable",
                "phase": phase,
                "editor_route": editor_route[0] if editor_route else "",
                "document_type": str(entry.get("type", "")),
                "controls": controls,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    if str(entry.get("type", "")).lower() == "mindmap":
        export_menu = await wait_for_visible(EDITOR_EXPORT_MENU)
        if export_menu is None:
            raise BatchError(await controls_diagnostic("mindmap_export_menu"))
        await export_menu.click(timeout=max(1, int((deadline - time.monotonic()) * 1000)))
        return await find_download_menu(
            page,
            entry,
            max(1, int((deadline - time.monotonic()) * 1000)),
        )

    file_menu = await wait_for_visible(EDITOR_FILE_MENU)
    if file_menu is None:
        raise BatchError(await controls_diagnostic("file_menu"))
    await file_menu.click(timeout=max(1, int((deadline - time.monotonic()) * 1000)))
    export_menu = await wait_for_visible(EDITOR_EXPORT_MENU)
    if export_menu is None:
        raise BatchError(await controls_diagnostic("export_menu"))
    await export_menu.click(timeout=max(1, int((deadline - time.monotonic()) * 1000)))
    return await find_download_menu(
        page,
        entry,
        max(1, int((deadline - time.monotonic()) * 1000)),
    )


def source_identity_plan_bound(entry: dict[str, Any]) -> bool:
    """Return whether inventory supplied stable source identity for this entry."""

    return bool(
        str(entry.get("remote_id") or "").strip()
        and str(entry.get("source_url") or "").strip()
    )


def write_semantic_binding_diagnostic(
    progress_path: Path,
    *,
    entry: dict[str, Any],
    browser_result: dict[str, Any],
    inspection: dict[str, Any],
) -> Path:
    """Persist a redacted audit record before blocking an unbound VSDX."""

    root = (
        progress_path.expanduser().resolve(strict=False).parent / "semantic-binding-diagnostics"
    )
    if root.is_symlink():
        raise BatchError(f"semantic diagnostic root must not be a symlink: {root}")
    root.mkdir(parents=True, exist_ok=True)
    artifact_id = str(entry["artifact_id"])
    target = root / f"{artifact_id}.json"
    if target.is_symlink():
        raise BatchError(f"semantic diagnostic target must not be a symlink: {target}")
    payload = {
        "schema_version": 1,
        "kind": "content_structure_verified_source_binding_missing",
        "artifact_id": artifact_id,
        "source_path": str(entry.get("source_path", "")),
        "title": str(entry.get("title", "")),
        "requested_format": str(entry.get("primary_format", "")),
        "source_identity_plan_bound": source_identity_plan_bound(entry),
        "observed_source_url": str(browser_result.get("source_url", "")),
        "observed_remote_id": str(browser_result.get("remote_id", "")),
        "download": {
            "path": str(browser_result["download"]["path"]),
            "suggested_filename": str(
                browser_result["download"].get("suggested_filename", "")
            ),
        },
        "inspection": inspection,
        "created_at": utc_now(),
    }
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    return target


def inspection_requires_source_binding_block(inspection: dict[str, Any]) -> bool:
    return (
        inspection.get("kind") == "visio-vsdx"
        and inspection.get("semantic_status") == "source_binding_missing"
    )


def apply_confirmed_collision_binding(
    inspection: dict[str, Any],
    *,
    browser_result: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Promote only a manual inventory-order binding with an observed unique source id.

    Duplicate placeholder titles such as ``未命名文件`` often do not occur in
    diagram text.  The customer's explicit occurrence confirmation plus the
    dedicated-browser editor URL is the source binding in that narrow case;
    package structure and secret scanning still run first.
    """

    binding = browser_result.get("collision_binding")
    if not isinstance(binding, dict):
        return inspection
    expected = {
        "confirmation_method": "inventory_order",
        "occurrence": int(entry.get("_collision_occurrence", -1)),
        "group_size": int(entry.get("_collision_group_size", 0)),
    }
    plan_group_size = int(
        entry.get("_collision_plan_group_size", expected["group_size"])
    )
    if binding != expected or plan_group_size < 2:
        raise BatchError("browser collision binding differs from the confirmed plan order")
    source_url = str(browser_result.get("source_url") or "")
    remote_id = str(browser_result.get("remote_id") or "")
    if not source_url or not remote_id or verify_source_identity(entry, source_url) != remote_id:
        raise BatchError("confirmed collision result has no verified ProcessOn source identity")
    if (
        inspection.get("kind") == "visio-vsdx"
        and inspection.get("semantic_status") == "source_binding_missing"
    ):
        promoted = dict(inspection)
        promoted["content_semantic_status"] = "title_not_present"
        promoted["semantic_status"] = "matched"
        promoted["semantic_match_method"] = (
            "confirmed_inventory_order_and_observed_remote_id"
        )
        promoted["collision_occurrence"] = expected["occurrence"]
        promoted["collision_group_size"] = expected["group_size"]
        return promoted
    return inspection


def apply_observed_source_binding(
    inspection: dict[str, Any],
    *,
    browser_result: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Promote a structurally valid export only with replayable source identity.

    A duplicate-title entry still requires the explicit collision confirmation
    handled above.  A plan entry whose inventory collision check was clean may
    instead use the exact row selection plus the observed, unique ProcessOn
    remote id.  Cross-artifact uniqueness is checked before recovery batches
    call the finalizer.
    """

    promoted = apply_confirmed_collision_binding(
        inspection, browser_result=browser_result, entry=entry
    )
    if not inspection_requires_source_binding_block(promoted):
        return promoted
    if entry.get("collision_risk") != "none_detected":
        return promoted
    source_url = str(browser_result.get("source_url") or "")
    remote_id = str(browser_result.get("remote_id") or "")
    if not source_url or not remote_id or verify_source_identity(entry, source_url) != remote_id:
        raise BatchError("unique inventory result has no verified ProcessOn source identity")
    result = dict(promoted)
    if browser_result.get("empty_source_confirmed"):
        if result.get("kind") != "visio-vsdx" or int(result.get("text_count", -1)) != 0:
            raise BatchError("live empty-canvas signal disagrees with the downloaded VSDX")
        result["content_semantic_status"] = "empty_source_confirmed"
        result["semantic_match_method"] = (
            "live_editor_empty_canvas_and_observed_remote_id"
        )
    else:
        result["content_semantic_status"] = "title_not_present"
        result["semantic_match_method"] = "unique_inventory_row_and_observed_remote_id"
    result["semantic_status"] = "matched"
    return result


def block_structurally_valid_unbound_vsdx(
    browser_result: dict[str, Any],
    entry: dict[str, Any],
    inspection: dict[str, Any],
    *,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Persist private evidence without promoting the file to final output."""

    source = Path(browser_result["download"]["path"])
    if source.is_symlink() or not source.is_file():
        raise BatchError("semantic-block source must be a regular staged download")
    diagnostic = write_semantic_binding_diagnostic(
        args.progress,
        entry=entry,
        browser_result=browser_result,
        inspection=inspection,
    )
    recorded = run_json(
        [
            sys.executable,
            str(ARCHIVE_STATE),
            "mark",
            "--plan",
            str(args.plan),
            "--progress",
            str(args.progress),
            "--artifact-id",
            str(entry["artifact_id"]),
            "--outcome",
            "blocked",
            "--reason",
            "content_structure_verified_source_binding_missing",
            "--evidence-file",
            str(diagnostic),
            "--evidence-file",
            str(source),
        ]
    )
    return {
        "artifact_id": entry["artifact_id"],
        "status": "blocked",
        "reason": "content_structure_verified_source_binding_missing",
        "download": str(source),
        "diagnostic": str(diagnostic),
        "inspection": inspection,
        "progress_counts": recorded.get("counts", {}),
    }


async def wait_for_source_title(page: Any, expected: str, timeout_ms: int) -> str:
    """Wait for ProcessOn's asynchronous editor document title to settle."""

    deadline = time.monotonic() + timeout_ms / 1000
    observed = ""
    while time.monotonic() < deadline:
        observed = await page.title()
        if source_title_matches(expected, observed):
            return observed
        await page.wait_for_timeout(150)
    raise BatchError(f"source editor title mismatch: expected {expected!r}, got {observed!r}")


async def download_one(
    page: Any,
    entry: dict[str, Any],
    *,
    download_dir: Path,
    progress_path: Path,
    timeout_ms: int,
    receipt: BrowserReceipt,
) -> dict[str, Any]:
    artifact_id = str(entry["artifact_id"])
    title = str(entry["title"])
    popup = None
    result: dict[str, Any] = {
        "artifact_id": artifact_id,
        "source_path": entry["source_path"],
        "title": title,
        "requested_format": entry["primary_format"],
        "source_lookup_method": str(
            entry.get("_source_lookup_method", "planned_directory")
        ),
    }
    try:
        if "_collision_occurrence" in entry:
            result["collision_binding"] = {
                "confirmation_method": str(entry["_collision_confirmation_method"]),
                "occurrence": int(entry["_collision_occurrence"]),
                "group_size": int(entry["_collision_group_size"]),
            }
        direct_source_url = str(entry.get("_direct_source_url") or "").strip()
        if direct_source_url:
            if not source_url_matches_document_type(entry, direct_source_url):
                raise BatchError("direct retry source URL route differs from the plan type")
            await page.goto(
                validate_processon_url(direct_source_url),
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            await page.wait_for_timeout(900)
            source_page = page
        else:
            source_page, popup = await open_source_editor(page, entry, timeout_ms, receipt)
        source_url = validate_processon_url(source_page.url)
        source_title = await wait_for_source_title(source_page, title, timeout_ms)
        remote_id = verify_source_identity(entry, source_url)
        result["source_url"] = source_url
        result["source_title"] = source_title
        result["remote_id"] = remote_id
        if entry.get("_require_live_empty_canvas"):
            await wait_for_live_empty_canvas(source_page, timeout_ms)
            result["empty_source_confirmed"] = True
            result["empty_source_editor_signal"] = "shape_count_zero"

        menu_label, menu = await open_editor_export_menu(source_page, entry, timeout_ms)
        result["download_menu"] = menu_label
        actual_format, fallback_used, fallback_reason = download_menu_format(entry, menu_label)
        result["actual_format"] = actual_format
        result["fallback_used"] = fallback_used
        result["fallback_reason"] = fallback_reason
        async with source_page.expect_download(timeout=max(timeout_ms, 60_000)) as download_info:
            await menu.click(timeout=timeout_ms)
        download = await download_info.value
        suggested = download.suggested_filename
        expected_suffix = f".{actual_format}"
        if Path(suggested).suffix.lower() != expected_suffix:
            raise BatchError(
                f"download suffix mismatch for {title!r}: expected {expected_suffix}, got {suggested!r}"
            )
        if not provider_suggested_filename_matches(title, suggested):
            raise BatchError(
                f"download title mismatch for {title!r}: suggested filename is {suggested!r}"
            )
        destination = safe_download_path(download_dir, artifact_id, suggested)
        await download.save_as(destination)
        size = destination.stat().st_size
        if size <= 0:
            raise BatchError(f"downloaded file is empty: {destination}")
        item = {
            "artifact_id": artifact_id,
            "path": str(destination),
            "bytes": size,
            "suggested_filename": suggested,
            "download_menu": menu_label,
        }
        receipt.downloaded_files.append(item)
        result["download"] = item
        result["staging_receipt"] = str(write_staging_receipt(progress_path, result))
        result["ok"] = True
        return result
    except Exception as exc:
        result.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return result
    finally:
        if popup is not None and not popup.is_closed():
            if await async_safe_close_page(popup):
                receipt.scoped_pages_closed += 1


async def worker_loop(
    worker_id: int,
    context: Any,
    queue: asyncio.Queue[tuple[str, list[dict[str, Any]]] | None],
    *,
    plan: dict[str, Any],
    team_url: str,
    download_dir: Path,
    progress_path: Path,
    settle_ms: int,
    timeout_ms: int,
    receipt: BrowserReceipt,
) -> list[dict[str, Any]]:
    page = await context.new_page()
    receipt.worker_pages_opened += 1
    results: list[dict[str, Any]] = []
    try:
        await asyncio.sleep(worker_id * 1.5)
        while True:
            job = await queue.get()
            if job is None:
                queue.task_done()
                break
            directory, entries = job
            for entry in entries:
                try:
                    if entry.get("_direct_source_url"):
                        results.append(
                            await download_one(
                                page,
                                entry,
                                download_dir=download_dir,
                                progress_path=progress_path,
                                timeout_ms=timeout_ms,
                                receipt=receipt,
                            )
                        )
                        continue
                    # A title can navigate this same worker into the official
                    # editor. Rebuild the approved directory view before each
                    # artifact instead of relying on history/back semantics.
                    entry_for_download = entry
                    try:
                        await navigate_directory(
                            page,
                            team_url=team_url,
                            root_path=str(plan["root_path"]),
                            source_directory=directory,
                            settle_ms=settle_ms,
                            timeout_ms=timeout_ms,
                        )
                    except Exception as path_error:
                        try:
                            lookup_method = await prepare_exact_team_search(
                                page,
                                team_url=team_url,
                                entry=entry,
                                settle_ms=settle_ms,
                                timeout_ms=timeout_ms,
                            )
                        except Exception as search_error:
                            raise BatchError(
                                "planned directory and exact team search both failed: "
                                f"{type(path_error).__name__}: {path_error}; "
                                f"{type(search_error).__name__}: {search_error}"
                            ) from search_error
                        entry_for_download = dict(entry)
                        entry_for_download["_source_lookup_method"] = lookup_method
                        entry_for_download["_search_unique_title_type"] = (
                            lookup_method == "exact_team_search_unique_title_and_type"
                        )
                    download_result = await download_one(
                        page,
                        entry_for_download,
                        download_dir=download_dir,
                        progress_path=progress_path,
                        timeout_ms=timeout_ms,
                        receipt=receipt,
                    )
                    title_lookup_errors = (
                        "title is not visible",
                        "duplicate-title row count exceeds confirmation",
                    )
                    if (
                        not download_result.get("ok")
                        and entry_for_download is entry
                        and any(
                            marker in str(download_result.get("error", ""))
                            for marker in title_lookup_errors
                        )
                    ):
                        lookup_method = await prepare_exact_team_search(
                            page,
                            team_url=team_url,
                            entry=entry,
                            settle_ms=settle_ms,
                            timeout_ms=timeout_ms,
                        )
                        searched_entry = dict(entry)
                        searched_entry["_source_lookup_method"] = lookup_method
                        searched_entry["_search_unique_title_type"] = (
                            lookup_method == "exact_team_search_unique_title_and_type"
                        )
                        download_result = await download_one(
                            page,
                            searched_entry,
                            download_dir=download_dir,
                            progress_path=progress_path,
                            timeout_ms=timeout_ms,
                            receipt=receipt,
                        )
                    results.append(download_result)
                except Exception as exc:
                    results.append(
                        {
                            "ok": False,
                            "artifact_id": entry["artifact_id"],
                            "source_path": entry["source_path"],
                            "title": entry["title"],
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
            queue.task_done()
        return results
    finally:
        if not page.is_closed():
            await page.close(run_before_unload=False)
            receipt.worker_pages_closed += 1


async def browser_download_batch(
    entries: list[dict[str, Any]],
    *,
    plan: dict[str, Any],
    team_url: str,
    profile_dir: Path,
    download_dir: Path,
    progress_path: Path,
    workers: int,
    settle_ms: int,
    timeout_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise BatchError("missing Playwright; install playwright and Chromium") from exc

    profile = ensure_dedicated_profile(profile_dir)
    receipt = BrowserReceipt()
    results: list[dict[str, Any]] = []
    async with async_playwright() as playwright:
        kwargs = {
            "headless": True,
            "accept_downloads": True,
            "viewport": {"width": 1440, "height": 1000},
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        try:
            context = await playwright.chromium.launch_persistent_context(
                str(profile), channel="chrome", **kwargs
            )
        except Exception:
            context = await playwright.chromium.launch_persistent_context(str(profile), **kwargs)
        try:
            initial = list(context.pages)
            receipt.pages_seen_at_start = len(initial)
            for stale in initial:
                if await async_safe_close_page(stale):
                    receipt.stale_pages_closed += 1
            queue: asyncio.Queue[tuple[str, list[dict[str, Any]]] | None] = asyncio.Queue()
            for job in build_jobs(entries, workers):
                queue.put_nowait(job)
            actual_workers = min(workers, max(1, queue.qsize()))
            for _ in range(actual_workers):
                queue.put_nowait(None)
            tasks = [
                asyncio.create_task(
                    worker_loop(
                        worker_id,
                        context,
                        queue,
                        plan=plan,
                        team_url=team_url,
                        download_dir=download_dir,
                        progress_path=progress_path,
                        settle_ms=settle_ms,
                        timeout_ms=timeout_ms,
                        receipt=receipt,
                    )
                )
                for worker_id in range(actual_workers)
            ]
            await queue.join()
            for worker_results in await asyncio.gather(*tasks):
                results.extend(worker_results)
        finally:
            for page in list(context.pages):
                if await async_safe_close_page(page):
                    receipt.pages_closed_at_exit += 1
            await context.close()
    return results, receipt.as_dict()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def source_title_matches(expected: str, observed: str) -> bool:
    return observed in {expected, f"{expected}-ProcessOn"}


def provider_safe_filename_stem(title: str) -> str:
    """Mirror ProcessOn's observed filename sanitization, and nothing broader."""

    # ProcessOn also replaces the pipe character when it appears in titles.
    # Keep this allow-list narrow: this is only a suggested-filename binding
    # check, not a general fuzzy title comparison.
    return title.replace("/", "_").replace("\\", "_").replace("|", "_")


def provider_suggested_filename_matches(title: str, suggested_filename: str) -> bool:
    """Match only ProcessOn's observed path sanitization and URL-style spacing."""

    suggested_stem = unquote_plus(Path(suggested_filename).stem)
    return suggested_stem in {title, provider_safe_filename_stem(title)}


def normalized_processon_source_url(value: str) -> str:
    validated = validate_processon_url(value)
    parsed = urlparse(validated)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def verify_source_identity(entry: dict[str, Any], observed_url: str) -> str:
    normalized_observed = normalized_processon_source_url(observed_url)
    parsed = urlparse(normalized_observed)
    remote_id = parsed.path.rstrip("/").split("/")[-1]
    if not remote_id:
        raise BatchError(f"source popup URL has no remote id: {observed_url}")
    expected_remote_id = str(entry.get("remote_id") or "").strip()
    if expected_remote_id and expected_remote_id != remote_id:
        raise BatchError(
            f"source popup remote id mismatch: expected {expected_remote_id!r}, got {remote_id!r}"
        )
    expected_url = str(entry.get("source_url") or "").strip()
    if expected_url:
        normalized_expected = normalized_processon_source_url(expected_url)
        if normalized_expected != normalized_observed:
            raise BatchError(
                f"source popup URL mismatch: expected {normalized_expected!r}, got {normalized_observed!r}"
            )
    return remote_id


def source_url_matches_document_type(entry: dict[str, Any], source_url: str) -> bool:
    """Require the provider route to agree with the audited plan type."""

    parsed = urlparse(validate_processon_url(source_url))
    document_type = str(entry.get("type") or "").strip().lower()
    if document_type == "flowchart":
        return bool(re.fullmatch(r"/diagraming/[^/]+", parsed.path.rstrip("/")))
    if document_type == "mindmap":
        return bool(re.fullmatch(r"/mindmap/[^/]+", parsed.path.rstrip("/")))
    return False


def retry_failed_source_binding_from_evidence(
    entry: dict[str, Any],
    failed_record: dict[str, Any],
    *,
    progress_path: Path,
) -> dict[str, str] | None:
    """Recover one exact source URL from an audited failed batch receipt.

    This is deliberately narrower than a general URL override.  The evidence
    must already be copied under the run's private evidence root, match its
    recorded size and SHA-256, and contain exactly one source URL for the same
    artifact/title/path.  Ordinary pending work and blocked retries never use
    this route.
    """

    artifact_id = str(entry.get("artifact_id") or "")
    if str(failed_record.get("artifact_id") or "") != artifact_id:
        raise BatchError("failed evidence record differs from the selected artifact")
    evidence_root = (progress_path.expanduser().resolve(strict=False).parent / "evidence").resolve(
        strict=False
    )
    candidates: dict[str, dict[str, str]] = {}
    for evidence in failed_record.get("evidence_files") or []:
        if not isinstance(evidence, dict):
            raise BatchError("failed evidence entry must be an object")
        archived_path = Path(str(evidence.get("archived_path") or "")).expanduser().resolve(
            strict=False
        )
        try:
            archived_path.relative_to(evidence_root)
        except ValueError as exc:
            raise BatchError("failed evidence file is outside the run evidence root") from exc
        if archived_path.is_symlink() or not archived_path.is_file():
            raise BatchError(f"failed evidence file is not a regular file: {archived_path}")
        expected_bytes = int(evidence.get("bytes") or 0)
        expected_sha256 = str(evidence.get("sha256") or "")
        if expected_bytes <= 0 or archived_path.stat().st_size != expected_bytes:
            raise BatchError("failed evidence file size differs from progress")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or sha256(archived_path) != expected_sha256:
            raise BatchError("failed evidence file SHA-256 differs from progress")
        try:
            receipt = load_json(archived_path)
        except Exception:
            continue
        for bucket in ("completed", "blocked", "pending"):
            for item in receipt.get(bucket) or []:
                if not isinstance(item, dict) or str(item.get("artifact_id") or "") != artifact_id:
                    continue
                if str(item.get("title") or "") != str(entry.get("title") or ""):
                    raise BatchError("failed receipt title differs from the current plan")
                receipt_path = str(item.get("source_path") or "")
                if receipt_path and receipt_path != str(entry.get("source_path") or ""):
                    raise BatchError("failed receipt source path differs from the current plan")
                source_url = str(item.get("source_url") or "").strip()
                if not source_url:
                    continue
                if not source_url_matches_document_type(entry, source_url):
                    raise BatchError("failed receipt source URL route differs from the plan type")
                normalized = normalized_processon_source_url(source_url)
                remote_id = urlparse(normalized).path.rstrip("/").split("/")[-1]
                if not remote_id:
                    raise BatchError("failed receipt source URL has no remote id")
                candidates[normalized] = {
                    "source_url": normalized,
                    "remote_id": remote_id,
                    "evidence_path": str(archived_path),
                    "evidence_sha256": expected_sha256,
                }
    if len(candidates) > 1:
        raise BatchError(
            f"failed evidence contains multiple ProcessOn source URLs: {artifact_id}"
        )
    return next(iter(candidates.values()), None)


def bind_retry_failed_source_evidence(
    selected: list[dict[str, Any]],
    progress: dict[str, Any],
    *,
    progress_path: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Decorate only failed retries that have one replayable source receipt."""

    failed_by_id = {
        str(item.get("artifact_id") or ""): item
        for item in progress.get("failed", [])
        if isinstance(item, dict) and item.get("artifact_id")
    }
    bound: list[dict[str, Any]] = []
    bound_ids: list[str] = []
    for original in selected:
        entry = dict(original)
        artifact_id = str(entry.get("artifact_id") or "")
        failed_record = failed_by_id.get(artifact_id)
        if failed_record is None:
            bound.append(entry)
            continue
        recovered = retry_failed_source_binding_from_evidence(
            entry,
            failed_record,
            progress_path=progress_path,
        )
        if recovered is None:
            bound.append(entry)
            continue
        entry["source_url"] = recovered["source_url"]
        entry["remote_id"] = recovered["remote_id"]
        entry["_direct_source_url"] = recovered["source_url"]
        entry["_source_lookup_method"] = "audited_failed_receipt_source_url"
        entry["_retry_source_evidence_path"] = recovered["evidence_path"]
        entry["_retry_source_evidence_sha256"] = recovered["evidence_sha256"]
        bound.append(entry)
        bound_ids.append(artifact_id)
    return bound, bound_ids


def retry_blocked_empty_source_binding_from_evidence(
    entry: dict[str, Any],
    blocked_record: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, str]:
    """Recover one exact empty-canvas URL from audited quarantine metadata."""

    artifact_id = str(entry.get("artifact_id") or "")
    if str(blocked_record.get("artifact_id") or "") != artifact_id:
        raise BatchError("blocked evidence record differs from the selected artifact")
    reason = str(blocked_record.get("reason") or "").lower()
    if not any(marker in reason for marker in ("0 shapes", "empty canvas")):
        raise BatchError("blocked artifact was not classified as an empty source")
    revalidation = blocked_record.get("revalidation")
    if not isinstance(revalidation, dict):
        raise BatchError("empty-source block has no revalidation evidence")
    quarantine_root = (output_root / "_quarantine").expanduser().resolve(strict=False)
    metadata_candidates: list[Path] = []
    for evidence in revalidation.get("quarantine_files") or []:
        if not isinstance(evidence, dict):
            raise BatchError("empty-source quarantine evidence must be an object")
        path = Path(str(evidence.get("quarantine_path") or "")).expanduser().resolve(
            strict=False
        )
        if path.name != "metadata.yml":
            continue
        try:
            path.relative_to(quarantine_root)
        except ValueError as exc:
            raise BatchError("empty-source metadata is outside the archive quarantine") from exc
        if path.is_symlink() or not path.is_file():
            raise BatchError(f"empty-source metadata is not a regular file: {path}")
        expected_bytes = int(evidence.get("bytes") or 0)
        expected_sha256 = str(evidence.get("sha256") or "")
        if expected_bytes <= 0 or path.stat().st_size != expected_bytes:
            raise BatchError("empty-source metadata size differs from progress")
        if (
            not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or sha256(path) != expected_sha256
        ):
            raise BatchError("empty-source metadata SHA-256 differs from progress")
        metadata_candidates.append(path)
    if len(metadata_candidates) != 1:
        raise BatchError("empty-source block must have exactly one audited metadata file")
    metadata = read_top_level_metadata(metadata_candidates[0])
    if metadata.get("artifact_id") != artifact_id:
        raise BatchError("empty-source metadata artifact_id differs from the plan")
    if metadata.get("source_path") != str(entry.get("source_path") or ""):
        raise BatchError("empty-source metadata source path differs from the plan")
    source_url = str(metadata.get("source_url") or "")
    remote_id = str(metadata.get("remote_id") or "")
    if not source_url_matches_document_type(entry, source_url):
        raise BatchError("empty-source metadata URL route differs from the plan type")
    observed_remote_id = verify_source_identity(entry, source_url)
    if not remote_id or observed_remote_id != remote_id:
        raise BatchError("empty-source metadata remote id differs from its source URL")
    return {
        "source_url": normalized_processon_source_url(source_url),
        "remote_id": remote_id,
        "evidence_path": str(metadata_candidates[0]),
    }


def bind_retry_blocked_empty_source_evidence(
    selected: list[dict[str, Any]],
    progress: dict[str, Any],
    *,
    output_root: Path,
    allowed_artifact_ids: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Decorate approved empty-source retries with exact audited URLs."""

    blocked_by_id = {
        str(item.get("artifact_id") or ""): item
        for item in progress.get("blocked", [])
        if isinstance(item, dict) and item.get("artifact_id")
    }
    selected_ids = {str(entry.get("artifact_id") or "") for entry in selected}
    if not allowed_artifact_ids <= selected_ids:
        missing = sorted(allowed_artifact_ids - selected_ids)[0]
        raise BatchError(f"empty-source approval is not in the blocked retry: {missing}")
    bound: list[dict[str, Any]] = []
    bound_ids: list[str] = []
    for original in selected:
        entry = dict(original)
        artifact_id = str(entry.get("artifact_id") or "")
        if artifact_id not in allowed_artifact_ids:
            bound.append(entry)
            continue
        blocked_record = blocked_by_id.get(artifact_id)
        if blocked_record is None:
            raise BatchError(f"empty-source approval is not currently blocked: {artifact_id}")
        recovered = retry_blocked_empty_source_binding_from_evidence(
            entry,
            blocked_record,
            output_root=output_root,
        )
        entry["source_url"] = recovered["source_url"]
        entry["remote_id"] = recovered["remote_id"]
        entry["_direct_source_url"] = recovered["source_url"]
        entry["_source_lookup_method"] = "audited_empty_source_metadata_url"
        entry["_require_live_empty_canvas"] = True
        entry["_empty_source_evidence_path"] = recovered["evidence_path"]
        bound.append(entry)
        bound_ids.append(artifact_id)
    return bound, bound_ids


def validated_security_block_source(
    entry: dict[str, Any],
    blocked_record: dict[str, Any],
    *,
    args: argparse.Namespace,
) -> dict[str, str]:
    """Resolve one secret-bearing VSDX from audited quarantine or run evidence."""

    artifact_id = str(entry.get("artifact_id") or "")
    if str(blocked_record.get("artifact_id") or "") != artifact_id:
        raise BatchError("security block differs from the selected artifact")
    reason = str(blocked_record.get("reason") or "").lower()
    if not any(marker in reason for marker in ("credential", "presigned", "security")):
        raise BatchError("blocked artifact was not classified for security review")
    candidates: dict[str, dict[str, str]] = {}
    quarantine_root = (args.output_root / "_quarantine").expanduser().resolve(
        strict=False
    )
    revalidation = blocked_record.get("revalidation")
    if isinstance(revalidation, dict):
        metadata_path: Path | None = None
        source_path: Path | None = None
        for evidence in revalidation.get("quarantine_files") or []:
            if not isinstance(evidence, dict):
                raise BatchError("security quarantine evidence must be an object")
            path = Path(str(evidence.get("quarantine_path") or "")).expanduser().resolve(
                strict=False
            )
            try:
                path.relative_to(quarantine_root)
            except ValueError as exc:
                raise BatchError("security evidence is outside the archive quarantine") from exc
            if path.is_symlink() or not path.is_file():
                raise BatchError(f"security evidence is not a regular file: {path}")
            expected_bytes = int(evidence.get("bytes") or 0)
            expected_sha256 = str(evidence.get("sha256") or "")
            if expected_bytes <= 0 or path.stat().st_size != expected_bytes:
                raise BatchError("security evidence size differs from progress")
            if (
                not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
                or sha256(path) != expected_sha256
            ):
                raise BatchError("security evidence SHA-256 differs from progress")
            if path.name == "metadata.yml":
                metadata_path = path
            elif path.suffix.lower() == ".vsdx":
                source_path = path
        if metadata_path and source_path:
            metadata = read_top_level_metadata(metadata_path)
            if metadata.get("artifact_id") != artifact_id:
                raise BatchError("security metadata artifact_id differs from the plan")
            if metadata.get("source_path") != str(entry.get("source_path") or ""):
                raise BatchError("security metadata source path differs from the plan")
            source_url = str(metadata.get("source_url") or "")
            remote_id = str(metadata.get("remote_id") or "")
            if not source_url_matches_document_type(entry, source_url):
                raise BatchError("security metadata URL route differs from the plan type")
            if verify_source_identity(entry, source_url) != remote_id:
                raise BatchError("security metadata remote id differs from its source URL")
            candidates[normalized_processon_source_url(source_url)] = {
                "source": str(source_path),
                "source_url": normalized_processon_source_url(source_url),
                "remote_id": remote_id,
                "evidence_path": str(metadata_path),
            }

    evidence_root = (args.progress.parent / "evidence").resolve(strict=False)
    for evidence in blocked_record.get("evidence_files") or []:
        if not isinstance(evidence, dict):
            raise BatchError("security run evidence must be an object")
        receipt_path = Path(str(evidence.get("archived_path") or "")).expanduser().resolve(
            strict=False
        )
        try:
            receipt_path.relative_to(evidence_root)
        except ValueError as exc:
            raise BatchError("security receipt is outside the run evidence root") from exc
        if receipt_path.suffix.lower() != ".json":
            continue
        if receipt_path.is_symlink() or not receipt_path.is_file():
            raise BatchError(f"security receipt is not a regular file: {receipt_path}")
        expected_bytes = int(evidence.get("bytes") or 0)
        expected_sha256 = str(evidence.get("sha256") or "")
        if expected_bytes <= 0 or receipt_path.stat().st_size != expected_bytes:
            raise BatchError("security receipt size differs from progress")
        if (
            not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or sha256(receipt_path) != expected_sha256
        ):
            raise BatchError("security receipt SHA-256 differs from progress")
        receipt = load_json(receipt_path)
        for bucket in ("blocked", "pending", "completed"):
            for item in receipt.get(bucket) or []:
                if not isinstance(item, dict) or str(item.get("artifact_id") or "") != artifact_id:
                    continue
                if str(item.get("source_path") or "") != str(entry.get("source_path") or ""):
                    raise BatchError("security receipt source path differs from the plan")
                source_url = str(item.get("source_url") or "")
                remote_id = str(item.get("remote_id") or "")
                download = item.get("download")
                if not isinstance(download, dict):
                    continue
                source = Path(str(download.get("path") or "")).expanduser().resolve(
                    strict=False
                )
                expected_parent = (args.download_dir / artifact_id).resolve(strict=False)
                if source.parent != expected_parent or source.is_symlink() or not source.is_file():
                    raise BatchError("security receipt source is not isolated staging")
                if int(download.get("bytes") or 0) != source.stat().st_size:
                    raise BatchError("security receipt source size differs from staging")
                if not source_url_matches_document_type(entry, source_url):
                    raise BatchError("security receipt URL route differs from the plan type")
                if verify_source_identity(entry, source_url) != remote_id:
                    raise BatchError("security receipt remote id differs from its source URL")
                candidates[normalized_processon_source_url(source_url)] = {
                    "source": str(source),
                    "source_url": normalized_processon_source_url(source_url),
                    "remote_id": remote_id,
                    "evidence_path": str(receipt_path),
                }
    if len(candidates) != 1:
        raise BatchError("security block must resolve to exactly one audited source identity")
    return next(iter(candidates.values()))


def sanitize_security_blocks(
    plan: dict[str, Any],
    progress: dict[str, Any],
    *,
    args: argparse.Namespace,
    artifact_ids: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Archive safe VSDX derivatives while retaining originals in quarantine."""

    plan_by_id = {
        str(entry.get("artifact_id") or ""): entry
        for entry in plan.get("entries", [])
        if entry.get("artifact_id")
    }
    blocked_by_id = {
        str(item.get("artifact_id") or ""): item
        for item in progress.get("blocked", [])
        if isinstance(item, dict) and item.get("artifact_id")
    }
    completed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for artifact_id in artifact_ids:
        entry = plan_by_id.get(artifact_id)
        blocked_record = blocked_by_id.get(artifact_id)
        if entry is None or blocked_record is None:
            errors.append({"artifact_id": artifact_id, "error": "not_currently_blocked"})
            continue
        try:
            evidence = validated_security_block_source(
                entry,
                blocked_record,
                args=args,
            )
            source = Path(evidence["source"])
            sanitized_path = safe_download_path(
                args.download_dir,
                artifact_id,
                f"{entry['title']}--sanitized.vsdx",
            )
            sanitization = sanitize_vsdx_sensitive_text(source, sanitized_path)
            result = {
                "artifact_id": artifact_id,
                "source_path": str(entry["source_path"]),
                "title": str(entry["title"]),
                "requested_format": str(entry["primary_format"]),
                "actual_format": "vsdx",
                "source_lookup_method": "audited_security_block_evidence",
                "source_url": evidence["source_url"],
                "source_title": str(entry["title"]),
                "remote_id": evidence["remote_id"],
                "download_menu": "security_redacted_derivative",
                "fallback_used": False,
                "fallback_reason": "",
                "sanitization": sanitization,
                "download": {
                    "artifact_id": artifact_id,
                    "path": str(sanitized_path),
                    "bytes": sanitized_path.stat().st_size,
                    "suggested_filename": sanitized_path.name,
                    "download_menu": "security_redacted_derivative",
                },
                "ok": True,
            }
            completed.append(finalize_result(result, entry, args=args))
        except Exception as exc:
            errors.append(
                {"artifact_id": artifact_id, "error": f"{type(exc).__name__}: {exc}"}
            )
    return completed, errors


def visible_editor_confirms_empty_canvas(text: str) -> bool:
    return bool(re.search(r"图形\s*[：:]\s*0(?:\D|$)", text))


async def wait_for_live_empty_canvas(page: Any, timeout_ms: int) -> None:
    """Wait for the editor's authoritative rendered shape counter."""

    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        editor_text = await page.locator("body").inner_text(timeout=timeout_ms)
        match = re.search(r"图形\s*[：:]\s*(\d+)(?:\D|$)", editor_text)
        if match:
            if int(match.group(1)) == 0:
                return
            raise BatchError("live ProcessOn editor contains one or more shapes")
        await page.wait_for_timeout(150)
    raise BatchError("live ProcessOn editor did not expose a shape count before timeout")


def title_signals(title: str) -> list[str]:
    candidates: list[str] = []
    candidates.extend(re.findall(r"[A-Za-z][A-Za-z0-9_.-]{1,}", title))
    cleaned = title
    for word in COMMON_TITLE_WORDS:
        cleaned = cleaned.replace(word, "")
    # Treat dotted release numbers as separators. Otherwise a title such as
    # "磐石4.0短信系统" produces the unusable signals "磐石4" and "0短信",
    # while the diagram itself naturally contains "磐石" and "短信".
    cleaned = re.sub(r"\d+(?:\.\d+)+", " ", cleaned)
    for piece in re.split(r"[\s《》()（）\[\]【】,，、:：/&+_\-.]+", cleaned):
        piece = piece.strip()
        if len(piece) >= 2 and not piece.isdigit():
            candidates.append(piece)
    result: list[str] = []
    for candidate in candidates:
        value = normalized_text(candidate)
        if value and value not in result:
            result.append(value)
    return result


def matched_chinese_bigram_pair(title: str, combined: str) -> list[str]:
    """Find two non-overlapping Chinese title bigrams in diagram text.

    The caller verifies the ProcessOn remote id and source URL before this
    semantic fallback runs. A single generic two-character hit is deliberately
    insufficient.
    """

    cleaned = title
    for word in COMMON_TITLE_WORDS:
        cleaned = cleaned.replace(word, "")
    candidates: list[tuple[str, int, int]] = []
    for run_match in re.finditer(r"[\u3400-\u9fff]{4,}", cleaned):
        run = run_match.group(0)
        for offset in range(len(run) - 1):
            signal = normalized_text(run[offset : offset + 2])
            start = run_match.start() + offset
            if signal in combined:
                candidates.append((signal, start, start + 2))
    for index, first in enumerate(candidates):
        for second in candidates[index + 1 :]:
            if first[2] <= second[1] or second[2] <= first[1]:
                return [first[0], second[0]]
    return []


def sensitive_text_findings(texts: list[str]) -> list[dict[str, Any]]:
    """Count potential plaintext credential assignments without returning values."""

    combined = "\n".join(texts)
    findings: list[dict[str, Any]] = []
    for finding_type, pattern in SENSITIVE_TEXT_PATTERNS:
        count = sum(1 for _ in pattern.finditer(combined))
        if count:
            findings.append({"type": finding_type, "count": count})
    return findings


def redact_sensitive_text(text: str) -> tuple[str, dict[str, int]]:
    """Redact supported secret values without returning or logging them."""

    result = text
    counts: dict[str, int] = {}
    for finding_type, pattern in SENSITIVE_REDACTION_PATTERNS:
        if finding_type == "aws_presigned_url_parameter":
            replacement = lambda match: (  # noqa: E731 - local fixed replacer
                f"{match.group('separator')}{match.group('key')}-REDACTED"
            )
        else:
            replacement = lambda match: f"{match.group('key')} [REDACTED]"  # noqa: E731
        result, count = pattern.subn(replacement, result)
        if count:
            counts[finding_type] = count
    return result, counts


def sanitize_vsdx_sensitive_text(source: Path, destination: Path) -> dict[str, Any]:
    """Create a same-format derivative with only detected secret values redacted."""

    if source.is_symlink() or not source.is_file():
        raise BatchError(f"security redaction source is not a regular file: {source}")
    if destination.exists() or destination.is_symlink():
        raise BatchError(f"security redaction destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    counts: dict[str, int] = {}
    try:
        with zipfile.ZipFile(source) as archive, zipfile.ZipFile(temporary, "w") as output:
            names = validate_zip_archive(archive)
            if "visio/document.xml" not in names:
                raise BatchError("security redaction source is missing visio/document.xml")
            page_parts = {
                name for name in names if re.fullmatch(r"visio/pages/page\d+\.xml", name)
            }
            if not page_parts:
                raise BatchError("security redaction source has no page XML")
            for info in archive.infolist():
                data = archive.read(info.filename)
                if info.filename in page_parts:
                    root = ElementTree.fromstring(data)
                    changed = False
                    for element in root.iter():
                        if element.tag.rsplit("}", 1)[-1] != "Text":
                            continue
                        combined = "".join(element.itertext())
                        redacted, element_counts = redact_sensitive_text(combined)
                        if not element_counts:
                            continue
                        for finding_type, count in element_counts.items():
                            counts[finding_type] = counts.get(finding_type, 0) + count
                        for child in list(element):
                            element.remove(child)
                        element.text = redacted
                        changed = True
                    if changed:
                        data = ElementTree.tostring(
                            root, encoding="utf-8", xml_declaration=True
                        )
                output.writestr(info, data)
        if not counts:
            raise BatchError("security redaction found no supported sensitive values")
        structure, _ = inspect_vsdx_structure(temporary)
        os.replace(temporary, destination)
        return {
            "status": "sanitized_derivative",
            "source_sha256": sha256(source),
            "destination_sha256": sha256(destination),
            "bytes": destination.stat().st_size,
            "redaction_counts": counts,
            "inspection": structure,
        }
    finally:
        if temporary.exists():
            temporary.unlink()


def validate_zip_archive(archive: zipfile.ZipFile) -> list[str]:
    infos = archive.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise BatchError(f"ZIP contains too many entries: {len(infos)}")
    total = 0
    names: list[str] = []
    for info in infos:
        raw_name = info.filename
        normalized_name = raw_name.replace("\\", "/")
        pure = PurePosixPath(normalized_name)
        if (
            not raw_name
            or "\\" in raw_name
            or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or (pure.parts and re.fullmatch(r"[A-Za-z]:", pure.parts[0]))
        ):
            raise BatchError(f"ZIP contains an unsafe member path: {raw_name!r}")
        if info.file_size > MAX_ZIP_MEMBER_BYTES:
            raise BatchError(f"ZIP member is too large: {raw_name!r}")
        total += info.file_size
        if total > MAX_ZIP_UNCOMPRESSED_BYTES:
            raise BatchError("ZIP uncompressed size exceeds the safety limit")
        names.append(raw_name)
    return names


def inspect_vsdx_structure(path: Path) -> tuple[dict[str, Any], str]:
    """Validate the package and redact-sensitive textual contents before use."""

    texts: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = validate_zip_archive(archive)
        if "visio/document.xml" not in names:
            raise BatchError("VSDX is missing visio/document.xml")
        page_parts = sorted(
            name
            for name in names
            if re.fullmatch(r"visio/pages/page\d+\.xml", name)
        )
        if not page_parts:
            raise BatchError("VSDX contains no page XML")
        for name in page_parts:
            root = ElementTree.fromstring(archive.read(name))
            for element in root.iter():
                if element.tag.rsplit("}", 1)[-1] == "Text":
                    text = "".join(element.itertext()).strip()
                    if text:
                        texts.append(text)
    combined = normalized_text("\n".join(texts))
    sensitive_findings = sensitive_text_findings(texts)
    if sensitive_findings:
        summary = ", ".join(
            f"{finding['type']}={finding['count']}" for finding in sensitive_findings
        )
        raise BatchError(
            "VSDX contains potential plaintext credential assignments; "
            f"security review required ({summary})"
        )
    return {
        "kind": "visio-vsdx",
        "package_entries": len(names),
        "page_part_count": len(page_parts),
        "text_count": len(texts),
    }, combined


def inspect_vsdx_title_semantics(title: str, combined: str) -> dict[str, Any]:
    """Return title-binding evidence without exposing diagram text."""

    signals = title_signals(title)
    if not signals:
        return {
            "title_signals": [],
            "matched_title_signals": [],
            "semantic_match_method": "none",
            "semantic_status": "source_binding_missing",
        }
    matched = [signal for signal in signals if signal in combined]
    semantic_match_method = "title_signal"
    if not matched:
        matched = matched_chinese_bigram_pair(title, combined)
        semantic_match_method = "chinese_bigram_pair"
    return {
        "title_signals": signals,
        "matched_title_signals": matched,
        "semantic_match_method": semantic_match_method,
        "semantic_status": "matched" if matched else "source_binding_missing",
    }


def inspect_vsdx(path: Path, title: str) -> dict[str, Any]:
    structure, combined = inspect_vsdx_structure(path)
    return {**structure, **inspect_vsdx_title_semantics(title, combined)}


def xmind_topic_title(topic: Any) -> str:
    if isinstance(topic, dict):
        title = topic.get("title")
        if isinstance(title, str):
            return title
    return ""


def inspect_xmind(path: Path, title: str) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        names = validate_zip_archive(archive)
        if "content.json" not in names:
            raise BatchError("XMind is missing content.json")
        content = json.loads(archive.read("content.json"))
    if not isinstance(content, list) or not content:
        raise BatchError("XMind content.json has no sheets")
    sheet = content[0]
    root = sheet.get("rootTopic")
    root_title = xmind_topic_title(root)
    empty_root = False
    if root_title != title:
        # ProcessOn exports a genuinely blank mindmap with the source title
        # on the sheet but no root-topic title.  Accept that shape only when
        # the sheet title is an exact match and the root has no attached
        # children; never weaken the normal title binding for non-empty maps.
        attached = (
            root.get("children", {}).get("attached", [])
            if isinstance(root, dict) and isinstance(root.get("children"), dict)
            else None
        )
        if not (
            root_title == ""
            and str(sheet.get("title") or "") == title
            and isinstance(attached, list)
            and not attached
        ):
            raise BatchError(
                f"XMind root title mismatch: expected {title!r}, got {root_title!r}"
            )
        empty_root = True
    return {
        "kind": "xmind",
        "package_source": "content.json",
        "root_title": root_title,
        "sheet_title": str(sheet.get("title") or ""),
        "empty_root": empty_root,
        "semantic_status": "matched_empty_root" if empty_root else "matched",
    }


def inspect_download(
    path: Path, entry: dict[str, Any], actual_format: str | None = None
) -> dict[str, Any]:
    actual = str(actual_format or entry["primary_format"]).lower()
    if actual == "vsdx":
        inspection = inspect_vsdx(path, str(entry["title"]))
    elif actual == "xmind":
        inspection = inspect_xmind(path, str(entry["title"]))
    elif actual == "pos":
        inspection = inspect_pos(path, text_limit=500)
        observed_title = str(inspection.get("title") or "")
        if observed_title != str(entry["title"]):
            raise BatchError(
                f"POS title mismatch: expected {entry['title']!r}, got {observed_title!r}"
            )
        findings = sensitive_text_findings(
            [str(item) for item in inspection.pop("text", [])]
        )
        if findings:
            summary = ", ".join(
                f"{finding['type']}={finding['count']}" for finding in findings
            )
            raise BatchError(
                "POS contains potential plaintext credential assignments; "
                f"security review required ({summary})"
            )
        inspection["semantic_status"] = "matched"
    else:
        raise BatchError(f"parallel batch does not support primary format: {actual}")
    inspection.update({"bytes": path.stat().st_size, "sha256": sha256(path)})
    return inspection


def yaml_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def write_metadata(
    path: Path,
    *,
    entry: dict[str, Any],
    browser_result: dict[str, Any],
    finalized: dict[str, Any],
    inspection: dict[str, Any],
    team_url: str,
) -> None:
    if path.is_symlink():
        raise BatchError(f"metadata path must not be a symlink: {path}")
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if yaml_string(entry["artifact_id"]) not in existing:
            raise BatchError(f"metadata already belongs to another artifact: {path}")
        return
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "schema_version: 1",
        'index_role: "asset-folder-index"',
        f"artifact_id: {yaml_string(entry['artifact_id'])}",
        'source: "processon"',
        f"source_path: {yaml_string(entry['source_path'])}",
        f"source_lookup_method: {yaml_string(browser_result.get('source_lookup_method', 'planned_directory'))}",
        f"source_url: {yaml_string(browser_result['source_url'])}",
        'source_url_status: "verified_from_dedicated_browser_popup"',
        f"remote_id: {yaml_string(browser_result['remote_id'])}",
        f"team_url: {yaml_string(team_url)}",
        f"title: {yaml_string(entry['title'])}",
        f"owner: {yaml_string(entry.get('owner', ''))}",
        f"remote_updated_at: {yaml_string(entry.get('remote_updated_at', ''))}",
        f"type: {yaml_string(entry['type'])}",
        f"type_evidence: {yaml_string('ProcessOn 盘点类型与官方下载菜单一致。')}",
        f"exported_at: {yaml_string(now)}",
        f"archived_at: {yaml_string(now)}",
        f"requested_format: {yaml_string(entry['primary_format'])}",
        f"actual_format: {yaml_string(browser_result.get('actual_format', entry['primary_format']))}",
        f"download_menu: {yaml_string(browser_result.get('download_menu', ''))}",
        f"fallback_used: {str(bool(browser_result.get('fallback_used', False))).lower()}",
        f"fallback_reason: {yaml_string(browser_result.get('fallback_reason', ''))}",
        f"file: {yaml_string(Path(finalized['destination']).name)}",
        f"bytes: {int(inspection['bytes'])}",
        f"sha256: {yaml_string(inspection['sha256'])}",
        f"finalizer_manifest: {yaml_string(finalized['manifest'])}",
        "inspection:",
    ]
    collision_binding = browser_result.get("collision_binding")
    if isinstance(collision_binding, dict):
        lines[lines.index("inspection:"):lines.index("inspection:")] = [
            'collision_confirmation_method: "inventory_order"',
            f"collision_occurrence: {int(collision_binding['occurrence'])}",
            f"collision_group_size: {int(collision_binding['group_size'])}",
        ]
    sanitization = browser_result.get("sanitization")
    if isinstance(sanitization, dict):
        lines[lines.index("inspection:"):lines.index("inspection:")] = [
            'derivative_status: "sanitized_derivative"',
            f"sanitized_from_sha256: {yaml_string(sanitization.get('source_sha256', ''))}",
            f"redaction_counts: {json.dumps(sanitization.get('redaction_counts', {}), ensure_ascii=False, sort_keys=True)}",
            'original_retention: "restricted_quarantine"',
        ]
    for key, value in inspection.items():
        if key in {"bytes", "sha256"}:
            continue
        if isinstance(value, list):
            lines.append(f"  {key}:")
            lines.extend(f"    - {yaml_string(item)}" for item in value)
        elif isinstance(value, int):
            lines.append(f"  {key}: {value}")
        else:
            lines.append(f"  {key}: {yaml_string(value)}")
    verification = (
        "隔离原件、源 URL 与 SHA-256 已核对；敏感值已在同格式副本中替换为占位符，"
        "复扫无命中，原件继续保留在受限隔离区。"
        if isinstance(sanitization, dict)
        else "浏览器弹页标题、源 URL、下载文件名、文件结构与文件内标题信号均已核对；归档 SHA-256 与下载文件一致。"
    )
    lines.extend(
        [
            f"verification: {yaml_string(verification)}",
            'visibility: "internal"',
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_top_level_metadata(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith(" ") or ":" not in line:
            continue
        key, raw = line.split(":", 1)
        raw = raw.strip()
        if not raw:
            continue
        try:
            result[key] = json.loads(raw)
        except json.JSONDecodeError:
            result[key] = raw
    return result


def quarantine_recreated_move_source(
    source: Path,
    *,
    artifact_id: str,
    archived_sha256: str,
    manifest: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any] | None:
    """Move a later retry residue away from an already moved source path.

    A successful no-copy finalization removes the original staging path.  A
    subsequent retry can recreate that same path with provider-generated
    metadata (for example a fresh POS exportTime) and a different SHA-256.
    Keep that later export as private run evidence instead of overwriting the
    archive or letting it impersonate the source recorded by the manifest.
    """

    if not source.exists():
        return None
    if source.is_symlink() or not source.is_file():
        raise BatchError(f"recreated move source is not a regular file: {source}")
    source_sha256 = sha256(source)
    if source_sha256 == archived_sha256:
        return None
    if manifest.get("operation") != "move":
        raise BatchError("a non-move manifest cannot ignore a changed download source")
    expected_parent = (args.download_dir / artifact_id).expanduser().resolve(strict=False)
    if source.parent.resolve(strict=False) != expected_parent:
        raise BatchError(
            f"recreated move source is outside the artifact staging directory: {source}"
        )
    quarantine_dir = args.progress.parent / "retry-residuals" / artifact_id
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    if quarantine_dir.is_symlink():
        raise BatchError(f"retry residual directory must not be a symlink: {quarantine_dir}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    quarantine = quarantine_dir / f"{source.stem}--{source_sha256[:12]}--{stamp}{source.suffix}"
    if quarantine.exists():
        raise BatchError(f"retry residual target already exists: {quarantine}")
    os.replace(source, quarantine)
    return {
        "original_path": str(source),
        "quarantine_path": str(quarantine),
        "bytes": quarantine.stat().st_size,
        "sha256": source_sha256,
        "reason": "source_path_recreated_after_manifest_move",
    }


def reconcile_existing(
    plan: dict[str, Any],
    progress: dict[str, Any],
    *,
    args: argparse.Namespace,
    explicitly_retried_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Finish a prior half-commit when metadata and finalizer evidence agree."""

    done = reconciliation_skip_ids(
        progress, explicitly_retried_ids=explicitly_retried_ids or set()
    )
    recovered: list[dict[str, Any]] = []
    for entry in plan["entries"]:
        artifact_id = str(entry.get("artifact_id", ""))
        if (
            not artifact_id
            or artifact_id in done
            or entry.get("confirmation_required")
            or not collision_entry_authorized(entry, args)
        ):
            continue
        folder = output_folder(args.output_root, entry)
        metadata_path = folder / "metadata.yml"
        if not metadata_path.is_file() or metadata_path.is_symlink():
            continue
        metadata = read_top_level_metadata(metadata_path)
        if metadata.get("artifact_id") != artifact_id:
            raise BatchError(f"existing metadata artifact_id mismatch: {metadata_path}")
        required = ("file", "sha256", "actual_format", "finalizer_manifest", "source_url", "remote_id")
        missing = [key for key in required if not metadata.get(key)]
        if missing:
            raise BatchError(f"existing metadata cannot be reconciled; missing {missing}: {metadata_path}")
        destination = folder / str(metadata["file"])
        manifest_path = Path(str(metadata["finalizer_manifest"])).expanduser()
        manifest = load_json(manifest_path)
        source = Path(str(manifest.get("source", ""))).expanduser()
        if not destination.is_file() or sha256(destination) != str(metadata["sha256"]):
            raise BatchError(f"existing archive file does not match metadata: {destination}")
        if not source.is_file() and manifest.get("operation") != "move":
            raise BatchError(f"cannot reconcile after staging source was removed: {source}")
        browser_result = {
            "source_url": str(metadata["source_url"]),
            "remote_id": str(metadata["remote_id"]),
        }
        verified_remote_id = verify_source_identity(entry, browser_result["source_url"])
        if verified_remote_id != browser_result["remote_id"]:
            raise BatchError(f"existing metadata source identity mismatch: {metadata_path}")
        recreated_source = None
        if artifact_id in (explicitly_retried_ids or set()):
            recreated_source = quarantine_recreated_move_source(
                source,
                artifact_id=artifact_id,
                archived_sha256=str(metadata["sha256"]),
                manifest=manifest,
                args=args,
            )
        try:
            if args.source_links:
                append_source_link(args.source_links, entry, browser_result)
            recorded = run_json(
                [
                    sys.executable,
                    str(ARCHIVE_STATE),
                    "record",
                    "--plan",
                    str(args.plan),
                    "--progress",
                    str(args.progress),
                    "--artifact-id",
                    artifact_id,
                    "--download-source",
                    str(source),
                    "--destination",
                    str(destination),
                    "--manifest",
                    str(manifest_path),
                    "--requested-format",
                    str(entry["primary_format"]),
                    "--actual-format",
                    str(metadata["actual_format"]),
                    "--download-event",
                    "observed",
                ]
            )
        except Exception:
            if recreated_source:
                quarantine = Path(recreated_source["quarantine_path"])
                if quarantine.exists() and not source.exists():
                    os.replace(quarantine, source)
            raise
        recovered.append(
            {
                "artifact_id": artifact_id,
                "status": "reconciled",
                "destination": str(destination),
                "metadata": str(metadata_path),
                "manifest": str(manifest_path),
                "recreated_source_quarantine": recreated_source,
                "progress_counts": recorded.get("counts", {}),
            }
        )
        done.add(artifact_id)
    return recovered


def append_source_link(path: Path, entry: dict[str, Any], browser_result: dict[str, Any]) -> None:
    if path.is_symlink():
        raise BatchError(f"source-links path must not be a symlink: {path}")
    text = path.read_text(encoding="utf-8")
    artifact_id = str(entry["artifact_id"])
    observed_url = normalized_processon_source_url(str(browser_result["source_url"]))
    for match in re.finditer(
        r'(?ms)^  - artifact_id: "(?P<artifact>[^"]+)"\n(?P<body>.*?)(?=^  - artifact_id:|\Z)',
        text,
    ):
        url_match = re.search(
            r'^    source_url: "([^"]*)"$', match.group("body"), re.MULTILINE
        )
        if not url_match or match.group("artifact") == artifact_id:
            continue
        try:
            existing_normalized = normalized_processon_source_url(url_match.group(1))
        except BrowserRunnerError:
            continue
        if existing_normalized == observed_url:
            raise BatchError(
                "source-links already binds this ProcessOn URL to another artifact: "
                f"{match.group('artifact')}"
            )
    if f'artifact_id: "{artifact_id}"' in text:
        pattern = re.compile(
            rf'(?ms)^  - artifact_id: "{re.escape(artifact_id)}"\n(?P<body>.*?)(?=^  - artifact_id:|\Z)'
        )
        match = pattern.search(text)
        existing_url = ""
        if match:
            url_match = re.search(r'^    source_url: "([^"]*)"$', match.group("body"), re.MULTILINE)
            existing_url = url_match.group(1) if url_match else ""
        if existing_url != str(browser_result["source_url"]):
            raise BatchError(
                f"source-links URL conflict for {artifact_id}: {existing_url!r} != {browser_result['source_url']!r}"
            )
        return
    if "\nentries:\n" not in text:
        raise BatchError("source-links YAML is missing entries")
    block = "\n".join(
        [
            f'  - artifact_id: "{artifact_id}"',
            f"    source_path: {yaml_string(entry['source_path'])}",
            f"    title: {yaml_string(entry['title'])}",
            f"    type: {yaml_string(entry['type'])}",
            f"    source_url: {yaml_string(browser_result['source_url'])}",
            f"    remote_id: {yaml_string(browser_result['remote_id'])}",
            '    status: "verified_from_dedicated_browser_popup"',
        ]
    )
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text.rstrip() + "\n" + block + "\n", encoding="utf-8")
    temporary.replace(path)


def write_progress_mirror(
    path: Path, *, plan: dict[str, Any], progress: dict[str, Any], run_id: str
) -> None:
    if path.is_symlink():
        raise BatchError(f"progress mirror must not be a symlink: {path}")
    counts = progress.get("counts", {})
    legacy_review = legacy_flat_download_review(progress)
    legacy_revalidation_ids = {
        str(item.get("artifact_id", ""))
        for item in legacy_review["revalidation_items"]
    }
    explicit_revalidation = [
        item for item in progress.get("revalidation_pending", []) if isinstance(item, dict)
    ]
    explicit_revalidation_ids = {
        str(item.get("artifact_id", "")) for item in explicit_revalidation
    }
    recorded_remaining_known = int(counts.get("remaining_known", 0))
    legacy_revalidation_count = int(legacy_review["revalidation_required_count"])
    explicit_revalidation_count = len(explicit_revalidation_ids)
    revalidation_count = len(legacy_revalidation_ids | explicit_revalidation_ids)
    remaining_known = recorded_remaining_known + legacy_revalidation_count
    blocked = int(counts.get("blocked", 0))
    failed = int(counts.get("failed", 0))
    unknown = int(counts.get("unknown_pending_confirmation", 0))
    collision_pending = len(deferred_collision_entries(plan, progress))
    automatic_remaining = max(remaining_known - blocked - failed - collision_pending, 0)
    if remaining_known > 0:
        mirror_status = "asset_archive_running"
    elif unknown > 0:
        mirror_status = "known_artifacts_completed_pending_confirmation"
    else:
        mirror_status = "asset_archive_completed"
    lines = [
        "schema_version: 1",
        'source: "processon"',
        f"run_id: {yaml_string(run_id)}",
        f"updated_at: {yaml_string(datetime.now().astimezone().isoformat(timespec='seconds'))}",
        f"status: {yaml_string(mirror_status)}",
        "archive_plan:",
        f"  checkpoint_sha256: {yaml_string(plan.get('checkpoint_sha256', ''))}",
        f"  plan_sha256: {yaml_string(progress.get('plan', {}).get('sha256', ''))}",
        f"  archive_status: {yaml_string(plan.get('archive_status', ''))}",
        f"  ready_for_known_artifacts: {str(bool(plan.get('ready_for_known_artifacts'))).lower()}",
        f"  ready_for_archive: {str(bool(plan.get('ready_for_archive'))).lower()}",
        "counts:",
        f"  total_inventory_entries: {int(plan.get('counts', {}).get('total_entries', len(plan.get('entries', []))))}",
        f"  planned_known: {int(counts.get('planned_known', 0))}",
        f"  unknown_pending_confirmation: {unknown}",
        f"  completed: {int(legacy_review['trusted_completed_count'])}",
        f"  completed_recorded: {int(counts.get('completed', 0))}",
        f"  revalidation_pending: {revalidation_count}",
        f"  explicit_revalidation_pending: {explicit_revalidation_count}",
        f"  legacy_flat_revalidation_pending: {legacy_revalidation_count}",
        f"  failed: {failed}",
        f"  blocked: {blocked}",
        f"  remaining_known: {remaining_known}",
        f"  remaining_known_recorded: {recorded_remaining_known}",
        f"  collision_identity_pending: {collision_pending}",
        f"  automatic_remaining: {automatic_remaining}",
        "completed:",
    ]
    for item in progress.get("completed", []):
        if str(item.get("artifact_id", "")) in legacy_revalidation_ids:
            continue
        destination = Path(str(item.get("archive_destination", "")))
        metadata = destination.parent / "metadata.yml"
        lines.extend(
            [
                f"  - artifact_id: {yaml_string(item.get('artifact_id', ''))}",
                f"    source_path: {yaml_string(item.get('source_path', ''))}",
                f"    format: {yaml_string(item.get('actual_format', ''))}",
                f"    file: {yaml_string(os.path.relpath(destination, path.parent))}",
                f"    metadata: {yaml_string(os.path.relpath(metadata, path.parent))}",
            ]
        )
    lines.append("revalidation_pending:")
    for item in explicit_revalidation:
        prior = item.get("prior_completion", {})
        lines.extend(
            [
                f"  - artifact_id: {yaml_string(item.get('artifact_id', ''))}",
                f"    source_path: {yaml_string(item.get('source_path', ''))}",
                f"    prior_download_source: {yaml_string(prior.get('download_source', ''))}",
                f"    reason: {yaml_string(item.get('reason', ''))}",
                '    state: "explicitly_reopened"',
            ]
        )
    for item in legacy_review["revalidation_items"]:
        lines.extend(
            [
                f"  - artifact_id: {yaml_string(item.get('artifact_id', ''))}",
                f"    source_path: {yaml_string(item.get('source_path', ''))}",
                f"    download_source: {yaml_string(item.get('download_source', ''))}",
                f"    archive_destination: {yaml_string(item.get('archive_destination', ''))}",
                '    reason: "个人 Downloads 平铺下载无法证明来源与 artifact_id 唯一绑定；须先重开，再按 artifact_id 隔离重下。"',
                '    state: "legacy_completed_pending_reopen"',
            ]
        )
    lines.append("blocked:")
    for item in progress.get("blocked", []):
        lines.extend(
            [
                f"  - artifact_id: {yaml_string(item.get('artifact_id', ''))}",
                f"    source_path: {yaml_string(item.get('source_path', ''))}",
                f"    reason: {yaml_string(item.get('reason', ''))}",
            ]
        )
    lines.append(
        'next_action: "继续按机械队列下载已确认类型；并发项须通过语义交叉校验，未知类型须人工确认。"'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(path)


def finalize_result(
    browser_result: dict[str, Any],
    entry: dict[str, Any],
    *,
    args: argparse.Namespace,
) -> dict[str, Any]:
    source = Path(browser_result["download"]["path"])
    actual_format = str(
        browser_result.get("actual_format", entry["primary_format"])
    ).lower()
    inspection = apply_observed_source_binding(
        inspect_download(source, entry, actual_format),
        browser_result=browser_result,
        entry=entry,
    )
    destination_dir = output_folder(args.output_root, entry)
    base_command = [
        sys.executable,
        str(FINALIZER),
        "finalize",
        str(source),
        "--output-dir",
        str(destination_dir),
        "--manifest-dir",
        str(args.manifest_dir),
        "--temp-dir",
        str(args.managed_temp_root),
        "--collision",
        "fail",
        "--move",
    ]
    dry_run = run_json(base_command + ["--dry-run"])
    if dry_run.get("status") != "dry-run":
        raise BatchError("finalizer dry-run did not return dry-run status")
    finalized = run_json(base_command)
    if finalized.get("status") != "completed":
        raise BatchError("finalizer did not return completed status")
    destination = Path(finalized["destination"])
    if sha256(destination) != inspection["sha256"]:
        raise BatchError("archive destination hash differs from browser download")
    metadata_path = destination_dir / "metadata.yml"
    write_metadata(
        metadata_path,
        entry=entry,
        browser_result=browser_result,
        finalized=finalized,
        inspection=inspection,
        team_url=args.team_url,
    )
    if args.source_links:
        append_source_link(args.source_links, entry, browser_result)
    recorded = run_json(
        [
            sys.executable,
            str(ARCHIVE_STATE),
            "record",
            "--plan",
            str(args.plan),
            "--progress",
            str(args.progress),
            "--artifact-id",
            str(entry["artifact_id"]),
            "--download-source",
            str(source),
            "--destination",
            str(destination),
            "--manifest",
            str(finalized["manifest"]),
            "--requested-format",
            str(entry["primary_format"]),
            "--actual-format",
            actual_format,
            "--download-event",
            "observed",
        ]
    )
    return {
        "artifact_id": entry["artifact_id"],
        "status": "completed",
        "source_url": browser_result["source_url"],
        "download_menu": browser_result.get("download_menu", ""),
        "download": str(source),
        "destination": str(destination),
        "metadata": str(metadata_path),
        "manifest": finalized["manifest"],
        "sha256": inspection["sha256"],
        "inspection": inspection,
        "progress_counts": recorded.get("counts", {}),
    }


def reconcile_staged_downloads(
    plan: dict[str, Any], progress: dict[str, Any], *, args: argparse.Namespace
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Recover only journal-bound staged files left by an interrupted batch.

    A raw file in staging is never sufficient evidence. Recovery requires the
    per-artifact atomic receipt written after `download.save_as`, then repeats
    source identity, file isolation and structure checks before finalization.
    """

    root = staging_receipt_root(args.progress)
    if not root.exists():
        return [], []
    if root.is_symlink() or not root.is_dir():
        raise BatchError(f"staging receipt root is not a regular directory: {root}")
    plan_by_id = {
        str(entry.get("artifact_id", "")): entry
        for entry in plan["entries"]
        if entry.get("artifact_id")
    }
    done = progress_done_ids(progress)
    recovered: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for receipt_path in sorted(root.glob("*.json")):
        artifact_id = receipt_path.stem
        if artifact_id in done:
            remove_staging_receipt(args.progress, artifact_id)
            continue
        entry = plan_by_id.get(artifact_id)
        if entry is None:
            errors.append({"receipt": str(receipt_path), "error": "artifact_not_in_current_plan"})
            continue
        if (
            entry.get("confirmation_required")
            or entry.get("type") == "unknown"
            or not collision_entry_authorized(entry, args)
        ):
            errors.append({"receipt": str(receipt_path), "error": "artifact_not_eligible_for_auto_recovery"})
            continue
        entry = entry_with_collision_confirmation(entry, args)
        try:
            browser_result = load_staging_result(receipt_path, entry, args=args)
            inspection = apply_observed_source_binding(
                inspect_download(
                    Path(browser_result["download"]["path"]),
                    entry,
                    str(browser_result.get("actual_format", entry["primary_format"])),
                ),
                browser_result=browser_result,
                entry=entry,
            )
            if inspection_requires_source_binding_block(inspection):
                recovered.append(
                    block_structurally_valid_unbound_vsdx(
                        browser_result,
                        entry,
                        inspection,
                        args=args,
                    )
                )
            else:
                recovered.append(finalize_result(browser_result, entry, args=args))
            remove_staging_receipt(args.progress, artifact_id)
            done.add(artifact_id)
        except Exception as exc:
            errors.append(
                {
                    "receipt": str(receipt_path),
                    "artifact_id": artifact_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return recovered, errors


def load_confirmed_collision_diagnostic(
    entry: dict[str, Any], *, args: argparse.Namespace, unique_inventory: bool = False
) -> dict[str, Any]:
    """Rebuild one browser result from a previously blocked private diagnostic.

    The diagnostic is not trusted by itself.  The current confirmation must
    still bind the same plan entry and inventory occurrence, the staged file
    must remain in its artifact-isolated directory, its fresh inspection must
    match the recorded hash/size/kind, and the observed ProcessOn URL must
    still yield the recorded remote id.
    """

    artifact_id = str(entry.get("artifact_id", ""))
    diagnostic_path = (
        args.progress.expanduser().resolve(strict=False).parent
        / "semantic-binding-diagnostics"
        / f"{artifact_id}.json"
    )
    if diagnostic_path.is_symlink() or not diagnostic_path.is_file():
        raise BatchError(
            f"confirmed collision diagnostic is not a regular file: {diagnostic_path}"
        )
    payload = load_json(diagnostic_path)
    expected_fields = {
        "schema_version": 1,
        "kind": "content_structure_verified_source_binding_missing",
        "artifact_id": artifact_id,
        "source_path": str(entry.get("source_path", "")),
        "title": str(entry.get("title", "")),
        "requested_format": str(entry.get("primary_format", "")),
    }
    for key, expected in expected_fields.items():
        if payload.get(key) != expected:
            raise BatchError(
                f"confirmed collision diagnostic {key} differs from the plan: {diagnostic_path}"
            )
    download = payload.get("download")
    if not isinstance(download, dict):
        raise BatchError(f"confirmed collision diagnostic has no download: {diagnostic_path}")
    source = Path(str(download.get("path", ""))).expanduser().resolve(strict=False)
    expected_parent = (args.download_dir / artifact_id).expanduser().resolve(strict=False)
    if source.parent != expected_parent or source.is_symlink() or not source.is_file():
        raise BatchError(
            f"confirmed collision diagnostic is not bound to isolated staging: {diagnostic_path}"
        )
    suggested = str(download.get("suggested_filename", ""))
    if Path(suggested).name != source.name:
        raise BatchError(
            f"confirmed collision diagnostic filename differs from staging: {diagnostic_path}"
        )
    prior_inspection = payload.get("inspection")
    if not isinstance(prior_inspection, dict):
        raise BatchError(
            f"confirmed collision diagnostic has no prior inspection: {diagnostic_path}"
        )
    current_inspection = inspect_download(
        source,
        entry,
        str(payload.get("actual_format", entry["primary_format"])),
    )
    for key in ("sha256", "bytes", "kind"):
        if current_inspection.get(key) != prior_inspection.get(key):
            raise BatchError(
                f"confirmed collision staged file {key} differs from diagnostic: {diagnostic_path}"
            )
    source_url = str(payload.get("observed_source_url", ""))
    remote_id = str(payload.get("observed_remote_id", ""))
    if not source_url or not remote_id or verify_source_identity(entry, source_url) != remote_id:
        raise BatchError(
            f"confirmed collision diagnostic has no verified ProcessOn source identity: {diagnostic_path}"
        )
    if unique_inventory:
        if entry.get("collision_risk") != "none_detected":
            raise BatchError(
                f"unique inventory recovery is not allowed for a collision entry: {diagnostic_path}"
            )
        binding = None
    else:
        binding = {
            "confirmation_method": str(entry.get("_collision_confirmation_method", "")),
            "occurrence": int(entry.get("_collision_occurrence", -1)),
            "group_size": int(entry.get("_collision_group_size", 0)),
        }
    result = {
        "artifact_id": artifact_id,
        "source_path": str(entry["source_path"]),
        "title": str(entry["title"]),
        "requested_format": str(entry["primary_format"]),
        "source_url": source_url,
        "source_title": str(entry["title"]),
        "remote_id": remote_id,
        "download_menu": (
            "recovered_unique_inventory"
            if unique_inventory
            else "recovered_confirmed_collision"
        ),
        "download": {
            "path": str(source),
            "bytes": source.stat().st_size,
            "suggested_filename": suggested,
        },
        "ok": True,
    }
    if binding is not None:
        result["collision_binding"] = binding
    promoted = apply_observed_source_binding(
        current_inspection, browser_result=result, entry=entry
    )
    if inspection_requires_source_binding_block(promoted):
        raise BatchError(
            f"confirmed collision diagnostic did not establish source binding: {diagnostic_path}"
        )
    return result


def completed_source_identities(progress: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Replay archived metadata to collect already-bound remote ids and URLs."""

    remote_ids: set[str] = set()
    source_urls: set[str] = set()
    for item in progress.get("completed", []):
        destination = Path(str(item.get("archive_destination", "")))
        metadata_path = destination.parent / "metadata.yml"
        if not metadata_path.is_file() or metadata_path.is_symlink():
            continue
        metadata = read_top_level_metadata(metadata_path)
        remote_id = str(metadata.get("remote_id") or "")
        source_url = str(metadata.get("source_url") or "")
        if remote_id:
            remote_ids.add(remote_id)
        if source_url:
            source_urls.add(normalized_processon_source_url(source_url))
    return remote_ids, source_urls


def reconcile_unique_inventory_blocks(
    plan: dict[str, Any],
    progress: dict[str, Any],
    *,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Finalize bounded old-policy blocks whose inventory row was unique."""

    plan_by_id = {
        str(entry.get("artifact_id", "")): entry
        for entry in plan["entries"]
        if entry.get("artifact_id")
    }
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    errors: list[dict[str, str]] = []
    for blocked_item in progress.get("blocked", []):
        if len(candidates) >= args.limit:
            break
        artifact_id = str(blocked_item.get("artifact_id", ""))
        if (
            blocked_item.get("reason")
            != "content_structure_verified_source_binding_missing"
        ):
            continue
        entry = plan_by_id.get(artifact_id)
        if entry is None:
            errors.append(
                {"artifact_id": artifact_id, "error": "artifact_not_in_current_plan"}
            )
            continue
        if entry.get("collision_risk") != "none_detected":
            continue
        try:
            candidates.append(
                (
                    entry,
                    load_confirmed_collision_diagnostic(
                        entry, args=args, unique_inventory=True
                    ),
                )
            )
        except Exception as exc:
            errors.append(
                {"artifact_id": artifact_id, "error": f"{type(exc).__name__}: {exc}"}
            )

    completed_remote_ids, completed_urls = completed_source_identities(progress)
    remote_owners: dict[str, list[str]] = {}
    url_owners: dict[str, list[str]] = {}
    for entry, result in candidates:
        artifact_id = str(entry["artifact_id"])
        remote_owners.setdefault(str(result["remote_id"]), []).append(artifact_id)
        url_owners.setdefault(
            normalized_processon_source_url(str(result["source_url"])), []
        ).append(artifact_id)
    duplicate_ids = {
        artifact_id
        for owners in (*remote_owners.values(), *url_owners.values())
        if len(set(owners)) > 1
        for artifact_id in owners
    }
    for remote_id, owners in remote_owners.items():
        if remote_id in completed_remote_ids:
            duplicate_ids.update(owners)
    for source_url, owners in url_owners.items():
        if source_url in completed_urls:
            duplicate_ids.update(owners)

    recovered: list[dict[str, Any]] = []
    for entry, result in candidates:
        artifact_id = str(entry["artifact_id"])
        if artifact_id in duplicate_ids:
            errors.append(
                {
                    "artifact_id": artifact_id,
                    "error": "unique_inventory_diagnostic_source_identity_is_not_unique",
                }
            )
            continue
        try:
            recovered.append(finalize_result(result, entry, args=args))
        except Exception as exc:
            errors.append(
                {"artifact_id": artifact_id, "error": f"{type(exc).__name__}: {exc}"}
            )
    return recovered, errors


def reconcile_confirmed_collision_blocks(
    plan: dict[str, Any], progress: dict[str, Any], *, args: argparse.Namespace
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Finalize exact confirmed collision downloads blocked by the old policy."""

    confirmations = getattr(args, "collision_confirmations", OrderedDict())
    if not confirmations:
        return [], []
    plan_by_id = {
        str(entry.get("artifact_id", "")): entry
        for entry in plan["entries"]
        if entry.get("artifact_id")
    }
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    errors: list[dict[str, str]] = []
    for blocked_item in progress.get("blocked", []):
        artifact_id = str(blocked_item.get("artifact_id", ""))
        if (
            artifact_id not in confirmations
            or blocked_item.get("reason")
            != "content_structure_verified_source_binding_missing"
        ):
            continue
        entry = plan_by_id.get(artifact_id)
        if entry is None:
            errors.append(
                {"artifact_id": artifact_id, "error": "artifact_not_in_current_plan"}
            )
            continue
        decorated = entry_with_collision_confirmation(entry, args)
        try:
            candidates.append(
                (decorated, load_confirmed_collision_diagnostic(decorated, args=args))
            )
        except Exception as exc:
            errors.append(
                {"artifact_id": artifact_id, "error": f"{type(exc).__name__}: {exc}"}
            )

    remote_owners: dict[str, list[str]] = {}
    url_owners: dict[str, list[str]] = {}
    for entry, result in candidates:
        artifact_id = str(entry["artifact_id"])
        remote_owners.setdefault(str(result["remote_id"]), []).append(artifact_id)
        url_owners.setdefault(
            normalized_processon_source_url(str(result["source_url"])), []
        ).append(artifact_id)
    duplicate_ids = {
        artifact_id
        for owners in (*remote_owners.values(), *url_owners.values())
        if len(set(owners)) > 1
        for artifact_id in owners
    }
    recovered: list[dict[str, Any]] = []
    for entry, result in candidates:
        artifact_id = str(entry["artifact_id"])
        if artifact_id in duplicate_ids:
            errors.append(
                {
                    "artifact_id": artifact_id,
                    "error": "confirmed_collision_diagnostics_share_one_source_identity",
                }
            )
            continue
        try:
            recovered.append(finalize_result(result, entry, args=args))
        except Exception as exc:
            errors.append(
                {"artifact_id": artifact_id, "error": f"{type(exc).__name__}: {exc}"}
            )
    return recovered, errors


def write_receipt(receipt_dir: Path, payload: dict[str, Any]) -> Path:
    receipt_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = receipt_dir / f"processon-archive-batch-{stamp}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    return target


def duplicate_browser_source_artifact_ids(results: list[dict[str, Any]]) -> set[str]:
    """Return every artifact participating in a duplicated observed remote id."""

    owners: dict[str, list[str]] = {}
    for result in results:
        if not result.get("ok"):
            continue
        remote_id = str(result.get("remote_id") or "")
        artifact_id = str(result.get("artifact_id") or "")
        if remote_id and artifact_id:
            owners.setdefault(remote_id, []).append(artifact_id)
    return {
        artifact_id
        for artifact_ids in owners.values()
        if len(set(artifact_ids)) > 1
        for artifact_id in artifact_ids
    }


def cmd_run(args: argparse.Namespace) -> dict[str, Any]:
    plan = load_json(args.plan)
    progress = load_json(args.progress)
    validate_plan(plan, progress)
    validate_processon_url(args.team_url)
    empty_source_ids = [
        str(item).strip() for item in args.allow_verified_empty_source or []
    ]
    if len(empty_source_ids) != len(set(empty_source_ids)):
        raise BatchError("--allow-verified-empty-source values must be unique")
    if empty_source_ids and not args.retry_blocked:
        raise BatchError("--allow-verified-empty-source requires --retry-blocked")
    if not set(empty_source_ids) <= set(args.artifact_id or []):
        raise BatchError(
            "--allow-verified-empty-source must also be named by --artifact-id"
        )
    security_redaction_ids = [
        str(item).strip() for item in args.redact_security_block or []
    ]
    if len(security_redaction_ids) != len(set(security_redaction_ids)):
        raise BatchError("--redact-security-block values must be unique")
    if security_redaction_ids and not args.retry_blocked:
        raise BatchError("--redact-security-block requires --retry-blocked")
    if not set(security_redaction_ids) <= set(args.artifact_id or []):
        raise BatchError("--redact-security-block must also be named by --artifact-id")
    overlap = set(security_redaction_ids) & set(empty_source_ids)
    if overlap:
        raise BatchError(
            "one artifact cannot be both an empty source and a security redaction"
        )
    collision_confirmations = load_collision_confirmation(
        args.collision_confirmation,
        plan_path=args.plan,
        plan=plan,
        progress=progress,
    )
    args.collision_confirmations = collision_confirmations
    proof = validate_concurrency_proof(
        args.concurrency_proof, workers=args.workers, plan=plan, progress=progress
    )
    # Verify the current plan against the current progress/checkpoint before browsing.
    run_json(
        [
            sys.executable,
            str(ARCHIVE_STATE),
            "audit",
            "--plan",
            str(args.plan),
            "--progress",
            str(args.progress),
        ]
    )
    security_redaction_preview: list[dict[str, str]] = []
    if security_redaction_ids:
        plan_by_id = {
            str(entry.get("artifact_id") or ""): entry
            for entry in plan.get("entries", [])
            if entry.get("artifact_id")
        }
        blocked_by_id = {
            str(item.get("artifact_id") or ""): item
            for item in progress.get("blocked", [])
            if isinstance(item, dict) and item.get("artifact_id")
        }
        for artifact_id in security_redaction_ids:
            entry = plan_by_id.get(artifact_id)
            blocked_record = blocked_by_id.get(artifact_id)
            if entry is None or blocked_record is None:
                raise BatchError(
                    f"security redaction artifact is not currently blocked: {artifact_id}"
                )
            evidence = validated_security_block_source(
                entry,
                blocked_record,
                args=args,
            )
            security_redaction_preview.append(
                {
                    "artifact_id": artifact_id,
                    "source_sha256": sha256(Path(evidence["source"])),
                    "source_url": evidence["source_url"],
                }
            )
    reconciled: list[dict[str, Any]] = []
    unique_block_recovered: list[dict[str, Any]] = []
    unique_block_recovery_errors: list[dict[str, str]] = []
    confirmed_block_recovered: list[dict[str, Any]] = []
    confirmed_block_recovery_errors: list[dict[str, str]] = []
    staging_recovered: list[dict[str, Any]] = []
    staging_recovery_errors: list[dict[str, str]] = []
    security_redacted: list[dict[str, Any]] = []
    security_redaction_errors: list[dict[str, str]] = []
    explicitly_retried_ids: set[str] = set()
    if args.retry_failed or args.retry_blocked:
        explicitly_retried_ids = {
            str(entry["artifact_id"])
            for entry in choose_entries(
                plan,
                progress,
                args.limit,
                workers=args.workers,
                retry_failed=args.retry_failed,
                retry_blocked=args.retry_blocked,
                artifact_ids=args.artifact_id,
                collision_confirmations=collision_confirmations,
            )
        }
    if not args.dry_run:
        unique_block_recovered, unique_block_recovery_errors = (
            reconcile_unique_inventory_blocks(plan, progress, args=args)
        )
        if unique_block_recovered:
            progress = load_json(args.progress)
        confirmed_block_recovered, confirmed_block_recovery_errors = (
            reconcile_confirmed_collision_blocks(plan, progress, args=args)
        )
        if confirmed_block_recovered:
            progress = load_json(args.progress)
        staging_recovered, staging_recovery_errors = reconcile_staged_downloads(
            plan, progress, args=args
        )
        if staging_recovered:
            progress = load_json(args.progress)
        if security_redaction_ids:
            security_redacted, security_redaction_errors = sanitize_security_blocks(
                plan,
                progress,
                args=args,
                artifact_ids=security_redaction_ids,
            )
            if security_redacted:
                progress = load_json(args.progress)
        reconciled = reconcile_existing(
            plan,
            progress,
            args=args,
            explicitly_retried_ids=explicitly_retried_ids,
        )
        if reconciled:
            progress = load_json(args.progress)
    if args.recover_existing_only:
        audit = run_json(
            [
                sys.executable,
                str(ARCHIVE_STATE),
                "audit",
                "--plan",
                str(args.plan),
                "--progress",
                str(args.progress),
            ]
        )
        refreshed_progress = load_json(args.progress)
        if args.progress_mirror:
            write_progress_mirror(
                args.progress_mirror,
                plan=plan,
                progress=refreshed_progress,
                run_id=args.progress.parent.parent.name,
            )
        payload = {
            "schema_version": 1,
            "status": "completed" if not unique_block_recovery_errors else "partial",
            "mode": "recover_existing_only",
            "unique_block_recovered_count": len(unique_block_recovered),
            "unique_block_recovered": unique_block_recovered,
            "unique_block_recovery_error_count": len(unique_block_recovery_errors),
            "unique_block_recovery_errors": unique_block_recovery_errors,
            "confirmed_block_recovered_count": len(confirmed_block_recovered),
            "staging_recovered_count": len(staging_recovered),
            "reconciled_count": len(reconciled),
            "audit": audit,
            "created_at": utc_now(),
        }
        payload["receipt_file"] = str(write_receipt(args.receipt_dir, payload))
        return payload
    legacy_review = legacy_flat_download_review(progress)
    deferred_collisions = deferred_collision_entries(
        plan, progress, confirmed_ids=set(collision_confirmations)
    )
    selection_artifact_ids = list(args.artifact_id or [])
    if not args.dry_run and security_redaction_ids:
        selection_artifact_ids = [
            artifact_id
            for artifact_id in selection_artifact_ids
            if artifact_id not in set(security_redaction_ids)
        ]
    if (args.retry_failed or args.retry_blocked) and not selection_artifact_ids:
        selected = []
    else:
        selected = choose_entries(
            plan,
            progress,
            args.limit,
            workers=args.workers,
            retry_failed=args.retry_failed,
            retry_blocked=args.retry_blocked,
            artifact_ids=selection_artifact_ids,
            collision_confirmations=collision_confirmations,
        )
    retry_source_bound_ids: list[str] = []
    if args.retry_failed and selected:
        selected, retry_source_bound_ids = bind_retry_failed_source_evidence(
            selected,
            progress,
            progress_path=args.progress,
        )
    empty_source_bound_ids: list[str] = []
    if args.retry_blocked and empty_source_ids and selected:
        selected, empty_source_bound_ids = bind_retry_blocked_empty_source_evidence(
            selected,
            progress,
            output_root=args.output_root,
            allowed_artifact_ids=set(empty_source_ids),
        )
    if not selected:
        refreshed_progress = load_json(args.progress)
        if args.progress_mirror and not args.dry_run:
            write_progress_mirror(
                args.progress_mirror,
                plan=plan,
                progress=refreshed_progress,
                run_id=args.progress.parent.parent.name,
            )
        payload = {
            "schema_version": 1,
            "status": (
                "partial"
                if security_redaction_errors
                else "completed"
                if security_redacted
                else "collision_confirmation_required"
                if deferred_collisions
                else "nothing_to_do"
            ),
            "selected": 0,
            "deferred_collision_count": len(deferred_collisions),
            "authorized_collision_count": len(collision_confirmations),
            "deferred_collision_artifact_ids": [
                str(item["artifact_id"]) for item in deferred_collisions
            ],
            "legacy_flat_download_review": legacy_review,
            "created_at": utc_now(),
            "reconciled": reconciled,
            "confirmed_block_recovered": confirmed_block_recovered,
            "confirmed_block_recovery_errors": confirmed_block_recovery_errors,
            "staging_recovered": staging_recovered,
            "staging_recovery_errors": staging_recovery_errors,
            "security_redaction_preview": security_redaction_preview,
            "security_redacted": security_redacted,
            "security_redaction_errors": security_redaction_errors,
        }
        payload["receipt_file"] = str(write_receipt(args.receipt_dir, payload))
        return payload
    if args.dry_run:
        payload = {
            "schema_version": 1,
            "status": "dry-run",
            "workers": args.workers,
            "concurrency_proof": str(args.concurrency_proof) if proof else None,
            "selected": len(selected),
            "retry_source_binding_count": len(retry_source_bound_ids),
            "retry_source_bound_artifact_ids": retry_source_bound_ids,
            "empty_source_binding_count": len(empty_source_bound_ids),
            "empty_source_bound_artifact_ids": empty_source_bound_ids,
            "security_redaction_preview": security_redaction_preview,
            "deferred_collision_count": len(deferred_collisions),
            "authorized_collision_count": len(collision_confirmations),
            "legacy_flat_download_review": legacy_review,
            "jobs": [
                {"source_directory": directory, "artifact_ids": [item["artifact_id"] for item in items]}
                for directory, items in build_jobs(selected, args.workers)
            ],
                "created_at": utc_now(),
                "confirmed_block_recovered": confirmed_block_recovered,
                "confirmed_block_recovery_errors": confirmed_block_recovery_errors,
                "staging_recovered": staging_recovered,
                "staging_recovery_errors": staging_recovery_errors,
            }
        payload["receipt_file"] = str(write_receipt(args.receipt_dir, payload))
        return payload

    results, browser_receipt = asyncio.run(
        browser_download_batch(
            selected,
            plan=plan,
            team_url=args.team_url,
            profile_dir=args.profile_dir,
            download_dir=args.download_dir,
            progress_path=args.progress,
            workers=args.workers,
            settle_ms=args.settle_ms,
            timeout_ms=args.timeout_ms,
        )
    )
    selected_by_id = {str(item["artifact_id"]): item for item in selected}
    completed: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    seen_hashes: dict[str, str] = {}
    duplicate_source_artifacts = duplicate_browser_source_artifact_ids(results)
    for result in results:
        if not result.get("ok"):
            pending.append(result)
            continue
        if str(result.get("artifact_id", "")) in duplicate_source_artifacts:
            pending.append(
                {
                    **result,
                    "ok": False,
                    "error": "BatchError: one ProcessOn remote id was observed for multiple artifacts",
                    "stage": "source_identity",
                }
            )
            continue
        entry = selected_by_id[str(result["artifact_id"])]
        try:
            inspection = apply_observed_source_binding(
                inspect_download(
                    Path(result["download"]["path"]),
                    entry,
                    str(result.get("actual_format", entry["primary_format"])),
                ),
                browser_result=result,
                entry=entry,
            )
            if inspection_requires_source_binding_block(inspection):
                blocked_result = block_structurally_valid_unbound_vsdx(
                    result,
                    entry,
                    inspection,
                    args=args,
                )
                blocked.append(blocked_result)
                remove_staging_receipt(args.progress, str(entry["artifact_id"]))
                continue
            prior = seen_hashes.get(inspection["sha256"])
            if prior and prior != entry["artifact_id"]:
                raise BatchError(
                    f"same batch produced an identical SHA-256 for two artifacts: {prior}, {entry['artifact_id']}"
                )
            seen_hashes[inspection["sha256"]] = str(entry["artifact_id"])
            completed_result = finalize_result(result, entry, args=args)
            completed.append(completed_result)
            remove_staging_receipt(args.progress, str(entry["artifact_id"]))
        except Exception as exc:
            pending.append(
                {
                    **result,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "stage": "validate_or_archive",
                }
            )
    audit = run_json(
        [
            sys.executable,
            str(ARCHIVE_STATE),
            "audit",
            "--plan",
            str(args.plan),
            "--progress",
            str(args.progress),
        ]
    )
    refreshed_progress = load_json(args.progress)
    if args.progress_mirror:
        write_progress_mirror(
            args.progress_mirror,
            plan=plan,
            progress=refreshed_progress,
            run_id=args.progress.parent.parent.name,
        )
    lifecycle_ok = (
        browser_receipt["worker_pages_opened"] == browser_receipt["worker_pages_closed"]
        and browser_receipt["scoped_pages_opened"] == browser_receipt["scoped_pages_closed"]
        and browser_receipt["pages_closed_at_exit"] == 0
    )
    status = "completed" if not pending and not blocked and lifecycle_ok else "partial"
    payload = {
        "schema_version": 1,
        "status": status,
        "selected": len(selected),
        "deferred_collision_count": len(deferred_collisions),
        "authorized_collision_count": len(collision_confirmations),
        "legacy_flat_download_review": legacy_review,
        "reconciled_count": len(reconciled),
        "reconciled": reconciled,
        "unique_block_recovered_count": len(unique_block_recovered),
        "unique_block_recovered": unique_block_recovered,
        "unique_block_recovery_error_count": len(unique_block_recovery_errors),
        "unique_block_recovery_errors": unique_block_recovery_errors,
        "confirmed_block_recovered_count": len(confirmed_block_recovered),
        "confirmed_block_recovered": confirmed_block_recovered,
        "confirmed_block_recovery_error_count": len(confirmed_block_recovery_errors),
        "confirmed_block_recovery_errors": confirmed_block_recovery_errors,
        "staging_recovered_count": len(staging_recovered),
        "staging_recovered": staging_recovered,
        "staging_recovery_error_count": len(staging_recovery_errors),
        "staging_recovery_errors": staging_recovery_errors,
        "completed_count": len(completed),
        "blocked_count": len(blocked),
        "pending_count": len(pending),
        "retry_source_binding_count": len(retry_source_bound_ids),
        "retry_source_bound_artifact_ids": retry_source_bound_ids,
        "empty_source_binding_count": len(empty_source_bound_ids),
        "empty_source_bound_artifact_ids": empty_source_bound_ids,
        "security_redaction_preview": security_redaction_preview,
        "security_redacted": security_redacted,
        "security_redaction_errors": security_redaction_errors,
        "workers": args.workers,
        "concurrency_proof": str(args.concurrency_proof) if proof else None,
        "browser_receipt": browser_receipt,
        "completed": completed,
        "blocked": blocked,
        "pending": pending,
        "audit": audit,
        "created_at": utc_now(),
    }
    payload["receipt_file"] = str(write_receipt(args.receipt_dir, payload))
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--team-url", required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--manifest-dir", type=Path)
    parser.add_argument("--source-links", type=Path)
    parser.add_argument("--progress-mirror", type=Path)
    parser.add_argument("--concurrency-proof", type=Path)
    parser.add_argument(
        "--collision-confirmation",
        type=Path,
        help=(
            "Plan-bound private inventory-order confirmation for duplicate-title artifacts; "
            "requires --workers 1 and runs as a dedicated flow."
        ),
    )
    parser.add_argument("--lock-file", type=Path)
    parser.add_argument("--receipt-dir", type=Path)
    parser.add_argument(
        "--download-dir",
        type=Path,
        help="Override the configured managed staging prefix; a run-id subdirectory is added.",
    )
    parser.add_argument("--profile-dir", type=Path, default=default_profile_dir())
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--settle-ms", type=int, default=3_000)
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry only the explicitly named current failed artifacts; never retries the whole queue.",
    )
    parser.add_argument(
        "--retry-blocked",
        action="store_true",
        help=(
            "Retry only explicitly named current blocked artifacts after the blocking "
            "condition has materially changed; never retries the whole blocked queue."
        ),
    )
    parser.add_argument(
        "--artifact-id",
        action="append",
        default=[],
        help="One exact current-plan artifact id; required with --retry-failed and repeatable.",
    )
    parser.add_argument(
        "--allow-verified-empty-source",
        action="append",
        default=[],
        help=(
            "One exact blocked artifact id whose audited source URL and live editor "
            "must both prove a zero-shape canvas; requires --retry-blocked and the "
            "same --artifact-id."
        ),
    )
    parser.add_argument(
        "--redact-security-block",
        action="append",
        default=[],
        help=(
            "One exact security-blocked VSDX artifact id to archive as a same-format "
            "sanitized derivative while retaining the original in restricted quarantine; "
            "requires --retry-blocked and the same --artifact-id."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--recover-existing-only",
        action="store_true",
        help=(
            "Revalidate and finalize only journal/diagnostic-bound existing files; "
            "do not launch a browser or download new files."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= MAX_WORKERS:
        parser.error(f"--workers must be within 1..{MAX_WORKERS}")
    if not 1 <= args.limit <= MAX_BATCH:
        parser.error(f"--limit must be within 1..{MAX_BATCH}")
    if not 250 <= args.timeout_ms <= 300_000:
        parser.error("--timeout-ms must be within 250..300000")
    if not 0 <= args.settle_ms <= 30_000:
        parser.error("--settle-ms must be within 0..30000")
    if args.dry_run and args.recover_existing_only:
        parser.error("--dry-run and --recover-existing-only are mutually exclusive")
    if args.redact_security_block and args.recover_existing_only:
        parser.error(
            "--redact-security-block and --recover-existing-only are mutually exclusive"
        )
    try:
        args.profile_dir = validate_profile_dir(args.profile_dir)
        settings = load_settings(
            config=args.config,
            temp_dir=args.download_dir,
            output_dir=args.output_root,
            manifest_dir=args.manifest_dir,
        )
        args.managed_temp_root = settings.temp_dir
        args.output_root = settings.output_dir
        args.manifest_dir = settings.manifest_dir
        run_id = args.progress.expanduser().resolve(strict=False).parent.parent.name
        if not run_id or run_id in {".", ".."} or "/" in run_id or "\\" in run_id:
            raise BatchError("cannot derive a safe run id from --progress")
        args.download_dir = args.managed_temp_root / run_id
        args.receipt_dir = (
            args.receipt_dir
            or args.progress.expanduser().resolve(strict=False).parent / "batch-receipts"
        )
        args.lock_file = (
            args.lock_file
            or args.progress.expanduser().resolve(strict=False).parent / ".archive-orchestrator.lock"
        )
        if not args.dry_run:
            ensure_paths(settings)
        with exclusive_lock(args.lock_file):
            payload = cmd_run(args)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["status"] in {
            "completed",
            "dry-run",
            "nothing_to_do",
            "collision_confirmation_required",
        } else 1
    except (BatchError, BrowserRunnerError, DownloadError, OSError, ValueError) as exc:
        payload = {
            "schema_version": 1,
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "created_at": utc_now(),
        }
        try:
            payload["receipt_file"] = str(write_receipt(args.receipt_dir, payload))
        except Exception:
            pass
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
