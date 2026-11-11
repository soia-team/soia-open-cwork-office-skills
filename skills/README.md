# SOIA Open Skills Catalog

> Generated from `skills/*/SKILL.md` and optional `agents/openai.yaml`.
> Do not edit by hand. Run `python3 scripts/generate_skill_catalog.py`.
> Discoverable by `npx skills add soia-team/soia-open-cwork-office-skills -l`: 2 skills.

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

## Registry Export

Generate v7 SOIA registry manifests from the same sources when needed:

```bash
python3 scripts/generate_skill_catalog.py --registry-out <soia-repo>/runtime/registry/skills
```
