# SOIA 办公协作技能库

[English](README.en.md) · 中文

把飞书知识库、ProcessOn 图表这类「锁在平台里」的资料，安全地取出来变成你自己的本地文件。

## 这是什么

`soia-open-cwork-office-skills` 解决同一个问题：**团队资料散在各个 SaaS 平台里，离开平台就打不开**。本仓的技能把它们导出成本地可版本化的格式：

```text
飞书知识库 / 云文档  →  本地 Markdown（保留目录与来源元数据）
ProcessOn 图表       →  本地图表源文件（盘点 → 授权 → 导出 → 校验 → 归档）
```

所有操作**默认只读**。浏览、搜索、下载属于允许动作；编辑、重命名、删除必须先获得你的明确确认。

### 适合什么场景

- 「把我们飞书知识库同步一份到本地，我要进 Git。」
- 「调研一下飞书云盘里有哪些文档，先别动。」
- 「ProcessOn 上那些架构图，导出来存档。」
- 「飞书 CLI 怎么配？我要最小权限的。」

### 不负责什么

- 不写回平台。同步是单向的（平台 → 本地），不会改动飞书或 ProcessOn 上的原件。
- 不保存凭据。飞书用官方 lark-cli 的登录态，ProcessOn 用你自己的浏览器 profile，都不进仓库。
- 不申请超出需要的权限。飞书调研走最小权限只读 scope，会先告诉你需要哪些权限再让你决定。
- 不做内容加工。导出来的 Markdown 如何整理、提炼，交给 [soia-open-pkm-vault-skills](https://github.com/soia-team/soia-open-pkm-vault-skills)。

## 从哪里开始

| 你要做的 | 用这个 | 完成标准 |
|---|---|---|
| 先摸清飞书里有什么 | `soia-cwork-feishu-cli` | 最小权限只读盘点，凭据配置有据可查 |
| 同步飞书文档到本地 | `soia-cwork-feishu-doc-git-sync` | 本地 Markdown 保留目录、来源与同步元数据 |
| 导出归档 ProcessOn 图表 | `soia-cwork-processon-diagrams` | 盘点 checkpoint、授权确认、导出校验三步齐全 |

三个技能都需要先完成平台侧登录或应用授权，技能会在执行前逐项检查并明确告诉你缺什么。

## 技能清单

> **开箱可用**：✅ 装完即可使用 · 🟡 还需申请 API key 或完成第三方登录

| 技能 | 一句话职责 | 开箱可用 |
|---|---|---|
| `soia-cwork-feishu-cli` | 通过飞书官方 lark-cli 以最小权限只读调研 Wiki、Drive 与文档。 | 🟡 |
| `soia-cwork-feishu-doc-git-sync` | 将飞书知识库或云文档以应用身份只读同步为本地 Markdown，保留目录、来源和同步元数据，并可接入 Git、Obsidian 与 VitePress。 | 🟡 |
| `soia-cwork-processon-diagrams` | 安全盘点并按授权导出、校验和归档 ProcessOn 图表。 | 🟡 |

## 触发词映射

装完直接用自然语言说话即可，Agent 按下表触发对应技能（完整触发词见各技能 `SKILL.md` 的 `description`）：

| 你说 | 触发技能 |
|---|---|
| `调研飞书知识库` / `读取飞书云盘` / `配置飞书 CLI` | `soia-cwork-feishu-cli` |
| `ProcessOn 盘点` / `导出架构图` / `批量下载图表` | `soia-cwork-processon-diagrams` |

## 安装

推荐装整个领域插件，一次装好本仓全部技能：

```bash
claude plugin marketplace add soia-team/soia-open-skills
```

```bash
claude plugin install soia-cwork-office@soia
```

Codex 用户：

```bash
codex plugin marketplace add soia-team/soia-open-skills
codex plugin add soia-cwork-office@soia
```

只要单个技能时可用 npx 路线。注意技能会落进共享真源 `~/.agents/skills`；
若同时装了插件，同一技能会出现两份索引且各自漂移，建议二选一：

```bash
npx skills add soia-team/soia-open-cwork-office-skills -g -a '*' -s <技能名> -y
```

## 验证与贡献

改动技能后，提交前跑：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/generate_skill_catalog.py --check
python3 scripts/audit_skills.py --strict
```

贡献流程、技能契约与发布步骤见元仓
[CONTRIBUTING.md](https://github.com/soia-team/soia-open-skills/blob/main/CONTRIBUTING.md)。

## 生态导航

规范真源、全生态技能目录与安装指南见 [soia-team/soia-open-skills](https://github.com/soia-team/soia-open-skills)。
维护本仓技能的完整流程见 [CONTRIBUTING.md](https://github.com/soia-team/soia-open-skills/blob/main/CONTRIBUTING.md)。

## License

MIT License — see [LICENSE](./LICENSE).
