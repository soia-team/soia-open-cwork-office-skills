---
name: soia-cwork-feishu-cli
description: 用飞书官方 lark-cli 以最小权限只读调研 Wiki、Drive 与文档，或把知识库只读同步为本地 Markdown。触发：「调研飞书知识库」「读取飞书云盘」「配置飞书 CLI」「同步飞书知识库」「飞书备份到 Git」「规划双向同步」
version: 1.1.1
created_at: 2026-07-14 15:26:13
updated_at: 2026-11-18 10:00:00
created_by: claude opus 4.6
updated_by: gemini-4-hydrogen
---

# soia-cwork-feishu-cli

通过飞书官方 `lark-cli` 执行最小权限**只读调研**，或将授权知识库**单向同步为本地 Markdown / Git / VitePress / Obsidian**。默认以应用身份 `bot`（`tenant_access_token`）只读运行；不猜测个人资源、不修改远端内容。

## 客户可读说明

### 这个技能可以做什么

| 客户想要 | 技能会做 | 客户能看到 |
|---|---|---|
| 调研飞书 Wiki 与云盘结构 | 分离 Wiki 与 Drive 接口，按 bot（或授权后 user）盘点空间、节点、文档类型与权限 | 结构化盘点、已访问范围、bot 不可见缺口与证据命令 |
| 读取、总结或评估迁移飞书文档 | 识别 URL 类型并按 `lark-doc` 规则读取正文与大纲 | 文档摘要、引用位置与迁移限制 |
| 初始化与诊断 `lark-cli` 凭证 | 用 stdin 安全写入应用 profile，校验身份、token、scope 与错误码 | 脱敏连通性状态与最小需开 scope/发布指南 |
| 同步飞书知识库到本地 / Git | 以 bot 只读增量镜像为 Markdown，保留层级、来源与同步元数据 | 镜像目录、同步清单、失败清单与验收结果 |

### 客户如何使用

- 「用飞书 CLI 盘点我可见的知识库层级和云盘文档类型，不要修改任何内容。」
- 「读取这个飞书 Wiki 页面，评估适合迁移到哪类本地知识库。」
- 「检查 `lark-cli` 认证报错并列出最小需开通权限。」
- 「把这个飞书知识空间按增量模式同步成本地 Markdown 并做完整性验收。」

### 依赖与安装

