#!/usr/bin/env python3
"""publish.py — 渲染 + 红线校验 + 推微信公众号草稿箱（soia-media-publish-wechat-draft）。

支持单账号与多账号配置（`--account <alias>`），只建草稿（`draft/add`），绝不自动群发。
凭据与账号配置从私有 `config.yml` 或进程环境读取，不打印秘钥或 token。

用法：
  python3 publish.py --list-accounts
  python3 publish.py --check-auth [--account <alias>]
  python3 publish.py --article <md> [--cover <png>] [--account <alias>] [--dry-run]
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import mimetypes
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from publish_env import list_accounts, load_private_env, resolve_account_meta, resolve_credentials
import render_wechat
from validate_wechat_html import WeChatHTMLChecker

WECHAT_API_BASE = "https://api.weixin.qq.com/cgi-bin"
IMG_SRC_RE = re.compile(r'(<img\b[^>]*\bsrc=")([^"]+)(")', re.IGNORECASE)
FIRST_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
VAULT_ASSET_DIRS = ("assets", "_attachments", "attachments")


class WeChatAPIError(RuntimeError):
    """Raised when WeChat Official Account API returns an error payload."""

    def __init__(self, endpoint: str, errcode: int | None, errmsg: str) -> None:
        self.endpoint = endpoint
        self.errcode = errcode
        self.errmsg = errmsg
        hint = ""
        if errcode == 40164:
            hint = "（提示：请将当前调用机器的公网 IP 加入公众号后台「开发 -> 基本配置 -> IP白名单」）"
        elif errcode == 48001:
            hint = "（提示：该接口仅对已认证的订阅号/服务号开放，或当前账号未开通草稿箱/素材权限）"
        elif errcode == 40001:
            hint = "（提示：AppSecret 无效或已被重置，请核对私有 config.yml）"
        super().__init__(f"微信接口 {endpoint} 失败：errcode={errcode}, errmsg={errmsg}{hint}")


def _redact(text: str, secrets: list[str]) -> str:
    cleaned = text
    for secret in secrets:
        if secret and len(secret) >= 4:
            cleaned = cleaned.replace(secret, "<REDACTED>")
    cleaned = re.sub(r"(access_token=)[^&\s\"']+", r"\1<REDACTED>", cleaned)
    cleaned = re.sub(r"(secret=)[^&\s\"']+", r"\1<REDACTED>", cleaned)
    return cleaned


def _http_get_json(url: str, timeout: int = 30, secrets: list[str] | None = None) -> dict:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(_redact(f"请求微信接口失败：{exc}", secrets or [])) from exc
    return json.loads(raw)


def _http_post_json(url: str, payload: dict, timeout: int = 30, secrets: list[str] | None = None) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(_redact(f"请求微信接口失败：{exc}", secrets or [])) from exc
    return json.loads(raw)


def _encode_multipart(field_name: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----SoiaWeChatBoundary{uuid.uuid4().hex}"
    mime_type, _ = mimetypes.guess_type(str(file_path))
    content_type = mime_type or "application/octet-stream"
    file_bytes = file_path.read_bytes()
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{file_path.name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return header + file_bytes + footer, f"multipart/form-data; boundary={boundary}"


def _http_post_file(
    url: str,
    field_name: str,
    file_path: Path,
    timeout: int = 60,
    secrets: list[str] | None = None,
) -> dict:
    body, content_type = _encode_multipart(field_name, file_path)
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(_redact(f"上传媒体文件到微信失败（{file_path.name}）：{exc}", secrets or [])) from exc
    return json.loads(raw)


def _ensure_ok(endpoint: str, resp: dict) -> dict:
    errcode = resp.get("errcode")
    if errcode not in (None, 0):
        raise WeChatAPIError(endpoint, errcode, str(resp.get("errmsg", "")))
    return resp


def fetch_access_token(app_id: str, app_secret: str) -> str:
    query = urllib.parse.urlencode(
        {
            "grant_type": "client_credential",
            "appid": app_id,
            "secret": app_secret,
        }
    )
    url = f"{WECHAT_API_BASE}/token?{query}"
    resp = _ensure_ok("token", _http_get_json(url, secrets=[app_id, app_secret]))
    token = resp.get("access_token")
    if not token:
        raise RuntimeError("微信 token 接口未返回 access_token。")
    return str(token)


def upload_body_image(access_token: str, image_path: Path, secrets: list[str]) -> str:
    query = urllib.parse.urlencode({"access_token": access_token})
    url = f"{WECHAT_API_BASE}/media/uploadimg?{query}"
    resp = _ensure_ok("media/uploadimg", _http_post_file(url, "media", image_path, secrets=secrets))
    cdn_url = resp.get("url")
    if not cdn_url:
        raise RuntimeError(f"media/uploadimg 未返回 url（{image_path.name}）。")
    return str(cdn_url)


def upload_cover_material(access_token: str, cover_path: Path, secrets: list[str]) -> str:
    query = urllib.parse.urlencode({"access_token": access_token, "type": "image"})
    url = f"{WECHAT_API_BASE}/material/add_material?{query}"
    resp = _ensure_ok("material/add_material", _http_post_file(url, "media", cover_path, secrets=secrets))
    media_id = resp.get("media_id")
    if not media_id:
        raise RuntimeError(f"material/add_material 未返回 media_id（{cover_path.name}）。")
    return str(media_id)


def create_draft(access_token: str, article_payload: dict, secrets: list[str]) -> str:
    query = urllib.parse.urlencode({"access_token": access_token})
    url = f"{WECHAT_API_BASE}/draft/add?{query}"
    resp = _ensure_ok("draft/add", _http_post_json(url, {"articles": [article_payload]}, secrets=secrets))
    media_id = resp.get("media_id")
    if not media_id:
        raise RuntimeError("draft/add 未返回草稿 media_id。")
    return str(media_id)


def _find_obsidian_attachment(filename: str, base_dir: Path) -> Path | None:
    """Resolve short Obsidian embed names (`![[foo.png]]`) within local/ancestor asset dirs, fail-closed on ambiguity."""
    seen_roots: list[Path] = [base_dir]
    cur = base_dir
    for _ in range(4):
        if (cur / ".obsidian").is_dir() or (cur / ".git").is_dir():
            break
        parent = cur.parent
        if parent == cur:
            break
        seen_roots.append(parent)
        cur = parent

    matches: list[Path] = []
    seen_resolved: set[Path] = set()
    for root in seen_roots:
        for sub in VAULT_ASSET_DIRS:
            cand_dir = root / sub
            if not cand_dir.is_dir():
                continue
            for found in sorted(cand_dir.rglob(filename)):
                if found.is_file():
                    res = found.resolve()
                    if res not in seen_resolved:
                        seen_resolved.add(res)
                        matches.append(res)
        direct = root / filename
        if direct.is_file():
            res = direct.resolve()
            if res not in seen_resolved:
                seen_resolved.add(res)
                matches.append(res)

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        listed = "\n  - ".join(str(p) for p in matches[:5])
        raise ValueError(f"同名本地图片 {filename!r} 命中多处，请改用相对路径消除歧义：\n  - {listed}")
    return None


def _resolve_local_path(raw_path: str, base_dir: Path) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate
    rel_to_article = (base_dir / candidate).resolve()
    if rel_to_article.exists():
        return rel_to_article
    cwd_candidate = candidate.resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    if candidate.parent == Path("."):
        obs_hit = _find_obsidian_attachment(candidate.name, base_dir)
        if obs_hit is not None:
            return obs_hit
    return rel_to_article


def collect_local_images(html_text: str, base_dir: Path) -> list[tuple[str, Path]]:
    seen: dict[str, Path] = {}
    for match in IMG_SRC_RE.finditer(html_text):
        raw_src = html_mod.unescape(match.group(2)).strip()
        if not raw_src or raw_src.startswith(("http://", "https://", "data:")):
            continue
        if raw_src not in seen:
            seen[raw_src] = _resolve_local_path(raw_src, base_dir)
    return list(seen.items())


def replace_image_urls(html_text: str, url_map: dict[str, str]) -> str:
    def _sub(match: re.Match[str]) -> str:
        raw_src = html_mod.unescape(match.group(2)).strip()
        new_src = url_map.get(raw_src)
        if not new_src:
            return match.group(0)
        return f"{match.group(1)}{html_mod.escape(new_src, quote=True)}{match.group(3)}"

    return IMG_SRC_RE.sub(_sub, html_text)


def extract_article_meta(md_text: str, md_path: Path, args: argparse.Namespace) -> dict[str, str]:
    fm, body = render_wechat.parse_frontmatter(md_text)
    h1_match = FIRST_H1_RE.search(body)
    fallback_title = h1_match.group(1).strip() if h1_match else md_path.stem
    title = (args.title or fm.get("title") or fallback_title).strip()
    raw_digest = (args.digest or fm.get("digest") or fm.get("summary") or "").strip()
    if not raw_digest and fm.get("preface"):
        raw_digest = str(fm.get("preface") or "").strip()[:120]
    digest = raw_digest
    account = (args.account or fm.get("wechat_account") or fm.get("account") or "").strip()
    cover = (args.cover or fm.get("cover") or "").strip()
    account_author = ""
    if not (args.author or fm.get("author")):
        try:
            meta_hint = resolve_account_meta(
                account=account or None,
                config_file=getattr(args, "config", None),
            )
            account_author = meta_hint.get("author", "")
        except ValueError:
            account_author = ""
    author = (args.author or fm.get("author") or account_author).strip()
    theme = (getattr(args, "theme", None) or fm.get("wechat_theme") or fm.get("theme") or "default").strip().lower()
    return {
        "title": title,
        "author": author,
        "digest": digest,
        "account": account,
        "cover": cover,
        "theme": theme,
    }


def render_and_validate(
    md_text: str,
    md_path: Path,
    renderer: str,
    strip_h1: bool,
    html_out: str | None = None,
    theme: str | None = None,
) -> tuple[Path, str]:
    html_path = Path(html_out).expanduser().resolve() if html_out else md_path.with_suffix(".html")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    if renderer == "wechat":
        html_text = render_wechat.render(md_text, strip_h1=strip_h1, theme=theme)
        html_path.write_text(html_text, encoding="utf-8")
    else:
        subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "render.py"), str(md_path), str(html_path), "--style", "tech"],
            check=True,
        )
        html_text = html_path.read_text(encoding="utf-8")

    checker = WeChatHTMLChecker()
    checker.feed(html_text)
    checker.close()
    if checker.errors:
        details = "\n".join(
            f"  - line {e['line']}: [{e['type']}] {e['detail']}" for e in checker.errors
        )
        raise SystemExit(
            f"❌ 微信 HTML 红线校验失败（{len(checker.errors)} 处硬违规），已阻断推草稿箱：\n{details}"
        )
    if checker.warnings:
        print(f"⚠️ 红线校验通过，但有 {len(checker.warnings)} 条 warning（建议检查裸文字节点）。", file=sys.stderr)
    return html_path, html_text


def _print_accounts(config_file: str | None = None) -> int:
    accounts = list_accounts(config_file=config_file)
    if not accounts:
        print("当前未在 config.yml 或分账号 .yml 中发现命名账号配置（单账号可直接使用 `env.WECHAT_APP_ID` / `env.WECHAT_APP_SECRET`）。")
        return 0
    print("已配置公众号账号（已脱敏）：")
    for item in accounts:
        default_mark = " [默认]" if item["is_default"] else ""
        status_mark = "已配凭据" if item["configured"] else "缺凭据"
        display = item["name"] or item["alias"]
        wid = f" ({item['wechat_id']})" if item["wechat_id"] else ""
        src = f" <- {item['source']}" if item.get("source") else ""
        print(f"- {item['alias']}: {display}{wid} [{status_mark}]{default_mark}{src}")
    return 0


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="渲染、校验并推送文章到微信公众号草稿箱（支持多账号与多 yml 配置，绝不自动群发）"
    )
    ap.add_argument("--article", help="Markdown 文章路径")
    ap.add_argument("--cover", help="封面图片路径（PNG/JPG；推草稿箱时必填或由 frontmatter cover 指定）")
    ap.add_argument("--account", help="目标公众号账号 alias / 微信号 / 名称（多账号时必填或设 default_account）")
    ap.add_argument("--config", help="指定私有配置文件路径或同目录文件名（如 config.zbt.yml）")
    ap.add_argument("--title", help="覆盖文章标题（默认取 frontmatter title 或首个 # 标题）")
    ap.add_argument("--author", help="覆盖作者名")
    ap.add_argument("--digest", help="覆盖摘要（建议 ≤120 字）")
    ap.add_argument("--renderer", default="wechat", choices=["wechat", "legacy"], help="渲染器，默认 wechat")
    ap.add_argument("--theme", choices=["default", "minimal"], help="wechat 渲染排版主题：default（默认红黑强调）或 minimal（克制纯墨）")
    ap.add_argument("--html-out", help="指定渲染 HTML 输出路径（默认在源 Markdown 同目录生成同名 .html）")
    ap.add_argument("--keep-h1", action="store_true", help="保留正文开头首个 # 一级标题")
    ap.add_argument("--dry-run", action="store_true", help="只渲染、校验并检查本地图片/封面，不调微信 API")
    ap.add_argument("--list-accounts", action="store_true", help="列出已配置的公众号账号（含 config.yml 与分账号 yml）")
    ap.add_argument("--check-auth", action="store_true", help="仅测试换取 access_token 验证凭据与 IP 白名单")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    try:
        load_private_env(config_file=args.config)
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    if args.list_accounts:
        try:
            return _print_accounts(config_file=args.config)
        except ValueError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1

    if args.check_auth:
        try:
            app_id, app_secret, resolved_account = resolve_credentials(account=args.account, config_file=args.config)
            token = fetch_access_token(app_id, app_secret)
        except (ValueError, RuntimeError, WeChatAPIError) as exc:
            print(f"❌ 凭据自检失败：{exc}", file=sys.stderr)
            return 1
        label = resolved_account or "default/env"
        print(f"✅ 凭据自检通过：账号={label}，已成功换取 access_token（长度 {len(token)}，已脱敏）。")
        return 0

    if not args.article:
        print("❌ 请提供 --article <md>（或使用 --list-accounts / --check-auth）。", file=sys.stderr)
        return 2

    md_path = Path(args.article).expanduser().resolve()
    if not md_path.is_file():
        print(f"❌ 找不到文章文件：{md_path}", file=sys.stderr)
        return 1

    md_text = md_path.read_text(encoding="utf-8")
    meta = extract_article_meta(md_text, md_path, args)
    try:
        html_path, html_text = render_and_validate(
            md_text,
            md_path,
            renderer=args.renderer,
            strip_h1=not args.keep_h1,
            html_out=args.html_out,
            theme=meta["theme"],
        )
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    try:
        local_images = collect_local_images(html_text, md_path.parent)
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    missing_images = [p for _, p in local_images if not p.is_file()]
    if missing_images:
        missing_str = "\n".join(f"  - {p}" for p in missing_images)
        print(f"❌ 正文引用了不存在的本地图片：\n{missing_str}", file=sys.stderr)
        return 1

    cover_path: Path | None = None
    if meta["cover"]:
        try:
            cover_path = _resolve_local_path(meta["cover"], md_path.parent)
        except ValueError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1
        if not cover_path.is_file():
            print(f"❌ 找不到封面图片：{cover_path}", file=sys.stderr)
            return 1

    if args.dry_run:
        account_label = meta["account"] or "(未指定，推送时按配置解析)"
        author_part = f" | 作者={meta['author']}" if meta["author"] else ""
        theme_part = f" | 主题={meta['theme']}" if meta["theme"] != "default" else ""
        print(f"[dry-run] 渲染并校验通过：{html_path}")
        print(
            f"[dry-run] 标题={meta['title']!r}{author_part}{theme_part} | 账号={account_label} | "
            f"本地正文图={len(local_images)} 张 | 封面={cover_path or '未指定'}"
        )
        print("[dry-run] 未推送远端。确认参数与封面后去掉 --dry-run 即可建草稿。")
        return 0

    if cover_path is None:
        print("❌ 推微信公众号草稿箱必须提供封面图（--cover <png/jpg> 或 frontmatter cover）。", file=sys.stderr)
        return 1

    try:
        app_id, app_secret, resolved_account = resolve_credentials(
            account=meta["account"] or None,
            config_file=args.config,
        )
        secrets = [app_id, app_secret]
        access_token = fetch_access_token(app_id, app_secret)
        secrets.append(access_token)

        url_map: dict[str, str] = {}
        for raw_src, img_path in local_images:
            url_map[raw_src] = upload_body_image(access_token, img_path, secrets)
        final_html = replace_image_urls(html_text, url_map)
        html_path.write_text(final_html, encoding="utf-8")

        thumb_media_id = upload_cover_material(access_token, cover_path, secrets)
        article_payload: dict[str, object] = {
            "title": meta["title"],
            "content": final_html,
            "thumb_media_id": thumb_media_id,
            "need_open_comment": 0,
            "only_fans_can_comment": 0,
        }
        if meta["author"]:
            article_payload["author"] = meta["author"]
        if meta["digest"]:
            article_payload["digest"] = meta["digest"]

        draft_media_id = create_draft(access_token, article_payload, secrets)
    except (ValueError, RuntimeError, WeChatAPIError) as exc:
        print(f"❌ 推草稿箱失败：{exc}", file=sys.stderr)
        return 1

    account_display = resolved_account or "default/env"
    print(f"✅ 已推入微信公众号草稿箱：账号={account_display} | 标题={meta['title']!r} | media_id={draft_media_id}")
    print(f"   正文图上传={len(local_images)} 张 | HTML={html_path}")
    print("⚠️ 本脚本只建草稿；请在公众号后台预览排版无误后再手动群发，发布后运行 archive.py 留底。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
