#!/usr/bin/env python3
"""render_wechat.py — Markdown → 微信公众号安全 HTML（soia-media-publish-wechat-draft skill 机械层）

只使用公众号编辑器认可的标签/内联样式来渲染，逐条对齐 SKILL.md「微信平台红线」：
不出现 <div>/class/id/position/float/媒体查询/动画/CSS 变量/<style> 块；所有样式内联
写在 style=""；每个可见文字节点都套一层 <span>，避免复制粘贴时按标签边界掉样式。

渲染完成后务必接着跑 validate_wechat_html.py 做机械复核，通过（exit 0）了
再交给 publish.py 推草稿箱——本脚本自己不做校验，只负责生成。

支持的 Markdown 子集：
  # ~ ######  标题          → 内联样式 h1~h6（锚点层）
  **加粗**                   → 主题色加粗 <strong>（标记层）
  ==高亮==                   → 红色加粗 <strong>（标记层）
  *斜体* / ~~删除线~~        → <em> / <del>
  [文字](url) / [[双链]]     → 外链 <a> 或纯文本 <span>（清洗 Obsidian 双链）
  > 引用（连续行合并）        → 金句卡：内联样式 <section>（容器层）
  - / * 无序列表 / 1. 有序   → <ul> / <ol> + <li>，无 class/id
  | 表格 | ... |             → <section> 包裹内联样式 <table>
  --- / *** / ___ 分割线     → <section> 顶边框分割线
  `行内代码` / ```代码块```   → <code style="..."> / <section> 内嵌 <code>
  ![alt](url) / ![[img.png]]→ <img>；alt 非空则图下加一行说明文字
  普通段落                    → <p>

用法：
  python3 render_wechat.py --file article.md
  python3 render_wechat.py --file article.md --output article.html
  python3 render_wechat.py --file article.md --strip-h1
  cat article.md | python3 render_wechat.py

纯标准库实现（re/html/argparse），无第三方依赖。
"""
import argparse
import html as html_escape_mod
import re
import sys
from pathlib import Path

# ---- 主题色：Python 常量集中定义，不用 CSS 变量（微信平台红线禁用 var(--x)）----
THEME_COLOR = "#c0392b"        # 标记层 L1：**加粗** 短语
HIGHLIGHT_COLOR = "#e74c3c"    # 标记层 L2：==高亮== 结论/警告/关键数字
BODY_COLOR = "#333333"
QUOTE_BG = "#f9f5f0"
CODE_BG = "#f6f8fa"
CODE_COLOR = "#476582"
CAPTION_COLOR = "#888888"
BORDER_COLOR = "#e5e7eb"

THEMES = {
    "default": {
        "body_color": BODY_COLOR,
        "bold_color": THEME_COLOR,
        "highlight_color": HIGHLIGHT_COLOR,
        "link_color": THEME_COLOR,
        "quote_bg": QUOTE_BG,
        "quote_border": THEME_COLOR,
        "line_height": "1.75",
    },
    "minimal": {
        "body_color": "#262826",
        "bold_color": "#191c1b",
        "highlight_color": "#191c1b",
        "link_color": "#4a463f",
        "quote_bg": "#f7f5ef",
        "quote_border": "#8c8376",
        "line_height": "1.85",
    },
}

FONT_STACK = "-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif"
MONO_STACK = "Menlo,Consolas,monospace"

