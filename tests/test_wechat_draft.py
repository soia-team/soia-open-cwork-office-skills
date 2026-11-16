from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "soia-media-publish-wechat-draft" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import archive  # noqa: E402
import publish  # noqa: E402
import publish_env  # noqa: E402
import render_wechat  # noqa: E402
from validate_wechat_html import WeChatHTMLChecker  # noqa: E402


SAMPLE_ARTICLE = """---
title: 示例长文标题
author: 示例作者
digest: 一段用于测试的摘要
tags: [草稿]
---

# 示例长文标题

第一段包含**主题加粗**、==红色高亮==、*斜体*、~~旧说法~~、`inline_code`，以及 [[20_资料库/示例笔记|别名双链]] 和 [[30_日志与思考/另一篇]]，还有 [外链](https://example.com/post) 和 [本地](relative.md)。

> 金句引用第一行
> 金句引用第二行

---

- 无序一
- 无序二

1. 有序一
2. 有序二

| 列A | 列B |
| --- | --- |
| **值1** | `code` |
| 值2 | ==高== |

```python
print("hello")

print("world")
```

![封面示意图](images/demo.png)
"""


class WeChatRenderAndValidateTests(unittest.TestCase):
    def test_render_wechat_full_markdown_passes_strict_validator_with_zero_warnings(self) -> None:
        html_out = render_wechat.render(SAMPLE_ARTICLE, strip_h1=True)

        self.assertNotIn("<h1>", html_out)
        self.assertNotIn("[[", html_out)
        self.assertIn("别名双链", html_out)
        self.assertIn("另一篇", html_out)
        self.assertIn("<ol style=", html_out)
        self.assertIn("<ul style=", html_out)
        self.assertIn("<table style=", html_out)
        self.assertIn("<th style=", html_out)
        self.assertIn("<td style=", html_out)
        self.assertIn('href="https://example.com/post"', html_out)
        self.assertNotIn("relative.md", html_out)
        self.assertIn("<em style=", html_out)
        self.assertIn("<del style=", html_out)

        checker = WeChatHTMLChecker()
        checker.feed(html_out)
        checker.close()
        self.assertEqual(checker.errors, [])
        self.assertEqual(checker.warnings, [])

    def test_validator_catches_forbidden_constructs_and_bare_text(self) -> None:
        bad_html = '<div class="wrap" id="x" style="position:fixed;color:var(--a)"><style>.a{float:left}</style><p>裸文字</p></div>'
        checker = WeChatHTMLChecker()
        checker.feed(bad_html)
        checker.close()
        err_types = {e["type"] for e in checker.errors}
        self.assertIn("div-tag", err_types)
        self.assertIn("class-attr", err_types)
        self.assertIn("id-attr", err_types)
        self.assertIn("style-block", err_types)
        self.assertIn("style-position", err_types)
        self.assertIn("style-css-var-use", err_types)
        self.assertIn("style-float", err_types)
        self.assertEqual([w["type"] for w in checker.warnings], ["bare-text"])

    def test_obsidian_embeds_and_frontmatter_inline_comments(self) -> None:
        md = (
            "---\n"
            "title: 示例 # 保留URL或注释?  # 注释文本\n"
            "wechat_account: zbt  # 默认账号\n"
            'digest: "包含 # 号的摘要"\n'
            "url: https://example.com/a#frag\n"
            "---\n\n"
            "![[assets/sub/pic1.png]]\n\n"
            "![[pic2.png|600]]\n\n"
            "![[pic3.png|清晰图注|600]]\n\n"
            "![[非图片笔记]]\n"
        )
        fm, _ = render_wechat.parse_frontmatter(md)
        self.assertEqual(fm["wechat_account"], "zbt")
        self.assertEqual(fm["digest"], "包含 # 号的摘要")
        self.assertEqual(fm["url"], "https://example.com/a#frag")

        html_out = render_wechat.render(md, strip_h1=True)
        self.assertIn('src="assets/sub/pic1.png"', html_out)
        self.assertIn('src="pic2.png"', html_out)
        self.assertNotIn(">600<", html_out)
        self.assertIn('src="pic3.png"', html_out)
        self.assertIn(">清晰图注<", html_out)
        self.assertNotIn("非图片笔记.png", html_out)

        checker = WeChatHTMLChecker()
        checker.feed(html_out)
        checker.close()
        self.assertEqual(checker.errors, [])
        self.assertEqual(checker.warnings, [])

    def test_minimal_theme_uses_ink_palette_and_rejects_unknown_theme(self) -> None:
        md = (
            "---\n"
            "title: 克制随笔\n"
            "wechat_theme: minimal\n"
            "---\n\n"
            "正文含**主题加粗**和==高亮==以及[外链](https://example.com)。\n\n"
            "> 引文内容\n"
        )
        html_out = render_wechat.render(md, strip_h1=True)
        self.assertIn("#191c1b", html_out)
        self.assertIn("#8c8376", html_out)
        self.assertNotIn("#c0392b", html_out)
        self.assertNotIn("#e74c3c", html_out)

        checker = WeChatHTMLChecker()
        checker.feed(html_out)
        checker.close()
        self.assertEqual(checker.errors, [])
        self.assertEqual(checker.warnings, [])

        with self.assertRaises(ValueError):
            render_wechat.render(md, theme="nonexistent")


