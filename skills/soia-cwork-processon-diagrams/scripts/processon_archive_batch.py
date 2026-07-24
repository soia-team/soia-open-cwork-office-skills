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
from urllib.parse import urlparse
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


SCRIPT_DIR = Path(__file__).resolve().parent
ARCHIVE_STATE = SCRIPT_DIR / "processon_archive_state.py"
FINALIZER = SCRIPT_DIR / "finalize_processon_download.py"
MAX_WORKERS = 3
MAX_BATCH = 60
READY_ATTEMPTS = 2
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
VSDX_DOWNLOAD_MENU_CANDIDATES = (
    "导出全部画布 （.vsdx）",
    "导出全部画布 (.vsdx)",
    "VISIO文件",
    "VISIO文件 beta",
)
EDITOR_FILE_MENU = "文件"
EDITOR_EXPORT_MENU = "导出为"
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
    ),
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
    artifact_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Choose normal pending work or a caller-whitelisted failed retry.

    A failed retry is deliberately not a queue-wide switch: the caller must
    name every planned artifact.  This preserves the immutable failure
    evidence and prevents a transient UI change from turning into a blind
    retry storm.
    """

    requested_ids = [str(item).strip() for item in artifact_ids or []]
    if requested_ids and not retry_failed:
        raise BatchError("--artifact-id requires --retry-failed")
    if retry_failed and not requested_ids:
        raise BatchError("--retry-failed requires one or more --artifact-id values")
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

    done = progress_done_ids(progress)
    if retry_failed:
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
    plan: dict[str, Any], progress: dict[str, Any]
) -> list[dict[str, Any]]:
    done = progress_done_ids(progress)
    return [
        entry
        for entry in plan["entries"]
        if str(entry.get("artifact_id", "")) not in done
        and not entry.get("confirmation_required")
        and entry.get("type") != "unknown"
        and entry.get("collision_risk") not in {None, "", "none_detected"}
    ]


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


async def find_title(page: Any, title: str, timeout_ms: int) -> Any:
    deadline = time.monotonic() + timeout_ms / 1000
    previous_marker: tuple[int, str] | None = None
    unchanged = 0
    while time.monotonic() < deadline:
        try:
            # A folder title is also present in the breadcrumb.  Only accept a
            # title that belongs to a concrete ProcessOn list row; otherwise a
            # same-named folder can be clicked instead of the requested file.
            candidates = page.get_by_text(title, exact=True).filter(visible=True)
            candidate_count = min(await candidates.count(), 32)
            for index in range(candidate_count):
                locator = candidates.nth(index)
                if not await locator.is_visible():
                    continue
                row = locator.locator(
                    "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' file_list_item ')][1]"
                )
                if await row.count() and await row.is_visible():
                    return locator
        except Exception:
            pass
        marker = (
            int(await page.evaluate("() => Math.round(window.scrollY || 0)")),
            (await page.locator("body").inner_text())[-500:],
        )
        unchanged = unchanged + 1 if marker == previous_marker else 0
        previous_marker = marker
        if unchanged >= 2:
            break
        await scroll_processon_file_list(page)
        await page.wait_for_timeout(350)
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
        "source_url": str(result["source_url"]),
        "source_title": str(result["source_title"]),
        "remote_id": str(result["remote_id"]),
        "download_menu": str(result.get("download_menu", "")),
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
    return {
        "artifact_id": artifact_id,
        "source_path": str(entry["source_path"]),
        "title": str(entry["title"]),
        "requested_format": str(entry["primary_format"]),
        "source_url": source_url,
        "source_title": source_title,
        "remote_id": remote_id,
        "download_menu": str(payload.get("download_menu", "")),
        "download": {
            "path": str(source),
            "bytes": source.stat().st_size,
            "suggested_filename": suggested,
        },
        "ok": True,
    }


def download_menu_candidates(entry: dict[str, Any]) -> list[str]:
    """Return ordered, exact ProcessOn menu labels for the requested format."""

    primary_format = str(entry.get("primary_format") or "").strip().lower()
    primary_menu = str(entry.get("primary_menu") or "").strip()
    raw_candidates: list[str]
    if primary_format == "vsdx":
        raw_candidates = [*VSDX_DOWNLOAD_MENU_CANDIDATES, primary_menu]
    else:
        raw_candidates = [primary_menu]
    candidates: list[str] = []
    for label in raw_candidates:
        if label and label not in candidates:
            candidates.append(label)
    if not candidates:
        raise BatchError(
            f"archive entry has no download menu for format {primary_format or '<missing>'}"
        )
    return candidates


def semantic_control_locators(page: Any, label: str) -> list[Any]:
    """Return fixed, provider-controlled semantic locators for a menu label.

    The browser runner exposes visible text plus standard accessible/title
    attributes.  Keep the batch executor aligned without accepting arbitrary
    selectors: attribute selectors are an allowlist for known ProcessOn menu
    labels only, while every caller-provided plan label remains text-only.
    """

    locators = [page.get_by_text(label, exact=True).filter(visible=True).nth(0)]
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
    page: Any, title: str, timeout_ms: int, receipt: BrowserReceipt
) -> tuple[Any, Any | None]:
    """Open one listed document, accepting an in-page editor or a popup.

    ProcessOn uses both behaviours across team-space views. Bind the listener
    to this worker page, then poll for an in-page editor navigation so workers
    never cross-capture each other's transient pages.
    """

    title_locator = await find_title(page, title, timeout_ms)
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
    }
    try:
        source_page, popup = await open_source_editor(page, title, timeout_ms, receipt)
        source_url = validate_processon_url(source_page.url)
        source_title = await wait_for_source_title(source_page, title, timeout_ms)
        remote_id = verify_source_identity(entry, source_url)
        result["source_url"] = source_url
        result["source_title"] = source_title
        result["remote_id"] = remote_id

        menu_label, menu = await open_editor_export_menu(source_page, entry, timeout_ms)
        result["download_menu"] = menu_label
        async with source_page.expect_download(timeout=max(timeout_ms, 60_000)) as download_info:
            await menu.click(timeout=timeout_ms)
        download = await download_info.value
        suggested = download.suggested_filename
        expected_suffix = f".{entry['primary_format'].lower()}"
        if Path(suggested).suffix.lower() != expected_suffix:
            raise BatchError(
                f"download suffix mismatch for {title!r}: expected {expected_suffix}, got {suggested!r}"
            )
        if Path(suggested).stem not in {title, provider_safe_filename_stem(title)}:
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
                    # A title can navigate this same worker into the official
                    # editor. Rebuild the approved directory view before each
                    # artifact instead of relying on history/back semantics.
                    await navigate_directory(
                        page,
                        team_url=team_url,
                        root_path=str(plan["root_path"]),
                        source_directory=directory,
                        settle_ms=settle_ms,
                        timeout_ms=timeout_ms,
                    )
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
    root_title = xmind_topic_title(content[0].get("rootTopic"))
    if root_title != title:
        raise BatchError(f"XMind root title mismatch: expected {title!r}, got {root_title!r}")
    return {
        "kind": "xmind",
        "package_source": "content.json",
        "root_title": root_title,
        "semantic_status": "matched",
    }


def inspect_download(path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    actual = str(entry["primary_format"]).lower()
    if actual == "vsdx":
        inspection = inspect_vsdx(path, str(entry["title"]))
    elif actual == "xmind":
        inspection = inspect_xmind(path, str(entry["title"]))
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
        f"actual_format: {yaml_string(entry['primary_format'])}",
        f"download_menu: {yaml_string(browser_result.get('download_menu', ''))}",
        "fallback_used: false",
        f"file: {yaml_string(Path(finalized['destination']).name)}",
        f"bytes: {int(inspection['bytes'])}",
        f"sha256: {yaml_string(inspection['sha256'])}",
        f"finalizer_manifest: {yaml_string(finalized['manifest'])}",
        "inspection:",
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
    lines.extend(
        [
            f"verification: {yaml_string('浏览器弹页标题、源 URL、下载文件名、文件结构与文件内标题信号均已核对；归档 SHA-256 与下载文件一致。')}",
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


def reconcile_existing(
    plan: dict[str, Any], progress: dict[str, Any], *, args: argparse.Namespace
) -> list[dict[str, Any]]:
    """Finish a prior half-commit when metadata and finalizer evidence agree."""

    done = progress_done_ids(progress)
    recovered: list[dict[str, Any]] = []
    for entry in plan["entries"]:
        artifact_id = str(entry.get("artifact_id", ""))
        if (
            not artifact_id
            or artifact_id in done
            or entry.get("confirmation_required")
            or entry.get("collision_risk") not in {None, "", "none_detected"}
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
        recovered.append(
            {
                "artifact_id": artifact_id,
                "status": "reconciled",
                "destination": str(destination),
                "metadata": str(metadata_path),
                "manifest": str(manifest_path),
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
    inspection = inspect_download(source, entry)
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
            str(entry["primary_format"]),
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
            or entry.get("collision_risk") not in {None, "", "none_detected"}
        ):
            errors.append({"receipt": str(receipt_path), "error": "artifact_not_eligible_for_auto_recovery"})
            continue
        try:
            browser_result = load_staging_result(receipt_path, entry, args=args)
            inspection = inspect_download(Path(browser_result["download"]["path"]), entry)
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


def write_receipt(receipt_dir: Path, payload: dict[str, Any]) -> Path:
    receipt_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = receipt_dir / f"processon-archive-batch-{stamp}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    return target


def cmd_run(args: argparse.Namespace) -> dict[str, Any]:
    plan = load_json(args.plan)
    progress = load_json(args.progress)
    validate_plan(plan, progress)
    validate_processon_url(args.team_url)
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
    reconciled: list[dict[str, Any]] = []
    staging_recovered: list[dict[str, Any]] = []
    staging_recovery_errors: list[dict[str, str]] = []
    if not args.dry_run:
        staging_recovered, staging_recovery_errors = reconcile_staged_downloads(
            plan, progress, args=args
        )
        if staging_recovered:
            progress = load_json(args.progress)
        reconciled = reconcile_existing(plan, progress, args=args)
        if reconciled:
            progress = load_json(args.progress)
    legacy_review = legacy_flat_download_review(progress)
    deferred_collisions = deferred_collision_entries(plan, progress)
    selected = choose_entries(
        plan,
        progress,
        args.limit,
        workers=args.workers,
        retry_failed=args.retry_failed,
        artifact_ids=args.artifact_id,
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
            "status": "collision_confirmation_required" if deferred_collisions else "nothing_to_do",
            "selected": 0,
            "deferred_collision_count": len(deferred_collisions),
            "deferred_collision_artifact_ids": [
                str(item["artifact_id"]) for item in deferred_collisions
            ],
            "legacy_flat_download_review": legacy_review,
            "created_at": utc_now(),
            "reconciled": reconciled,
            "staging_recovered": staging_recovered,
            "staging_recovery_errors": staging_recovery_errors,
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
            "deferred_collision_count": len(deferred_collisions),
            "legacy_flat_download_review": legacy_review,
            "jobs": [
                {"source_directory": directory, "artifact_ids": [item["artifact_id"] for item in items]}
                for directory, items in build_jobs(selected, args.workers)
            ],
                "created_at": utc_now(),
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
    for result in results:
        if not result.get("ok"):
            pending.append(result)
            continue
        entry = selected_by_id[str(result["artifact_id"])]
        try:
            inspection = inspect_download(Path(result["download"]["path"]), entry)
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
        "legacy_flat_download_review": legacy_review,
        "reconciled_count": len(reconciled),
        "reconciled": reconciled,
        "staging_recovered_count": len(staging_recovered),
        "staging_recovered": staging_recovered,
        "staging_recovery_error_count": len(staging_recovery_errors),
        "staging_recovery_errors": staging_recovery_errors,
        "completed_count": len(completed),
        "blocked_count": len(blocked),
        "pending_count": len(pending),
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
        "--artifact-id",
        action="append",
        default=[],
        help="One exact current-plan artifact id; required with --retry-failed and repeatable.",
    )
    parser.add_argument("--dry-run", action="store_true")
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
