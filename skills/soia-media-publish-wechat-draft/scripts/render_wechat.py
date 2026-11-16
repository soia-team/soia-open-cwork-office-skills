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
  ![alt](url)                → <img>；alt 非空则图下加一行说明文字
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
                fm[k.strip()] = v.strip().strip('"').strip("'")
        body = normalized[m.end():]
    return fm, body


def _wikilink_label(raw_inner):
    if "|" in raw_inner:
        return raw_inner.split("|", 1)[1].strip()
    target = raw_inner.strip()
    if target.startswith("#"):
        return target.lstrip("#").strip()
    base = target.split("#", 1)[0].rsplit("/", 1)[-1].strip()
    return base or target


def render_inline(text):
    """行内标记：`代码` / [[双链]] / [链接](url) / ==高亮== / **加粗** / ~~删除~~ / *斜体*；其余纯文本套 <span>。"""
    if text == "":
        return span("")
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
                out.append(f'<a href="{esc_attr(url)}" style="{LINK_STYLE}">{span(link_text)}</a>')
            else:
                out.append(span(link_text))
        elif hi is not None:
            out.append(f'<strong style="color:{HIGHLIGHT_COLOR}">{span(hi)}</strong>')
        elif bold is not None:
            out.append(f'<strong style="color:{THEME_COLOR}">{span(bold)}</strong>')
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


def _render_table(headers, rows):
    th_cells = "".join(f'<th style="{TH_STYLE}">{render_inline(h)}</th>' for h in headers)
    thead = f"<thead><tr>{th_cells}</tr></thead>"
    body_rows = []
    for row in rows:
        padded = row + [""] * max(0, len(headers) - len(row))
        tds = "".join(f'<td style="{TD_STYLE}">{render_inline(cell)}</td>' for cell in padded[:len(headers)])
        body_rows.append(f"<tr>{tds}</tr>")
    tbody = f"<tbody>{''.join(body_rows)}</tbody>" if body_rows else ""
    return (
        f'<section style="{TABLE_WRAP_STYLE}">'
        f'<table style="{TABLE_STYLE}">{thead}{tbody}</table>'
        f'</section>'
    )


def render(md_text, strip_h1=False):
    _, body = parse_frontmatter(md_text)
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
            content = render_inline(m.group(2).strip())
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
            parts.append(_render_table(headers, rows))
            continue

        # 引用块（连续 > 行合并成一张金句卡）
        if stripped.startswith(">"):
            seen_non_empty = True
            quote_lines = []
            while i < n and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip().lstrip(">").strip())
                i += 1
            inner = "".join(
                f'<p style="{QUOTE_P_STYLE}">{render_inline(q)}</p>' for q in quote_lines if q
            )
            parts.append(f'<section style="{QUOTE_SECTION_STYLE}">{inner}</section>')
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
                f'<li style="{LIST_ITEM_STYLE}">{render_inline(t)}</li>' for t in items
            )
            parts.append(f'<ul style="{LIST_STYLE}">{li_html}</ul>')
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
                f'<li style="{LIST_ITEM_STYLE}">{render_inline(t)}</li>' for t in items
            )
            parts.append(f'<ol style="{LIST_STYLE}">{li_html}</ol>')
            continue

        # 图片
        m = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", stripped)
        if m:
            seen_non_empty = True
            alt, url = m.group(1), m.group(2)
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
        parts.append(f'<p style="{BODY_P_STYLE}">{render_inline(stripped)}</p>')
        i += 1

    body_html = "\n".join(parts)
    # 最外层容器同样不能用 <div>，用 <section> 承载，纯内联样式、无 class/id
    return f'<section style="{WRAPPER_STYLE}">\n{body_html}\n</section>\n'


def parse_args():
    ap = argparse.ArgumentParser(
        description="Markdown → 微信公众号安全 HTML（遵守微信平台红线，渲染后请接 validate_wechat_html.py 校验）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--file", help="输入 Markdown 文件路径；不传则从 stdin 读取")
    ap.add_argument("--output", help="输出 HTML 文件路径；不传则打印到 stdout")
    ap.add_argument("--strip-h1", action="store_true", help="剥离正文开头的首个 # 一级标题（推草稿箱时标题由元数据承载）")
    return ap.parse_args()


def main():
    args = parse_args()
    if args.file:
        md_text = Path(args.file).read_text(encoding="utf-8")
    else:
        md_text = sys.stdin.read()

    out_html = render(md_text, strip_h1=args.strip_h1)

    if args.output:
        Path(args.output).write_text(out_html, encoding="utf-8")
        print(f"✅ 渲染完成：{args.output}（{len(out_html)} 字节）", file=sys.stderr)
    else:
        print(out_html, end="")


if __name__ == "__main__":
    main()
