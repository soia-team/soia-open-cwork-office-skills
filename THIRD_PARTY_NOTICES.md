# THIRD_PARTY_NOTICES

> Last updated: 2026-07-22
> License values are metadata snapshots. Recheck the upstream source before reuse.

## Runtime tools and libraries

| Upstream | License snapshot | Used by | Relationship |
|---|---|---|---|
| [larksuite/cli](https://github.com/larksuite/cli) | MIT | `soia-cwork-feishu-cli`, `soia-cwork-feishu-doc-git-sync` | Official Feishu/Lark CLI invoked at runtime. |
| [PyYAML](https://pypi.org/project/PyYAML/) | MIT | `soia-cwork-processon-diagrams` | Optional dependency for reading a user-owned YAML config path. |

## Online services

| Service | Provider | Used by | Relationship |
|---|---|---|---|
| ProcessOn Web and API | Beijing DaMaiDi Information Technology Co., Ltd. | `soia-cwork-processon-diagrams` | Uses the customer's authorized web session for inventory/export; official enterprise API capabilities are informational only. |

## Maintenance

- Record new upstream links, install commands, or API endpoints here when a skill adds them.
- Recheck upstream licenses before reuse or redistribution.