class PublishEnvMultiAccountTests(unittest.TestCase):
    def test_multi_account_resolution_and_fail_closed_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yml"
            cfg.write_text(
                "\n".join(
                    [
                        "accounts:",
                        "  main_acct:",
                        '    name: "示例主号"',
                        '    wechat_id: "acct_main_01"',
                        '    app_id: "wx_id_1"',
                        '    app_secret: "wx_sec_1"',
                        "  alt_acct:",
                        '    name: "示例副号"',
                        '    wechat_id: "acct_alt_02"',
                        '    app_id: "wx_id_2"',
                        '    app_secret: "wx_sec_2"',
                        "  pending_acct:",
                        '    name: "待填账号"',
                        '    wechat_id: "acct_pending_03"',
                        '    app_id: "wx_id_3"',
                        '    app_secret: "<YOUR_APP_SECRET>"',
                    ]
                ),
                encoding="utf-8",
            )

            accounts = publish_env.list_accounts(config_path=cfg)
            self.assertEqual(len(accounts), 3)
            by_alias = {item["alias"]: item for item in accounts}
            for item in accounts:
                self.assertNotIn("app_id", item)
                self.assertNotIn("app_secret", item)
            self.assertTrue(by_alias["main_acct"]["configured"])
            self.assertTrue(by_alias["alt_acct"]["configured"])
            self.assertFalse(by_alias["pending_acct"]["configured"])
            with self.assertRaises(ValueError):
                publish_env.resolve_credentials(account="pending_acct", config_path=cfg)

            # 未指定账号且无 default_account 时必须报错，不能静默选一个
            with self.assertRaises(ValueError):
                publish_env.resolve_credentials(account=None, config_path=cfg)

            # 指定不存在的账号不能回退到另一个账号
            with self.assertRaises(ValueError):
                publish_env.resolve_credentials(account="unknown", config_path=cfg)

            # 按 alias / wechat_id 均能精确命中
            self.assertEqual(
                publish_env.resolve_credentials(account="alt_acct", config_path=cfg),
                ("wx_id_2", "wx_sec_2", "alt_acct"),
            )
            self.assertEqual(
                publish_env.resolve_credentials(account="acct_main_01", config_path=cfg),
                ("wx_id_1", "wx_sec_1", "main_acct"),
            )

    def test_sidecar_multi_yml_files_and_explicit_config_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "soia-media-publish-wechat-draft"
            (cfg_dir / "accounts").mkdir(parents=True)
            (cfg_dir / "config.zbt.yml").write_text(
                "env:\n  WECHAT_ACCOUNT_NAME: \"示例号A\"\n  WECHAT_APP_ID: \"wx_a\"\n  WECHAT_APP_SECRET: \"sec_a\"\n",
                encoding="utf-8",
            )
            (cfg_dir / "accounts" / "yuanbaobiji.yml").write_text(
                "env:\n  WECHAT_ACCOUNT_NAME: \"示例号B\"\n  WECHAT_APP_ID: \"wx_b\"\n  WECHAT_APP_SECRET: \"sec_b\"\n",
                encoding="utf-8",
            )
            # 模板文件应被忽略
            (cfg_dir / "config.example.yml").write_text("env:\n  WECHAT_APP_ID: \"wx_x\"\n", encoding="utf-8")

            clean_env = {
                k: v
                for k, v in os.environ.items()
                if k not in {
                    "SOIA_MEDIA_PUBLISH_WECHAT_DRAFT_CONFIG_FILE",
                    "SOIA_MEDIA_PUBLISH_ENV_FILE",
                    "WECHAT_APP_ID",
                    "WECHAT_APP_SECRET",
                }
            }
            clean_env["SOIA_SKILLS_CONFIG_HOME"] = tmp
            with mock.patch.dict(os.environ, clean_env, clear=True):
                accounts = publish_env.list_accounts()
                self.assertEqual([a["alias"] for a in accounts], ["zbt", "yuanbaobiji"])
                # 多分文件无 default 时不传 --account 必须报错
                with self.assertRaises(ValueError):
                    publish_env.resolve_credentials()
                self.assertEqual(
                    publish_env.resolve_credentials(account="zbt"),
                    ("wx_a", "sec_a", "zbt"),
                )
                self.assertEqual(
                    publish_env.resolve_credentials(account="yuanbaobiji"),
                    ("wx_b", "sec_b", "yuanbaobiji"),
                )
                # 显式 --config 文件名可直接命中
                self.assertEqual(
                    publish_env.resolve_credentials(config_file="config.zbt.yml"),
                    ("wx_a", "sec_a", "config.zbt"),
                )

    def test_legacy_v1_fallback_emits_migration_hint_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            v1_cfg = home / ".config" / "soia-skills" / "soia-pkm-publish-wechat-draft" / "config.yml"
            v1_cfg.parent.mkdir(parents=True, exist_ok=True)
            v1_cfg.write_text(
                "env:\n  WECHAT_APP_ID: wx_legacy_id\n  WECHAT_APP_SECRET: legacy_super_secret\n",
                encoding="utf-8",
            )

            publish_env._WARNED_LEGACY_PATHS.clear()
            clean_env = {
                k: v
                for k, v in os.environ.items()
                if k
                not in {
                    "SOIA_MEDIA_PUBLISH_WECHAT_DRAFT_CONFIG_FILE",
                    "SOIA_MEDIA_PUBLISH_ENV_FILE",
                    "SOIA_PKM_PUBLISH_WECHAT_DRAFT_CONFIG_FILE",
                    "SOIA_PKM_PUBLISH_ENV_FILE",
                    "SOIA_SKILLS_CONFIG_HOME",
                    "XDG_CONFIG_HOME",
                    "WECHAT_APP_ID",
                    "WECHAT_APP_SECRET",
                }
            }
            clean_env["HOME"] = str(home)
            stderr_buf = io.StringIO()
            with mock.patch.dict(os.environ, clean_env, clear=True):
                with redirect_stderr(stderr_buf):
                    creds = publish_env.resolve_credentials()

            self.assertEqual(creds, ("wx_legacy_id", "legacy_super_secret", None))
            self.assertIn("soia-media-publish-wechat-draft/config.yml", stderr_buf.getvalue())
            self.assertNotIn("legacy_super_secret", stderr_buf.getvalue())


