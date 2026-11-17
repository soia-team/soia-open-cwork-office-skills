#!/usr/bin/env python3
"""archive.py — 发布后归档：更新 frontmatter 状态与发布元信息（对齐 vault 写作流规范）。

soia-media-publish-wechat-draft 的归档器。
用法：
  python3 archive.py --article <md> --url <link> [--date YYYY-MM-DD] [--platform 公众号] [--account <alias>]
"""
from __future__ import annotations

import argparse
from datetime import date
import os
import re
import shutil
import sys
from pathlib import Path

LOCAL_ASSET_ROOTS = ("assets", "_attachments", "attachments")


def _update_inline_tags(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        raw_items = [item.strip() for item in stripped[1:-1].split(",") if item.strip()]
        filtered = [item for item in raw_items if item not in {"草稿", "待审核", "已发布"}]
        return "[" + ", ".join(["已发布", *filtered]) + "]"
    return "[已发布]"


def update_frontmatter(
    text: str,
    *,
    url: str,
    pub_date: str,
    platform: str,
    account: str | None = None,
) -> str:
    normalized = text.replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n?", normalized, re.DOTALL)
    if not m:
        lines = [
            "---",
            "tags: [已发布]",
            "status: 已发布",
            f"published_at: {pub_date}",
            f"platform: {platform}",
            f"url: {url}",
        ]
        if account:
            lines.append(f"wechat_account: {account}")
        lines.append("---")
        return "\n".join(lines) + "\n\n" + normalized

    fm_lines = m.group(1).splitlines()
    body = normalized[m.end():]

    updates: dict[str, str] = {
        "status": "已发布",
        "published_at": pub_date,
        "platform": platform,
        "url": url,
    }
    if account:
        updates["wechat_account"] = account

    seen_keys: set[str] = set()
    new_fm_lines: list[str] = []
    for line in fm_lines:
        if ":" in line and not line.startswith((" ", "\t", "-")):
            key, raw_val = line.split(":", 1)
            k = key.strip()
            if k == "tags":
                seen_keys.add("tags")
                new_fm_lines.append(f"tags: {_update_inline_tags(raw_val)}")
                continue
            if k in updates:
                seen_keys.add(k)
                new_fm_lines.append(f"{k}: {updates[k]}")
                continue
        new_fm_lines.append(line)

    for key, value in updates.items():
        if key not in seen_keys:
            new_fm_lines.append(f"{key}: {value}")

    return "---\n" + "\n".join(new_fm_lines) + "\n---\n" + body


def _extract_archive_title(text: str, fallback_stem: str) -> str:
    m = re.match(r"^---\n(.*?)\n---\n?", text.replace("\r\n", "\n"), re.DOTALL)
    body = text
    if m:
        body = text[m.end():]
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith((" ", "\t", "-")):
                k, raw_v = line.split(":", 1)
                if k.strip() == "title":
                    val = raw_v.strip().split(" #", 1)[0].strip().strip('"').strip("'")
                    if val:
                        return val
    h1 = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    if h1:
        return h1.group(1).strip()
    return re.sub(r"^\d{4}-\d{2}-\d{2}-", "", fallback_stem).strip() or fallback_stem


def _safe_filename_part(title: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "-", title).strip()
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned or "untitled"


def _rebase_relative_links(text: str, src_dir: Path, dst_dir: Path) -> str:
    if src_dir.resolve() == dst_dir.resolve():
        return text

    def _rebase(raw_rel: str) -> str:
        rel = raw_rel.strip()
        if not rel or rel.startswith(("http://", "https://", "data:", "/", "#")):
            return raw_rel
        candidate = (src_dir / rel).resolve()
        if not candidate.exists():
            return raw_rel
        return Path(os.path.relpath(candidate, dst_dir.resolve())).as_posix()

    normalized = text.replace("\r\n", "\n")
    fm_match = re.match(r"^(---\n)(.*?)(\n---\n?)", normalized, re.DOTALL)
    if fm_match:
        fm_lines = []
        for line in fm_match.group(2).splitlines():
            if line.startswith("cover:"):
                key, val = line.split(":", 1)
                raw_val = val.strip().strip('"').strip("'")
                rebased = _rebase(raw_val)
                fm_lines.append(f"{key}: {rebased}" if rebased != raw_val else line)
            else:
                fm_lines.append(line)
        normalized = fm_match.group(1) + "\n".join(fm_lines) + fm_match.group(3) + normalized[fm_match.end():]

    return re.sub(
        r"(!\[[^\]]*\]\()([^)]+)(\))",
        lambda m: f"{m.group(1)}{_rebase(m.group(2))}{m.group(3)}",
        normalized,
    )


def _collect_local_asset_rels(text: str) -> list[str]:
    rels: list[str] = []
    normalized = text.replace("\r\n", "\n")
    fm_match = re.match(r"^(---\n)(.*?)(\n---\n?)", normalized, re.DOTALL)
    if fm_match:
        for line in fm_match.group(2).splitlines():
            if line.startswith("cover:"):
                _, val = line.split(":", 1)
                raw_val = val.strip().strip('"').strip("'")
                if raw_val and not raw_val.startswith(("http://", "https://", "data:", "/", "#")):
                    rels.append(raw_val)
    for m in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", normalized):
        raw_val = m.group(1).strip()
        if raw_val and not raw_val.startswith(("http://", "https://", "data:", "/", "#")):
            rels.append(raw_val)
    return rels


def _prune_empty_dirs(start_dir: Path, stop_dir: Path) -> None:
    cur = start_dir.resolve()
    stop = stop_dir.resolve()
    while cur != stop and stop in cur.parents:
        try:
            cur.rmdir()
        except OSError:
            break
        cur = cur.parent


def _migrate_local_assets_on_move(text: str, src_dir: Path, dst_dir: Path) -> None:
    if src_dir.resolve() == dst_dir.resolve():
        return
    src_root = src_dir.resolve()
    dst_root = dst_dir.resolve()
    for rel in _collect_local_asset_rels(text):
        rel_path = Path(rel)
        if not rel_path.parts or rel_path.parts[0] not in LOCAL_ASSET_ROOTS:
            continue
        src_asset = (src_root / rel_path).resolve()
        if src_root not in src_asset.parents or not src_asset.is_file():
            continue
        dst_asset = (dst_root / rel_path).resolve()
        dst_asset.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_asset), str(dst_asset))
        _prune_empty_dirs(src_asset.parent, src_root)