WRAPPER_STYLE = f"max-width:677px;margin:0 auto;font-family:{FONT_STACK};color:{BODY_COLOR};font-size:16px"
BODY_P_STYLE = f"line-height:1.75;margin:16px 0;color:{BODY_COLOR}"
H_STYLE = "font-weight:700;margin:28px 0 14px;line-height:1.4"
QUOTE_SECTION_STYLE = (
    f"margin:20px 0;padding:14px 18px;background:{QUOTE_BG};"
    f"border-left:3px solid {THEME_COLOR};color:{BODY_COLOR};line-height:1.7"
)
QUOTE_P_STYLE = "margin:0 0 6px"
LIST_STYLE = f"margin:16px 0;padding-left:22px;color:{BODY_COLOR};line-height:1.75"
LIST_ITEM_STYLE = "margin:6px 0"
CODE_INLINE_STYLE = (
    f"background:{CODE_BG};color:{CODE_COLOR};padding:2px 5px;border-radius:3px;"
    f"font-family:{MONO_STACK};font-size:14px"
)
CODE_BLOCK_SECTION_STYLE = f"margin:18px 0;padding:14px 16px;background:{CODE_BG};border-radius:6px;overflow-x:auto"
CODE_BLOCK_CODE_STYLE = f"color:{CODE_COLOR};font-family:{MONO_STACK};font-size:14px;line-height:1.6"
IMG_STYLE = "max-width:100%;display:block;margin:16px auto"
CAPTION_STYLE = f"text-align:center;font-size:13px;color:{CAPTION_COLOR};margin:-8px 0 16px"
HR_SECTION_STYLE = f"margin:24px 0;border-top:1px solid {BORDER_COLOR}"
TABLE_WRAP_STYLE = "margin:18px 0;overflow-x:auto"
TABLE_STYLE = f"width:100%;border-collapse:collapse;font-size:14px;line-height:1.6;color:{BODY_COLOR}"
TH_STYLE = (
    f"border:1px solid {BORDER_COLOR};padding:8px 10px;background:{QUOTE_BG};"
    f"font-weight:700;text-align:left"
)
TD_STYLE = f"border:1px solid {BORDER_COLOR};padding:8px 10px;text-align:left"
LINK_STYLE = f"color:{THEME_COLOR};text-decoration:underline"
DEL_STYLE = f"text-decoration:line-through;color:{CAPTION_COLOR}"

TABLE_SEP_RE = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$")
HR_RE = re.compile(r"^(?:---+|\*\*\*+|___+)$")
ORDERED_ITEM_RE = re.compile(r"^\d+\.\s+(.*)$")
UNORDERED_ITEM_RE = re.compile(r"^[-*]\s+(.*)$")
OBSIDIAN_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
OBSIDIAN_SIZE_RE = re.compile(r"^\d+(?:x\d+)?$", re.IGNORECASE)

INLINE_TOKEN_RE = re.compile(
    r"`([^`]+)`"
    r"|\[\[([^\]]+)\]\]"
    r"|\[([^\]]+)\]\(([^)]+)\)"
    r"|==(.+?)=="
    r"|\*\*(.+?)\*\*"
    r"|~~(.+?)~~"
    r"|(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)"
)


def esc(text):
    return html_escape_mod.escape(text, quote=False)


def esc_attr(text):
    return html_escape_mod.escape(text, quote=True)


def span(text, style=""):
    """每个文字节点强制套一层 <span>——公众号编辑器按标签边界取样式，裸文字最容易掉样式。"""
    return f'<span style="{style}">{esc(text)}</span>'


def parse_frontmatter(text):
    normalized = text.replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n?", normalized, re.DOTALL)
    fm, body = {}, normalized
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith((" ", "\t", "-")):
                k, v = line.split(":", 1)
                raw_val = v.strip()
                if len(raw_val) >= 2 and raw_val[0] in ('"', "'"):
                    quote_ch = raw_val[0]
                    end_idx = raw_val.find(quote_ch, 1)
                    val = raw_val[1:end_idx] if end_idx != -1 else raw_val.strip('"').strip("'")
                else:
                    val = raw_val.split(" #", 1)[0].strip().strip('"').strip("'")
                fm[k.strip()] = val
        body = normalized[m.end():]
    return fm, body


