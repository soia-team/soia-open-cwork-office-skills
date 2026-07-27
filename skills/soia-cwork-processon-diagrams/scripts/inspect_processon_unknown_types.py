#!/usr/bin/env python3
"""Resolve planned ProcessOn unknown types from fixed provider row icons."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from processon_archive_batch import (
    BatchError,
    async_safe_close_page,
    async_target_accessible,
    find_title,
    load_json,
    navigate_directory,
    sha256,
    validate_plan,
)
from processon_browser_runner import (
    default_profile_dir,
    ensure_dedicated_profile,
    validate_processon_url,
    validate_profile_dir,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def observe_type(page: Any, entry: dict[str, Any], timeout_ms: int) -> str:
    title = str(entry["title"])
    try:
        title_node = await find_title(
            page,
            title,
            timeout_ms,
            owner=str(entry.get("owner", "")),
            remote_updated_at=str(entry.get("remote_updated_at", "")),
        )
    except BatchError:
        # Metadata may drift, but an exact title must still identify one row.
        title_node = await find_title(page, title, timeout_ms)
    row = title_node.locator(
        "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' file_list_item ')][1]"
    )
    if await row.count() != 1:
        raise BatchError(f"unknown type row is unavailable: {title!r}")
    flowchart = bool(await row.locator(".icon-a-444_huaban1").count())
    mindmap = bool(await row.locator(".icon-a-siweidaotu1_huaban1").count())
    if flowchart == mindmap:
        raise BatchError(f"unknown type row has ambiguous provider icons: {title!r}")
    return "flowchart" if flowchart else "mindmap"


async def inspect(args: argparse.Namespace) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise BatchError("missing Playwright; install playwright and Chromium") from exc

    plan = load_json(args.plan)
    progress = load_json(args.progress)
    validate_plan(plan, progress)
    validate_processon_url(args.team_url)
    entries = [
        entry
        for entry in plan["entries"]
        if entry.get("type") == "unknown" or entry.get("confirmation_required")
    ]
    profile = ensure_dedicated_profile(args.profile_dir)
    observed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    async with async_playwright() as playwright:
        kwargs = {
            "headless": True,
            "accept_downloads": False,
            "viewport": {"width": 1440, "height": 1000},
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        try:
            context = await playwright.chromium.launch_persistent_context(
                str(profile), channel="chrome", **kwargs
            )
        except Exception:
            context = await playwright.chromium.launch_persistent_context(
                str(profile), **kwargs
            )
        page = await context.new_page()
        try:
            for entry in entries:
                try:
                    await navigate_directory(
                        page,
                        team_url=args.team_url,
                        root_path=str(plan["root_path"]),
                        source_directory=str(entry["source_directory"]),
                        settle_ms=args.settle_ms,
                        timeout_ms=args.timeout_ms,
                    )
                    if not await async_target_accessible(page, args.team_url):
                        raise BatchError("dedicated ProcessOn profile is not logged in")
                    observed.append(
                        {
                            "artifact_id": str(entry["artifact_id"]),
                            "source_directory": str(entry["source_directory"]),
                            "title": str(entry["title"]),
                            "observed_type": await observe_type(
                                page, entry, args.timeout_ms
                            ),
                            "evidence": "fixed_provider_row_icon",
                        }
                    )
                except Exception as exc:
                    errors.append(
                        {
                            "artifact_id": str(entry.get("artifact_id", "")),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
        finally:
            await async_safe_close_page(page)
            for stale in list(context.pages):
                await async_safe_close_page(stale)
            await context.close()
    return {
        "schema_version": 1,
        "kind": "processon_unknown_type_observation",
        "plan_sha256": sha256(args.plan.expanduser().resolve(strict=True)),
        "observed": observed,
        "errors": errors,
        "created_at": utc_now(),
    }


def write_output(path: Path, payload: dict[str, Any]) -> Path:
    target = path.expanduser().resolve(strict=False)
    if target.is_symlink():
        raise BatchError(f"unknown type output must not be a symlink: {target}")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--team-url", required=True)
    parser.add_argument("--profile-dir", type=Path, default=default_profile_dir())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--settle-ms", type=int, default=1_000)
    args = parser.parse_args()
    args.profile_dir = validate_profile_dir(args.profile_dir)
    payload = asyncio.run(inspect(args))
    target = write_output(args.output, payload)
    print(
        json.dumps(
            {
                "status": "completed" if not payload["errors"] else "partial",
                "path": str(target),
                "observed_count": len(payload["observed"]),
                "error_count": len(payload["errors"]),
            },
            ensure_ascii=False,
        )
    )
    return 0 if not payload["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
