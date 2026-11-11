# SOIA Open Skills Catalog

> Generated from `skills/*/SKILL.md` and optional `agents/openai.yaml`.
> Do not edit by hand. Run `python3 scripts/generate_skill_catalog.py`.
> Discoverable by `npx skills add soia-team/soia-open-cwork-office-skills -l`: 7 skills.

## Source Fields

- `SKILL.md` is the canonical cross-agent instruction file. Capabilities, dependencies, setup, workflow steps, logs, and completion summaries must live there.
- `agents/openai.yaml` is optional UI/catalog metadata for OpenAI/Codex-style surfaces and SOIA registry display: `display_name`, `short_description`, and `default_prompt`.
- Claude Code and generic skills.sh-compatible agents must be assumed to consume `SKILL.md`; do not put required workflow steps only in `agents/openai.yaml`.
- Legacy `metadata.json` files are not used to generate this catalog.

## CWork

| Skill | Description | Default Prompt |
|---|---|---|
| [`soia-cwork-feishu-cli`](./soia-cwork-feishu-cli/) | 用官方 lark-cli 只读调研飞书 Wiki/Drive，并可把知识库同步为本地 Markdown。 | 用 soia-cwork-feishu-cli 先区分飞书知识库和云盘，再分别核对应用身份 Bot 与用户 OAuth 的最小只读权限，最后只读调研，不要修改远端内容。 |
| [`soia-cwork-processon-diagrams`](./soia-cwork-processon-diagrams/) | 可断点、可审计地递归盘点 ProcessOn，并安全导出图表。 | Use $soia-cwork-processon-diagrams in three explicit stages: (1) inventory.init for a recursive, resumable and audited baseline, or inventory.incremental only after a new complete snapshot is diffed against a previous complete checkpoint; (2) use the host-agnostic processon_browser_runner.py with its dedicated profile—never the customer's main Chrome or a host-only browser/computer-use loop—then build and verify the archive plan, initialize download-progress.json, use next/record/mark/audit for every confirmed artifact, serialize asynchronous exports, and require balanced popup open/close plus context-close receipts; flowcharts default to VSDX and mind maps to XMind, while Markdown-only mind-map export is evidence rather than completion; (3) ask before parsing, converting or upgrading; VSDX conversion or editing is out of scope and left to the user's own draw.io tooling. Keep each stage's evidence and completion state separate; a snapshot diff is not a ProcessOn API/event delta. |

## Media

| Skill | Description | Default Prompt |
|---|---|---|
| [`soia-media-compose-article-draft`](./soia-media-compose-article-draft/) | 把 distill 提炼出的观点写成成文草稿，生成可继续交给 publish-* 家族的文章。可指定公众号/知乎/随笔风格 | Use soia-media-compose-article-draft: 把 distill 提炼出的观点写成成文草稿，后续按目标平台交给 publish-* 家族。 |
| [`soia-media-generate-article-image`](./soia-media-generate-article-image/) | 生成文章图、品牌 Logo、X Prompt Deck 进化图与可验收技能宣传轮播 | Use $soia-media-generate-article-image to compile source facts, a brand-logo brief, or an X Prompt Deck into a reusable visual system; generate direction assets, finalize brand logos as deterministic SVG variants, and verify exact text and evidence. |
| [`soia-media-publish-rednote-card`](./soia-media-publish-rednote-card/) | 把文章改写成小红书 rednote 笔记：标题、3–5 段短文、标签和配图建议。 | Use soia-media-publish-rednote-card: 把这篇文章改成小红书笔记，给我标题、短文、话题标签和配图建议。 |
| [`soia-media-publish-wechat-draft`](./soia-media-publish-wechat-draft/) | 把文章排版成符合微信公众号限制的 HTML，校验后推入草稿箱；只建草稿，绝不自动群发。 | Use soia-media-publish-wechat-draft: 把这篇文章排版成公众号文章，校验后推到草稿箱。 |
| [`soia-media-publish-x`](./soia-media-publish-x/) | 把成文拆成 ≤280 字符的 X thread（可按授权存草稿或发布），或把 Markdown 直传 X Articles 草稿箱并校验，只存草稿。 | Use soia-media-publish-x: 把这篇文章拆成带 (1/N) 编号的 X 推文串，保留链接和代码完整性；如果我要的是长文，就上传到 X Articles 草稿箱并给我草稿 URL。 |

## Registry Export

Generate v7 SOIA registry manifests from the same sources when needed:

```bash
python3 scripts/generate_skill_catalog.py --registry-out <soia-repo>/runtime/registry/skills
```