def compute_archive_path(src_path: Path, text: str, archive_dir: Path, pub_date: str, platform: str) -> Path:
    date_match = re.match(r"^(\d{4})-(\d{2})-\d{2}$", pub_date.strip())
    if not date_match:
        raise ValueError(f"发布日期格式需为 YYYY-MM-DD：{pub_date!r}")
    year, month = date_match.group(1), date_match.group(2)
    title = _extract_archive_title(text, src_path.stem)
    safe_title = _safe_filename_part(title)
    safe_platform = _safe_filename_part(platform)
    return archive_dir / year / month / f"{pub_date}-{safe_platform}-{safe_title}.md"


def main() -> int:
    ap = argparse.ArgumentParser(description="更新已发布文章的 frontmatter（status/published_at/platform/url）")
    ap.add_argument("--article", required=True, help="文章 Markdown 路径")
    ap.add_argument("--url", default="", help="已发布文章链接")
    ap.add_argument("--date", help="发布日期 YYYY-MM-DD（默认当天）")
    ap.add_argument("--platform", default="公众号", help="发布平台，默认 公众号")
    ap.add_argument("--account", help="发布公众号账号标识（多账号时记录）")
    ap.add_argument("--archive-dir", help="按 YYYY/MM/YYYY-MM-DD-<平台>-<标题>.md 写入发布归档目录")
    ap.add_argument("--move", action="store_true", help="配合 --archive-dir 使用：写入归档目录后移除源草稿文件")
    ap.add_argument("--force", action="store_true", help="配合 --archive-dir 使用：若目标文件已存在则允许覆盖")
    args = ap.parse_args()

    path = Path(args.article).expanduser().resolve()
    pub_date = args.date or date.today().isoformat()
    updated = update_frontmatter(
        path.read_text(encoding="utf-8"),
        url=args.url,
        pub_date=pub_date,
        platform=args.platform,
        account=args.account,
    )

    if args.archive_dir:
        archive_root = Path(args.archive_dir).expanduser().resolve()
        dest_path = compute_archive_path(path, updated, archive_root, pub_date, args.platform)
        if dest_path.exists() and dest_path.resolve() != path and not args.force:
            print(f"❌ 归档目标已存在（加 --force 可覆盖）：{dest_path}", file=sys.stderr)
            return 1
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if args.move and dest_path.resolve() != path:
            _migrate_local_assets_on_move(updated, path.parent, dest_path.parent)
            final_text = _rebase_relative_links(updated, path.parent, dest_path.parent)
            dest_path.write_text(final_text, encoding="utf-8")
            path.unlink()
            companion_html = path.with_suffix(".html")
            if companion_html.is_file():
                companion_html.unlink()
            print(f"✅ 已移动归档：{dest_path} → status: 已发布，published_at: {pub_date}，platform: {args.platform}")
        else:
            final_text = _rebase_relative_links(updated, path.parent, dest_path.parent)
            dest_path.write_text(final_text, encoding="utf-8")
            print(f"✅ 已复制归档：{dest_path} → status: 已发布，published_at: {pub_date}，platform: {args.platform}")
        return 0

    path.write_text(updated, encoding="utf-8")
    print(f"✅ 已归档：{path.name} → status: 已发布，published_at: {pub_date}，platform: {args.platform}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