class PublishWorkflowTests(unittest.TestCase):
    def test_dry_run_and_mocked_push_flow_with_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            img_dir = work / "images"
            img_dir.mkdir()
            (img_dir / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\ndemo")
            cover = work / "cover.png"
            cover.write_bytes(b"\x89PNG\r\n\x1a\ncover")
            md_path = work / "post.md"
            md_path.write_text(SAMPLE_ARTICLE, encoding="utf-8")

            cfg = work / "config.yml"
            cfg.write_text(
                "\n".join(
                    [
                        "accounts:",
                        "  zbt:",
                        '    name: "示例公众号"',
                        '    wechat_id: "example_id"',
                        '    app_id: "wx_secret_appid"',
                        '    app_secret: "super_secret_value_123"',
                    ]
                ),
                encoding="utf-8",
            )

            # 1. dry-run
            stdout_buf = io.StringIO()
            with mock.patch.dict(os.environ, {"SOIA_MEDIA_PUBLISH_WECHAT_DRAFT_CONFIG_FILE": str(cfg)}):
                with mock.patch.object(
                    sys,
                    "argv",
                    ["publish.py", "--article", str(md_path), "--cover", str(cover), "--account", "zbt", "--dry-run"],
                ):
                    with redirect_stdout(stdout_buf):
                        rc = publish.main()
            self.assertEqual(rc, 0)
            self.assertIn("[dry-run] 渲染并校验通过", stdout_buf.getvalue())
            self.assertTrue((work / "post.html").is_file())

            # 2. mocked full push
            captured_calls: list[tuple[str, bytes | None]] = []

            class FakeResp:
                def __init__(self, payload: dict) -> None:
                    self._data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

                def read(self) -> bytes:
                    return self._data

                def __enter__(self) -> "FakeResp":
                    return self

                def __exit__(self, exc_type, exc, tb) -> None:
                    return None

            def fake_urlopen(req, timeout=30):
                url = req.full_url
                captured_calls.append((url, req.data))
                if "/cgi-bin/token?" in url:
                    return FakeResp({"access_token": "tok_secret_999", "expires_in": 7200})
                if "/cgi-bin/media/uploadimg?" in url:
                    return FakeResp({"url": "https://mmbiz.qpic.cn/mmbiz_png/uploaded_demo/0"})
                if "/cgi-bin/material/add_material?" in url:
                    return FakeResp({"media_id": "thumb_media_001"})
                if "/cgi-bin/draft/add?" in url:
                    return FakeResp({"media_id": "draft_media_777"})
                raise AssertionError(f"Unexpected URL: {url}")

            push_out = io.StringIO()
            push_err = io.StringIO()
            with mock.patch.dict(os.environ, {"SOIA_MEDIA_PUBLISH_WECHAT_DRAFT_CONFIG_FILE": str(cfg)}):
                with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                    with mock.patch.object(
                        sys,
                        "argv",
                        ["publish.py", "--article", str(md_path), "--cover", str(cover), "--account", "zbt"],
                    ):
                        with redirect_stdout(push_out), redirect_stderr(push_err):
                            rc = publish.main()

            self.assertEqual(rc, 0)
            out_all = push_out.getvalue() + push_err.getvalue()
            self.assertIn("draft_media_777", out_all)
            self.assertNotIn("super_secret_value_123", out_all)
            self.assertNotIn("tok_secret_999", out_all)

            draft_reqs = [data for url, data in captured_calls if "/cgi-bin/draft/add?" in url]
            self.assertEqual(len(draft_reqs), 1)
            draft_payload = json.loads(draft_reqs[0].decode("utf-8"))
            article_obj = draft_payload["articles"][0]
            self.assertEqual(article_obj["title"], "示例长文标题")
            self.assertEqual(article_obj["thumb_media_id"], "thumb_media_001")
            self.assertIn("https://mmbiz.qpic.cn/mmbiz_png/uploaded_demo/0", article_obj["content"])
            self.assertNotIn('src="images/demo.png"', article_obj["content"])

    def test_obsidian_basename_resolution_account_author_and_html_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / ".obsidian").mkdir()
            drafts = vault / "50_写作" / "10_草稿"
            img_sub = drafts / "assets" / "nested"
            img_sub.mkdir(parents=True)
            (img_sub / "cover_obs.png").write_bytes(b"\x89PNG\r\n\x1a\ncover")
            (img_sub / "inline_obs.png").write_bytes(b"\x89PNG\r\n\x1a\ninline")

            md_path = drafts / "2026-11-16-obs.md"
            md_path.write_text(
                "---\n"
                "title: Obsidian 嵌图测试\n"
                "wechat_account: zbt\n"
                "cover: cover_obs.png\n"
                "---\n\n"
                "# Obsidian 嵌图测试\n\n"
                "![[inline_obs.png|唯一图注|600]]\n",
                encoding="utf-8",
            )
            html_out_path = vault / "preview" / "custom.html"
            cfg = vault / "config.yml"
            cfg.write_text(
                "\n".join(
                    [
                        "accounts:",
                        "  zbt:",
                        '    name: "测试号"',
                        '    author: "默认作者名"',
                        '    app_id: "wx_id"',
                        '    app_secret: "wx_sec"',
                    ]
                ),
                encoding="utf-8",
            )

            out_buf = io.StringIO()
            with mock.patch.dict(os.environ, {"SOIA_MEDIA_PUBLISH_WECHAT_DRAFT_CONFIG_FILE": str(cfg)}):
                with mock.patch.object(
                    sys,
                    "argv",
                    [
                        "publish.py",
                        "--article",
                        str(md_path),
                        "--config",
                        str(cfg),
                        "--html-out",
                        str(html_out_path),
                        "--dry-run",
                    ],
                ):
                    with redirect_stdout(out_buf):
                        rc = publish.main()
            self.assertEqual(rc, 0)
            self.assertTrue(html_out_path.is_file())
            self.assertFalse(md_path.with_suffix(".html").exists())
            self.assertIn("作者=默认作者名", out_buf.getvalue())

            # 重名 basename 必须 fail-closed 报错
            dup_dir = drafts / "assets" / "other"
            dup_dir.mkdir()
            (dup_dir / "inline_obs.png").write_bytes(b"\x89PNG\r\n\x1a\ndup")
            with self.assertRaises(ValueError):
                publish._resolve_local_path("inline_obs.png", drafts)