默认项目单技能：`npx skills add soia-team/soia-open-cwork-office-skills -a <agent> -s soia-cwork-feishu-cli`（`<agent>` 换成明确宿主；仅明确选择全局时加 `-g`）。整域需明确选择：`claude plugin marketplace add soia-team/soia-open-skills && claude plugin install soia-cwork-office@soia`（Codex 用 `codex plugin add soia-cwork-office@soia`）。**WorkBuddy** 使用[专家安装说明](https://github.com/soia-team/soia-open-skills/blob/main/docs/install/workbuddy.md)，不由 npx 代装。上述命令不构成安装或发布授权。环境需 Node.js/npx 与官方 `lark-cli`（`npx @larksuite/cli@latest install`）；知识库同步脚本依赖 Python 3.10+ 与 PyYAML。

### 私密信息与中间数据

凭证配置仅存 `${XDG_CONFIG_HOME:-~/.config}/soia-skills/soia-cwork-feishu-cli/config.yml` 或 `$SOIA_CWORK_FEISHU_CONFIG_FILE`（模板见 [assets/config.example.yml](assets/config.example.yml)）；同步配置独立存 `doc-git-sync.yml` 或 `$SOIA_CWORK_FEISHU_DOC_GIT_SYNC_CONFIG_FILE`（只读兼容旧路径，示例见 [assets/doc-git-sync.config.example.yml](assets/doc-git-sync.config.example.yml)）。绝不把 App Secret、access token、cookie、真实 App ID 或本机绝对路径/系统用户名写入公共库、vault 或回复日志；脱敏规则见 [references/doc-git-sync-output-redaction.yml](references/doc-git-sync-output-redaction.yml)。

### 日志与完成回执

汇报 `started`（CLI 版本、profile 名称、`bot`/`user` 身份与非敏感范围）、`processed`（空间/节点/文档数与分页完整性）、`created/updated`（默认“未改动远端”；同期同步报脱敏状态与数量）、`skipped/failed`（权限/可见性缺口与错误分类）及 `verification`。

## 身份分流、凭证与权限规则

1. **Wiki vs Drive 与 `bot` vs `user`**：Wiki（[wiki-workflow.md](references/wiki-workflow.md)）与 Drive（[drive-workflow.md](references/drive-workflow.md)）接口分离。应用身份 `bot` 只读取授权给应用的空间/文件夹；只有用户明确指定个人云盘、`my_library` 或 `--as user` 时才启用用户 OAuth（device flow 经 `lark-cli auth login --no-wait --json` 生成二维码交用户扫码完成，不得因 bot 搜索为空就静默切 user OAuth）。
2. **初始化应用 profile（stdin 传密，禁止命令行裸传）**：
   - `python3 <skill-path>/scripts/setup_app_credentials.py --use`（首次）或 `--replace --use`（仅用户明确要求覆盖同 App ID 旧 profile 时）。
   - 安装/初始化后运行：`lark-cli doctor`、`lark-cli profile list`、`lark-cli auth status --json --verify`、`lark-cli whoami`。
3. **诊断区分**：遇 `invalid_client`、`20140` 或“auth method is not supported”先按 [errors.yml](references/errors.yml) 修 profile 凭证，不误判为缺 scope；缺 scope 时按 [permissions.yml](references/permissions.yml) 与 [permissions.md](references/permissions.md) 分出「必需 / 可选 / 不要默认开通」，明确提示需完成**开通保存 → 数据可见范围 → 创建版本并提交审核通过**闭环（「审核中」不算生效）。

## 只读调研与知识库同步工作流

### A. 只读调研

1. 运行 `auth status --json --verify` 核对真实身份（以 `identities.bot`/`user` 与 `--as` 为准）。
2. 执行 `lark-cli skills read lark-shared` 及对应领域嵌入说明（`lark-wiki` / `lark-drive` / `lark-doc`），不凭 `--help` 猜参。
3. Wiki 用 `wiki +space-list` / `wiki +node-list`；Drive 按身份搜索；读文档先 `drive +inspect --as <bot|user> --url '<URL>' --format json` 再 `docs +fetch`（命令细则见 [references/cli-workflows.md](references/cli-workflows.md)）。
4. 写操作、导出（`drive +export`/`docs +media-download`）、加权、删除或 `confirmation_required` 一律先展示范围、命令与影响等用户明确确认；禁止静默 `--yes`。

### B. 知识库同步到本地（`sync_feishu_wiki.py`）

全量细节（14 步用法、增量与 ID 策略、Sheet/多维表格/附件边界、Git/VitePress 接入）见 [references/doc-git-sync.md](references/doc-git-sync.md)，执行真实同步前必读：

```bash
python3 <skill-path>/scripts/sync_feishu_wiki.py --config <private-config.yml> --dry-run
python3 <skill-path>/scripts/sync_feishu_wiki.py --config <private-config.yml> --output-dir <pilot-dir> --pilot-node-token <token> --incremental
python3 <skill-path>/scripts/sync_feishu_wiki.py --config <private-config.yml> --incremental
python3 <skill-path>/scripts/sync_feishu_wiki.py --config <private-config.yml> --retry-failed --retry-batch-size 100 --skip-assets
python3 <skill-path>/scripts/sync_feishu_wiki.py --config <private-config.yml> --validate-only
```

只使用 `--as bot`；Sheet / Bitables 值镜像仅在用户明确勾选有界范围并写入私有配置后开启，不顺带全表导出或自动 Git commit/push；`--validate-only` 非 0 退出、`failed`/`stale` 或语义缺口均不视为完成。

## 参考资料与验证

- 参考：[cli-workflows.md](references/cli-workflows.md)、[providers.md](references/providers.md)、[permissions.yml](references/permissions.yml)、[permissions.md](references/permissions.md)、[errors.yml](references/errors.yml)、[doc-git-sync.md](references/doc-git-sync.md)（及其 `doc-git-sync-*.yml` 策略/脱敏子文件）。
- 单测与前向测试：先用脱敏 fixture 或授权代表性只读对象核对身份、分页数量与脱敏回执，再运行仓库校验：

```bash
python3 -m unittest tests.test_feishu_cli_setup tests.test_feishu_doc_sync
python3 scripts/audit_skills.py --strict
```
