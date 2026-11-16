#!/usr/bin/env python3
"""archive.py — 发布后归档：更新 frontmatter 状态与发布元信息（对齐 vault 写作流规范）。

soia-media-publish-wechat-draft 的归档器。
用法：
  python3 archive.py --article <md> --url <link> [--date YYYY-MM-DD] [--platform 公众号] [--account <alias>]
"""
from __future__ import annotations

import argparse
from datetime import date
import re
from pathlib import Path


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


def main() -> int:
    ap = argparse.ArgumentParser(description="更新已发布文章的 frontmatter（status/published_at/platform/url）")
    ap.add_argument("--article", required=True, help="文章 Markdown 路径")
    ap.add_argument("--url", default="", help="已发布文章链接")
    ap.add_argument("--date", help="发布日期 YYYY-MM-DD（默认当天）")
    ap.add_argument("--platform", default="公众号", help="发布平台，默认 公众号")
    ap.add_argument("--account", help="发布公众号账号标识（多账号时记录）")
    args = ap.parse_args()

    path = Path(args.article)
    pub_date = args.date or date.today().isoformat()
    updated = update_frontmatter(
        path.read_text(encoding="utf-8"),
        url=args.url,
        pub_date=pub_date,
        platform=args.platform,
        account=args.account,
    )
    path.write_text(updated, encoding="utf-8")
    print(f"✅ 已归档：{path.name} → status: 已发布，published_at: {pub_date}，platform: {args.platform}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