class ArchiveTests(unittest.TestCase):
    def test_archive_preserves_order_and_aligns_vault_frontmatter(self) -> None:
        src = (
            "---\n"
            "tags: [待审核, 自省]\n"
            "title: 测试文章\n"
            "status: 待审核\n"
            "created: 2026-11-16\n"
            "custom_field: keep_me\n"
            "---\n\n"
            "# 正文\n"
        )
        out = archive.update_frontmatter(
            src,
            url="https://mp.weixin.qq.com/s/example",
            pub_date="2026-11-16",
            platform="公众号",
            account="zbt",
        )
        self.assertIn("tags: [已发布, 自省]", out)
        self.assertIn("status: 已发布", out)
        self.assertIn("published_at: 2026-11-16", out)
        self.assertIn("platform: 公众号", out)
        self.assertIn("url: https://mp.weixin.qq.com/s/example", out)
        self.assertIn("wechat_account: zbt", out)
        self.assertIn("custom_field: keep_me", out)
        self.assertLess(out.index("title:"), out.index("custom_field:"))

    def test_archive_dir_writes_yyyy_mm_and_rebases_relative_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft_dir = root / "10_草稿" / "50_自省"
            asset_dir = draft_dir / "assets" / "demo"
            asset_dir.mkdir(parents=True)
            (asset_dir / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\nc")
            (asset_dir / "fig.png").write_bytes(b"\x89PNG\r\n\x1a\nf")
            draft = draft_dir / "2026-11-16-半山行.md"
            draft.write_text(
                "---\n"
                "tags: [草稿, 自省]\n"
                "title: 行至半山\n"
                "status: 草稿\n"
                "cover: assets/demo/cover.png\n"
                "---\n\n"
                "# 行至半山\n\n"
                "![图注](assets/demo/fig.png)\n",
                encoding="utf-8",
            )
            archive_root = root / "20_发布"
            out_buf = io.StringIO()
            with mock.patch.object(
                sys,
                "argv",
                [
                    "archive.py",
                    "--article",
                    str(draft),
                    "--url",
                    "https://mp.weixin.qq.com/s/test",
                    "--date",
                    "2026-11-16",
                    "--platform",
                    "公众号",
                    "--account",
                    "zbt",
                    "--archive-dir",
                    str(archive_root),
                ],
            ):
                with redirect_stdout(out_buf):
                    rc = archive.main()
            self.assertEqual(rc, 0)
            dest = archive_root / "2026" / "11" / "2026-11-16-公众号-行至半山.md"
            self.assertTrue(dest.is_file())
            self.assertTrue(draft.is_file())
            text = dest.read_text(encoding="utf-8")
            self.assertIn("status: 已发布", text)
            self.assertIn("cover: ../../../10_草稿/50_自省/assets/demo/cover.png", text)
            self.assertIn("![图注](../../../10_草稿/50_自省/assets/demo/fig.png)", text)

    def test_extract_article_meta_falls_back_to_preface_without_entering_body(self) -> None:
        md_text = (
            "---\n"
            "title: 半山止息\n"
            "series_intro: 系列总推荐。\n"
            "preface: 本篇序言兼平台推荐语。\n"
            "---\n\n"
            "# 半山止息\n\n"
            "正文第一段。\n"
        )
        ns = publish.argparse.Namespace(
            title=None,
            digest=None,
            account=None,
            cover=None,
            author=None,
            config=None,
            theme=None,
        )
        meta = publish.extract_article_meta(md_text, Path("demo.md"), ns)
        html_out = render_wechat.render(md_text, strip_h1=True, theme="minimal")
        self.assertEqual(meta["digest"], "本篇序言兼平台推荐语。")
        self.assertNotIn("本篇序言兼平台推荐语", html_out)
        self.assertNotIn("系列总推荐", html_out)
        self.assertIn("正文第一段。", html_out)

    def test_update_draft_uses_frontmatter_media_id_and_saves_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cover = root / "cover.png"
            cover.write_bytes(b"\x89PNG\r\n\x1a\nc")
            article = root / "post.md"
            article.write_text(
                "---\n"
                "title: 更新草稿测试\n"
                "wechat_account: zbt\n"
                "wechat_draft_media_id: OLD_DRAFT_ID\n"
                "cover: cover.png\n"
                "---\n\n"
                "# 更新草稿测试\n\n"
                "正文段落。\n",
                encoding="utf-8",
            )
            calls: list[tuple[str, dict]] = []

            def fake_post_json(url: str, payload: dict, timeout: int = 30, secrets: list[str] | None = None) -> dict:
                calls.append((url, payload))
                return {"errcode": 0, "errmsg": "ok"}

            out_buf = io.StringIO()
            with mock.patch.object(publish, "load_private_env", return_value=None), mock.patch.object(
                publish, "resolve_credentials", return_value=("app1", "sec1", "zbt")
            ), mock.patch.object(publish, "fetch_access_token", return_value="tok1"), mock.patch.object(
                publish, "upload_cover_material", return_value="THUMB1"
            ), mock.patch.object(publish, "_http_post_json", side_effect=fake_post_json), mock.patch.object(
                sys,
                "argv",
                ["publish.py", "--article", str(article), "--save-draft-id"],
            ):
                with redirect_stdout(out_buf):
                    rc = publish.main()

            self.assertEqual(rc, 0)
            self.assertEqual(len(calls), 1)
            self.assertIn("/draft/update?", calls[0][0])
            self.assertEqual(calls[0][1]["media_id"], "OLD_DRAFT_ID")
            self.assertEqual(calls[0][1]["index"], 0)
            self.assertEqual(calls[0][1]["articles"]["title"], "更新草稿测试")
            self.assertIn("已原地更新微信公众号草稿", out_buf.getvalue())
            self.assertIn("wechat_draft_media_id: OLD_DRAFT_ID", article.read_text(encoding="utf-8"))

    def test_save_draft_id_persists_new_media_id_to_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cover = root / "cover.png"
            cover.write_bytes(b"\x89PNG\r\n\x1a\nc")
            article = root / "new_post.md"
            article.write_text(
                "---\n"
                "title: 新建并写回ID\n"
                "wechat_account: zbt\n"
                "cover: cover.png\n"
                "---\n\n"
                "正文段落。\n",
                encoding="utf-8",
            )
            out_buf = io.StringIO()
            with mock.patch.object(publish, "load_private_env", return_value=None), mock.patch.object(
                publish, "resolve_credentials", return_value=("app1", "sec1", "zbt")
            ), mock.patch.object(publish, "fetch_access_token", return_value="tok1"), mock.patch.object(
                publish, "upload_cover_material", return_value="THUMB1"
            ), mock.patch.object(
                publish, "_http_post_json", return_value={"media_id": "NEW_DRAFT_999"}
            ), mock.patch.object(
                sys,
                "argv",
                ["publish.py", "--article", str(article), "--save-draft-id"],
            ):
                with redirect_stdout(out_buf):
                    rc = publish.main()

            self.assertEqual(rc, 0)
            text = article.read_text(encoding="utf-8")
            self.assertIn("wechat_draft_media_id: NEW_DRAFT_999", text)


if __name__ == "__main__":
    unittest.main()