def _parse_image_line(stripped):
    """Parse standard `![alt](url)` or Obsidian `![[path|alt|size]]` standalone image lines."""
    m = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", stripped)
    if m:
        alt, url = m.group(1), m.group(2).strip()
        if url.startswith("<") and url.endswith(">") and len(url) >= 2:
            url = url[1:-1].strip()
        return url, alt

    if stripped.startswith("![[") and stripped.endswith("]]"):
        inner = stripped[3:-2].strip()
        segments = [seg.strip() for seg in inner.split("|")]
        target = segments[0] if segments else ""
        if not target:
            return None
        clean_target = target.split("#", 1)[0].strip()
        if Path(clean_target).suffix.lower() not in OBSIDIAN_IMG_EXTS:
            return None
        alt = ""
        for seg in segments[1:]:
            if seg and not OBSIDIAN_SIZE_RE.match(seg):
                alt = seg
                break
        return clean_target, alt

    return None


def _wikilink_label(raw_inner):
    if "|" in raw_inner:
        return raw_inner.split("|", 1)[1].strip()
    target = raw_inner.strip()
    if target.startswith("#"):
        return target.lstrip("#").strip()
    base = target.split("#", 1)[0].rsplit("/", 1)[-1].strip()
    return base or target


def _resolve_theme(fm, explicit_theme=None):
    name = (explicit_theme or fm.get("wechat_theme") or fm.get("theme") or "default").strip().lower()
    if name not in THEMES:
        supported = ", ".join(sorted(THEMES))
        raise ValueError(f"不支持的排版主题 {name!r}（可选：{supported}）。")
    return THEMES[name]


def render_inline(text, theme_cfg=None):
    """行内标记：`代码` / [[双链]] / [链接](url) / ==高亮== / **加粗** / ~~删除~~ / *斜体*；其余纯文本套 <span>。"""
    if text == "":
        return span("")
    cfg = theme_cfg or THEMES["default"]
    link_style = f"color:{cfg['link_color']};text-decoration:underline"
    out = []
    pos = 0
    for m in INLINE_TOKEN_RE.finditer(text):
        if m.start() > pos:
            out.append(span(text[pos:m.start()]))
        code, wiki, link_text, link_url, hi, bold, strike, italic = m.groups()
        if code is not None:
            out.append(f'<code style="{CODE_INLINE_STYLE}">{esc(code)}</code>')
        elif wiki is not None:
            out.append(span(_wikilink_label(wiki)))
        elif link_text is not None:
            url = (link_url or "").strip()
            if url.startswith(("http://", "https://")):
                out.append(f'<a href="{esc_attr(url)}" style="{link_style}">{span(link_text)}</a>')
            else:
                out.append(span(link_text))
        elif hi is not None:
            out.append(f'<strong style="color:{cfg["highlight_color"]}">{span(hi)}</strong>')
        elif bold is not None:
            out.append(f'<strong style="color:{cfg["bold_color"]}">{span(bold)}</strong>')
        elif strike is not None:
            out.append(f'<del style="{DEL_STYLE}">{span(strike)}</del>')
        else:
            out.append(f'<em style="font-style:italic">{span(italic)}</em>')
        pos = m.end()
    if pos < len(text):
        out.append(span(text[pos:]))
    return "".join(out) if out else span(text)


def _split_table_row(line):
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", row)]


def _render_table(headers, rows, theme_cfg=None):
    cfg = theme_cfg or THEMES["default"]
    th_cells = "".join(f'<th style="{TH_STYLE}">{render_inline(h, cfg)}</th>' for h in headers)
    thead = f"<thead><tr>{th_cells}</tr></thead>"
    body_rows = []
    for row in rows:
        padded = row + [""] * max(0, len(headers) - len(row))
        tds = "".join(f'<td style="{TD_STYLE}">{render_inline(cell, cfg)}</td>' for cell in padded[:len(headers)])
        body_rows.append(f"<tr>{tds}</tr>")
    tbody = f"<tbody>{''.join(body_rows)}</tbody>" if body_rows else ""
    table_style = f"width:100%;border-collapse:collapse;font-size:14px;line-height:1.6;color:{cfg['body_color']}"
    return (
        f'<section style="{TABLE_WRAP_STYLE}">'
        f'<table style="{table_style}">{thead}{tbody}</table>'
        f'</section>'
    )


