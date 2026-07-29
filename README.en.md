# SOIA Collaborative Office Skills

[中文](README.md) · English

Get material that is locked inside Feishu wikis and ProcessOn diagrams out safely, as local files you own.

## What this is

`soia-open-cwork-office-skills` addresses one problem: **team material scattered across SaaS platforms is unreadable once you leave the platform**. These skills export it into local, version-controllable formats:

```text
Feishu wiki / docs   →  local Markdown (directory structure and source metadata preserved)
ProcessOn diagrams   →  local diagram sources (inventory → authorize → export → verify → archive)
```

Every operation is **read-only by default**. Browsing, searching, and downloading are allowed; editing, renaming, and deleting require your explicit confirmation.

### When to use it

- "Sync our Feishu wiki locally — I want it in Git."
- "Survey what's in the Feishu drive; don't change anything yet."
- "Export and archive those architecture diagrams from ProcessOn."
- "How do I configure the Feishu CLI with least privilege?"

### What it does not do

- Does not write back to the platform. Sync is one-way (platform → local); originals in Feishu or ProcessOn stay untouched.
- Does not store credentials. Feishu uses the official lark-cli session, ProcessOn uses your own browser profile — neither enters the repo.
- Does not request more access than needed. Feishu surveys use least-privilege read-only scopes and tell you which permissions are required before you decide.
- Does not process content. What to do with the exported Markdown belongs to [soia-open-pkm-vault-skills](https://github.com/soia-team/soia-open-pkm-vault-skills).

## Where to start

| Your task | Use | Done when |
|---|---|---|
| Find out what's in Feishu | `soia-cwork-feishu-cli` | Least-privilege read-only inventory with traceable credential setup |
| Sync Feishu docs locally | `soia-cwork-feishu-doc-git-sync` | Local Markdown preserves structure, source, and sync metadata |
| Export and archive ProcessOn diagrams | `soia-cwork-processon-diagrams` | Inventory checkpoint, authorization, and export verification all complete |

All three need a platform login or app authorization first; each checks and reports exactly what is missing before running.

## Skill catalog

> **Ready to use**: ✅ works right after install · 🟡 needs an API key or a third-party login first

| Skill | Responsibility | Ready to use |
|---|---|---|
| `soia-cwork-feishu-cli` | Use the official Feishu `lark-cli` for least-privilege, read-only research across wikis, drives, and documents. | 🟡 |
| `soia-cwork-feishu-doc-git-sync` | Synchronize Feishu wikis or cloud documents to local Markdown while preserving structure and source metadata. | 🟡 |
| `soia-cwork-processon-diagrams` | Inventory ProcessOn folders and export, verify, and archive authorized diagrams safely. | 🟡 |

## Trigger phrases

Once installed, just speak naturally — the agent routes to a skill by these phrases (the full trigger list lives in each skill's `SKILL.md` `description`):

| You say | Skill |
|---|---|
| `调研飞书知识库` / `读取飞书云盘` / `配置飞书 CLI` | `soia-cwork-feishu-cli` |
| `ProcessOn 盘点` / `导出架构图` / `批量下载图表` | `soia-cwork-processon-diagrams` |

## Install

Installing the whole domain plugin is recommended — it brings every skill in this repo:

```bash
claude plugin marketplace add soia-team/soia-open-skills
```

```bash
claude plugin install soia-cwork-office@soia
```

For Codex:

```bash
codex plugin marketplace add soia-team/soia-open-skills
codex plugin add soia-cwork-office@soia
```

For a single skill you can use the npx route. Note the skill lands in the shared
source `~/.agents/skills`; if the plugin is installed too, the same skill shows up
twice and the two copies drift apart — pick one:

```bash
npx skills add soia-team/soia-open-cwork-office-skills -g -a '*' -s <skill-name> -y
```

## Validate & contribute

After changing a skill, run before committing:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/generate_skill_catalog.py --check
python3 scripts/audit_skills.py --strict
```

Contribution flow, the skill contract, and release steps are in the portal's
[CONTRIBUTING.md](https://github.com/soia-team/soia-open-skills/blob/main/CONTRIBUTING.md).

## Ecosystem

Specifications, the full ecosystem catalog, and install guides live in [soia-team/soia-open-skills](https://github.com/soia-team/soia-open-skills).
The full maintenance workflow is in [CONTRIBUTING.md](https://github.com/soia-team/soia-open-skills/blob/main/CONTRIBUTING.md).

## License

MIT License — see [LICENSE](./LICENSE).
