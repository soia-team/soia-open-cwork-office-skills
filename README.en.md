# SOIA Office Collaboration Skills

Reusable AI skills for Feishu knowledge collaboration and ProcessOn diagram archiving.

## Skills

| Skill | Summary |
|---|---|
| `soia-cwork-feishu-cli` | Use the official Feishu `lark-cli` for least-privilege, read-only research across wikis, drives, and documents. |
| `soia-cwork-feishu-doc-git-sync` | Synchronize Feishu wikis or cloud documents to local Markdown while preserving structure and source metadata. |
| `soia-cwork-processon-diagrams` | Inventory ProcessOn folders and export, verify, and archive authorized diagrams safely. |

## Installation

Replace `<skill>` with a skill name from the table above:

```bash
npx skills add soia-team/soia-open-cwork-office-skills -g -a '*' -s <skill> -y
```

For example, install the Feishu CLI skill:

```bash
npx skills add soia-team/soia-open-cwork-office-skills -g -a '*' -s soia-cwork-feishu-cli -y
```

## Ecosystem

See [soia-team/soia-open-skills](https://github.com/soia-team/soia-open-skills) for the canonical specifications and complete ecosystem catalog.

## License

MIT License. See [LICENSE](./LICENSE).