def render(md_text, strip_h1=False, theme=None):
    fm, body = parse_frontmatter(md_text)
    cfg = _resolve_theme(fm, explicit_theme=theme)
    wrapper_style = f"max-width:677px;margin:0 auto;font-family:{FONT_STACK};color:{cfg['body_color']};font-size:16px"
    body_p_style = f"line-height:{cfg['line_height']};margin:16px 0;color:{cfg['body_color']}"
    quote_section_style = (
        f"margin:20px 0;padding:14px 18px;background:{cfg['quote_bg']};"
        f"border-left:3px solid {cfg['quote_border']};color:{cfg['body_color']};line-height:1.7"
    )
    list_style = f"margin:16px 0;padding-left:22px;color:{cfg['body_color']};line-height:{cfg['line_height']}"
    lines = body.splitlines()
    n = len(lines)
    parts = []
    i = 0
    seen_non_empty = False
    while i < n:
        raw_line = lines[i]
        stripped = raw_line.strip()

        # 围栏代码块（```...```）→ 内联样式 <section> 内嵌 <code>，逐行用 <br/> 连接
        if stripped.startswith("```"):
            seen_non_empty = True
            i += 1
            code_lines = []
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # 跳过收尾 ```
            rendered_lines = [esc(line) if line.strip() else "&nbsp;" for line in code_lines]
            code_html = "<br/>".join(rendered_lines)
            parts.append(
                f'<section style="{CODE_BLOCK_SECTION_STYLE}">'
                f'<code style="{CODE_BLOCK_CODE_STYLE}">{code_html}</code>'
                f'</section>'
            )
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", raw_line)
        if m:
            level = len(m.group(1))
            if strip_h1 and level == 1 and not seen_non_empty:
                seen_non_empty = True
                i += 1
                continue
            seen_non_empty = True
            content = render_inline(m.group(2).strip(), cfg)
            parts.append(f'<h{level} style="{H_STYLE}">{content}</h{level}>')
            i += 1
            continue

        # 分割线（--- / *** / ___）
        if HR_RE.match(stripped):
            seen_non_empty = True
            parts.append(f'<section style="{HR_SECTION_STYLE}"></section>')
            i += 1
            continue

        # Markdown 表格（表头 + 分隔行）
        if "|" in stripped and i + 1 < n and TABLE_SEP_RE.match(lines[i + 1].strip()):
            seen_non_empty = True
            headers = _split_table_row(stripped)
            i += 2
            rows = []
            while i < n and "|" in lines[i].strip() and lines[i].strip():
                rows.append(_split_table_row(lines[i].strip()))
                i += 1
            parts.append(_render_table(headers, rows, cfg))
            continue

        # 引用块（连续 > 行合并成一张金句卡）
        if stripped.startswith(">"):
            seen_non_empty = True
            quote_lines = []
            while i < n and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip().lstrip(">").strip())
                i += 1
            inner = "".join(
                f'<p style="{QUOTE_P_STYLE}">{render_inline(q, cfg)}</p>' for q in quote_lines if q
            )
            parts.append(f'<section style="{quote_section_style}">{inner}</section>')
            continue

        # 无序列表（连续 - / * 行合并）
        if UNORDERED_ITEM_RE.match(stripped):
            seen_non_empty = True
            items = []
            while i < n:
                m_item = UNORDERED_ITEM_RE.match(lines[i].strip())
                if not m_item:
                    break
                items.append(m_item.group(1))
                i += 1
            li_html = "".join(
                f'<li style="{LIST_ITEM_STYLE}">{render_inline(t, cfg)}</li>' for t in items
            )
            parts.append(f'<ul style="{list_style}">{li_html}</ul>')
            continue

        # 有序列表（连续 1. 2. 行合并）
        if ORDERED_ITEM_RE.match(stripped):
            seen_non_empty = True
            items = []
            while i < n:
                m_item = ORDERED_ITEM_RE.match(lines[i].strip())
                if not m_item:
                    break
                items.append(m_item.group(1))
                i += 1
            li_html = "".join(
                f'<li style="{LIST_ITEM_STYLE}">{render_inline(t, cfg)}</li>' for t in items
            )
            parts.append(f'<ol style="{list_style}">{li_html}</ol>')
            continue

        # 图片（支持 ![alt](url) 与 Obsidian ![[path|alt|width]]）
        parsed_img = _parse_image_line(stripped)
        if parsed_img is not None:
            url, alt = parsed_img
            seen_non_empty = True
            parts.append(
                f'<p style="margin:16px 0"><img src="{esc_attr(url)}" alt="{esc_attr(alt)}" style="{IMG_STYLE}"/></p>'
            )
            if alt.strip():
                parts.append(f'<p style="{CAPTION_STYLE}">{span(alt.strip())}</p>')
            i += 1
            continue

        # 空行
        if stripped == "":
            i += 1
            continue

        # 普通段落
        seen_non_empty = True
        parts.append(f'<p style="{body_p_style}">{render_inline(stripped, cfg)}</p>')
        i += 1

    body_html = "\n".join(parts)
    # 最外层容器同样不能用 <div>，用 <section> 承载，纯内联样式、无 class/id
    return f'<section style="{wrapper_style}">\n{body_html}\n</section>\n'


def build_copy_html(
    wechat_html,
    *,
    title="",
    author="",
    digest="",
    cover_src="",
    x_cover_src="",
    theme="default",
):
    """生成独立可打开的公众号一键复制与手机端双比例预览工作台（copy.html）。"""
    safe_title = esc(title or "未命名公众号草稿")
    safe_author = esc(author or "（未填作者）")
    safe_digest = esc(digest or "（未填摘要）")
    title_len = len(title or "")
    digest_len = len(digest or "")

    cover_block = ""
    if cover_src:
        safe_cover = esc_attr(cover_src)
        cover_block = (
            '<div class="cover-grid">'
            '<div class="cover-card">'
            '<div class="cover-label">公众号首图预览（2.35:1 · 900×383 / 1880×800，红框为中央 1:1 分享安全区）</div>'
            '<div class="cover-banner-wrap">'
            f'<img src="{safe_cover}" alt="公众号首图预览" class="cover-banner-img"/>'
            '<div class="cover-safe-box" title="中央 1:1 分享卡片裁切安全区"><span>1:1 安全区</span></div>'
            '</div>'
            '</div>'
            '<div class="cover-card cover-card-square">'
            '<div class="cover-label">转发卡片 / 次条 1:1 裁切预览</div>'
            '<div class="cover-square-wrap">'
            f'<img src="{safe_cover}" alt="1:1 分享卡片预览" class="cover-square-img"/>'
            '</div>'
            '</div>'
            '</div>'
        )
    else:
        cover_block = '<div class="cover-empty">未指定公众号封面图（建议提供 900×383 或 1880×800 封面）</div>'

    x_cover_block = ""
    if x_cover_src:
        safe_x_cover = esc_attr(x_cover_src)
        x_cover_block = (
            '<div class="cover-card" style="margin-top:12px">'
            '<div class="cover-label">X Article 封面预览（5:2）</div>'
            f'<div class="x-cover-wrap"><img src="{safe_x_cover}" alt="X 封面预览" class="cover-banner-img"/></div>'
            '</div>'
        )

    escaped_source = esc(wechat_html)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{safe_title} · 公众号排版预览与一键复制工作台</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 24px 16px 48px; background: #f4f3ef; color: #222; font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif; }}
  .workbench {{ max-width: 1120px; margin: 0 auto; display: grid; grid-template-columns: 1fr 420px; gap: 24px; align-items: start; }}
  @media (max-width: 920px) {{ .workbench {{ grid-template-columns: 1fr; }} }}
  .panel {{ background: #fff; border: 1px solid #e5e2dc; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.04); }}
  .toolbar {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between; margin-bottom: 16px; padding-bottom: 14px; border-bottom: 1px solid #eee; }}
  .toolbar h1 {{ font-size: 18px; margin: 0; font-weight: 600; color: #1d1c1a; }}
  .btn-group {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  button, .link-btn {{ appearance: none; border: 1px solid #d0ccc4; background: #fff; color: #222; padding: 8px 14px; border-radius: 8px; font-size: 13px; font-weight: 500; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; gap: 6px; }}
  button.primary {{ background: #07c160; border-color: #07c160; color: #fff; font-weight: 600; }}
  button.primary:hover {{ background: #06ad56; }}
  button:hover, .link-btn:hover {{ background: #f8f7f4; }}
  .status-toast {{ font-size: 13px; color: #07c160; font-weight: 600; min-height: 20px; margin-bottom: 8px; }}
  .meta-list {{ display: grid; gap: 10px; margin-bottom: 18px; font-size: 13px; }}
  .meta-row {{ display: grid; grid-template-columns: 72px 1fr auto; gap: 8px; align-items: center; background: #faf9f6; padding: 8px 12px; border-radius: 8px; border: 1px solid #eeeae2; }}
  .meta-key {{ color: #666; font-weight: 500; }}
  .meta-val {{ color: #111; word-break: break-all; }}
  .meta-count {{ color: #888; font-size: 12px; margin-left: 6px; }}
  .cover-grid {{ display: grid; grid-template-columns: 1fr 160px; gap: 12px; align-items: start; }}
  .cover-card {{ background: #faf9f6; border: 1px solid #eeeae2; border-radius: 8px; padding: 10px; }}
  .cover-label {{ font-size: 12px; color: #666; margin-bottom: 8px; }}
  .cover-banner-wrap {{ position: relative; width: 100%; aspect-ratio: 2.35 / 1; overflow: hidden; border-radius: 6px; background: #e9e5dc; }}
  .cover-banner-img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
  .cover-safe-box {{ position: absolute; top: 0; bottom: 0; left: 50%; width: 42.553%; transform: translateX(-50%); border: 2px dashed rgba(200, 60, 35, 0.85); background: rgba(200, 60, 35, 0.04); display: flex; align-items: flex-end; justify-content: center; padding-bottom: 4px; pointer-events: none; }}
  .cover-safe-box span {{ background: rgba(200, 60, 35, 0.85); color: #fff; font-size: 10px; padding: 1px 6px; border-radius: 4px; }}
  .cover-square-wrap {{ width: 100%; aspect-ratio: 1 / 1; overflow: hidden; border-radius: 6px; background: #e9e5dc; }}
  .cover-square-img {{ width: 100%; height: 100%; object-fit: cover; object-position: center; display: block; }}
  .x-cover-wrap {{ width: 100%; aspect-ratio: 5 / 2; overflow: hidden; border-radius: 6px; background: #e9e5dc; }}
  .cover-empty {{ padding: 12px; border-radius: 8px; background: #fff8eb; border: 1px solid #f0dc99; color: #8a6d1b; font-size: 13px; }}
  .checklist {{ margin-top: 18px; padding-top: 14px; border-top: 1px solid #eee; font-size: 13px; }}
  .checklist h2 {{ font-size: 14px; margin: 0 0 10px; color: #222; }}
  .checklist label {{ display: flex; align-items: flex-start; gap: 8px; margin: 6px 0; color: #444; cursor: pointer; }}
  .phone-frame {{ background: #fff; border: 10px solid #222; border-radius: 32px; max-width: 395px; margin: 0 auto; overflow: hidden; box-shadow: 0 8px 24px rgba(0,0,0,0.08); }}
  .phone-statusbar {{ background: #f7f7f7; padding: 8px 16px; font-size: 12px; color: #666; text-align: center; border-bottom: 1px solid #eee; font-weight: 500; }}
  .phone-body {{ padding: 18px 14px 28px; max-height: 780px; overflow-y: auto; background: #fff; }}
  details.source-box {{ margin-top: 16px; }}
  details.source-box summary {{ cursor: pointer; font-size: 13px; color: #555; font-weight: 500; }}
  textarea#html-source {{ width: 100%; height: 180px; margin-top: 8px; font-family: Menlo, Monaco, Consolas, monospace; font-size: 12px; padding: 10px; border-radius: 8px; border: 1px solid #ddd; }}
</style>
</head>
<body>
<div class="workbench">
  <div class="panel">
    <div class="toolbar">
      <h1>公众号排版预览与一键复制工作台（theme: {esc(theme)}）</h1>
      <div class="btn-group">
        <button type="button" class="primary" id="btn-copy-rich" onclick="copyRichText()">复制公众号富文本</button>
        <button type="button" id="btn-copy-source" onclick="copyHtmlSource()">复制 HTML 源码</button>
        <a class="link-btn" href="https://mp.weixin.qq.com" target="_blank" rel="noopener noreferrer">打开公众号后台 ↗</a>
      </div>
    </div>
    <div id="toast" class="status-toast" role="status" aria-live="polite"></div>

    <div class="meta-list">
      <div class="meta-row">
        <span class="meta-key">文章标题</span>
        <span class="meta-val" id="val-title">{safe_title}<span class="meta-count">（{title_len} 字）</span></span>
        <button type="button" onclick="copyTextValue('val-title', { repr(title or '') })">复制标题</button>
      </div>
      <div class="meta-row">
        <span class="meta-key">作者署名</span>
        <span class="meta-val" id="val-author">{safe_author}</span>
        <button type="button" onclick="copyTextValue('val-author', { repr(author or '') })">复制作者</button>
      </div>
      <div class="meta-row">
        <span class="meta-key">摘要 digest</span>
        <span class="meta-val" id="val-digest">{safe_digest}<span class="meta-count">（{digest_len} 字，建议 20–40 字）</span></span>
        <button type="button" onclick="copyTextValue('val-digest', { repr(digest or '') })">复制摘要</button>
      </div>
    </div>

    {cover_block}
    {x_cover_block}

    <div class="checklist">
      <h2>发布前 7 项预览检查（只建草稿，人工核对后再群发）</h2>
      <label><input type="checkbox"/> 1. 封面图：手机端缩略图清晰，中央 1:1 红色虚线安全区内主标题/主体完整无裁断</label>
      <label><input type="checkbox"/> 2. 标题与作者：无错别字，未擅改作者原意，赞赏署名一致</label>
      <label><input type="checkbox"/> 3. 摘要（digest）：转发卡片展示自然凝练（20–40 字），未直接渲染进正文开头</label>
      <label><input type="checkbox"/> 4. 正文配图：仅保留必要的流程图/预览图/意境分隔图，路径与显示正常</label>
      <label><input type="checkbox"/> 5. 手机端排版：右侧 390px 手机视口内段落呼吸感舒适，加粗与引用克制</label>
      <label><input type="checkbox"/> 6. 链接与双链：外链可正常打开，Obsidian [[双链]] 已转为纯文本</label>
      <label><input type="checkbox"/> 7. 结尾口吻：保持作者本人文风，无多余 AI 总结套话</label>
    </div>

    <details class="source-box">
      <summary>查看 / 手动复制内联样式 HTML 源码</summary>
      <textarea id="html-source" readonly>{escaped_source}</textarea>
    </details>
  </div>

  <div>
    <div class="phone-frame">
      <div class="phone-statusbar">微信公众号 · 390px 手机端预览</div>
      <div class="phone-body" id="wechat-rich-content">
{wechat_html}
      </div>
    </div>
  </div>
</div>

<script>
function showToast(msg) {{
  var el = document.getElementById('toast');
  if (el) {{ el.textContent = msg; }}
}}
function copyTextValue(id, rawText) {{
  if (!rawText) {{ showToast('⚠️ 当前字段为空'); return; }}
  navigator.clipboard.writeText(rawText).then(function() {{
    showToast('✅ 已复制：' + rawText.slice(0, 28));
  }});
}}
function copyHtmlSource() {{
  var src = document.getElementById('html-source').value;
  navigator.clipboard.writeText(src).then(function() {{
    showToast('✅ 已复制 HTML 源码（' + src.length + ' 字符）');
  }});
}}
function copyRichText() {{
  var node = document.getElementById('wechat-rich-content');
  var html = document.getElementById('html-source').value;
  var plain = node ? node.innerText : '';
  if (navigator.clipboard && window.ClipboardItem) {{
    var item = new ClipboardItem({{
      'text/html': new Blob([html], {{ type: 'text/html' }}),
      'text/plain': new Blob([plain], {{ type: 'text/plain' }})
    }});
    navigator.clipboard.write([item]).then(function() {{
      showToast('✅ 已复制公众号富文本！可直接到公众号后台「图文消息」正文区按 Cmd+V / Ctrl+V 粘贴');
    }}).catch(function() {{
      fallbackCopyNode(node);
    }});
  }} else {{
    fallbackCopyNode(node);
  }}
}}
function fallbackCopyNode(node) {{
  var range = document.createRange();
  range.selectNodeContents(node);
  var sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  document.execCommand('copy');
  sel.removeAllRanges();
  showToast('✅ 已复制公众号富文本（兼容模式）！可直接粘贴到公众号后台');
}}
</script>
</body>
</html>
"""


def parse_args():
    ap = argparse.ArgumentParser(
        description="Markdown → 微信公众号安全 HTML（遵守微信平台红线，渲染后请接 validate_wechat_html.py 校验）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--file", help="输入 Markdown 文件路径；不传则从 stdin 读取")
    ap.add_argument("--output", help="输出 HTML 文件路径；不传则打印到 stdout")
    ap.add_argument("--copy-html", help="可选：同时输出一键复制与手机端预览工作台页面（如 copy.html 或 <stem>.copy.html）")
    ap.add_argument("--cover", help="可选：用于 copy.html 预览的公众号首图路径（默认读取 frontmatter cover）")
    ap.add_argument("--x-cover", help="可选：用于 copy.html 预览的 X 封面图路径（默认读取 frontmatter x_cover）")
    ap.add_argument("--strip-h1", action="store_true", help="剥离正文开头的首个 # 一级标题（推草稿箱时标题由元数据承载）")
    ap.add_argument("--theme", choices=sorted(THEMES), help="排版主题：default（默认红黑强调）或 minimal（克制纯墨随笔）")
    return ap.parse_args()


def main():
    args = parse_args()
    if args.file:
        md_text = Path(args.file).read_text(encoding="utf-8")
    else:
        md_text = sys.stdin.read()

    out_html = render(md_text, strip_h1=args.strip_h1, theme=args.theme)

    if args.output:
        Path(args.output).write_text(out_html, encoding="utf-8")
        print(f"✅ 渲染完成：{args.output}（{len(out_html)} 字节）", file=sys.stderr)
    else:
        print(out_html, end="")

    if args.copy_html:
        fm, body = parse_frontmatter(md_text)
        m_h1 = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        title = fm.get("title") or (m_h1.group(1).strip() if m_h1 else "")
        author = fm.get("author") or ""
        digest = fm.get("digest") or fm.get("summary") or (str(fm.get("preface") or "").strip()[:120])
        cover_src = args.cover or fm.get("cover") or ""
        x_cover_src = args.x_cover or fm.get("x_cover") or ""
        resolved_theme = (args.theme or fm.get("wechat_theme") or fm.get("theme") or "default").strip().lower()
        copy_doc = build_copy_html(
            out_html,
            title=title,
            author=author,
            digest=digest,
            cover_src=cover_src,
            x_cover_src=x_cover_src,
            theme=resolved_theme,
        )
        copy_path = Path(args.copy_html)
        copy_path.parent.mkdir(parents=True, exist_ok=True)
        copy_path.write_text(copy_doc, encoding="utf-8")
        print(f"✅ 一键复制工作台已生成：{args.copy_html}", file=sys.stderr)


if __name__ == "__main__":
    main()
