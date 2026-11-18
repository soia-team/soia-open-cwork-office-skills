---
name: soia-cwork-processon-diagrams
description: 安全盘点并按授权导出、校验和归档 ProcessOn 图表。触发：ProcessOn 盘点、导出架构图、批量下载图表
version: 1.15.2
created_at: 2026-07-20 18:57:53
updated_at: 2026-11-18 10:00:00
created_by: gpt-5.6-sol
updated_by: gemini-4-hydrogen
---

# soia-cwork-processon-diagrams

通过技能专用 ProcessOn 浏览器 profile 只读读取团队空间、文件夹与图表，按客户授权导出文件，经受管 staging 校验并归档到交付目录，或解析本地 POS/XMind/SVG/图片/PDF 清单。默认只读，严禁修改、分享、移动或删除远端文件。

## 客户可读说明

### 这个技能可以做什么

| 客户想要 | 技能会做 | 客户能看到 |
|---|---|---|
| 递归盘点团队空间与增量对比 | 递归至叶子目录分批审计 checkpoint，或对两份已审计快照运行差分 | 目录/文件树、遗漏差集、权限缺口、增量清单与审计回执 |
| 受控批量导出与无复制归档 | 在独立专用 headless context 内下载至受管 staging，校验后按同盘 move 归档并支持尾单监督续跑 | 下载格式/尺寸/SHA-256、`download-progress.json`、碰撞确认与监督状态 |
| 解析已有本地导出文件 | 用 `inspect_processon_export.py` 提取 POS、XMind、SVG、图片与 PDF 标题/节点/校验值 | 结构化内容清单；VSDX 编辑/升级交给 draw.io 或 Visio 处理 |

### 客户如何使用

- 「初始化盘点这个 ProcessOn 团队空间，只读，不下载：`<team-url>`」
- 「对比两次已审计快照做增量盘点，只列新增、变更与待确认移除候选。」
- 「按已审计计划导出已确认流程图为 VSDX、思维导图为 XMind，归档到 `<output-dir>`。」
- 「解析 `<export-dir>` 里的 POS/XMind，或核对下载清单。」

未指定格式时，流程图默认 `.vsdx`、思维导图默认 `.xmind`（建议同留 `.pos` 原生备份），无法确认的类型只入待人工确认清单；首次登录由客户在独立弹窗手动完成。

### 依赖与安装

默认项目单技能：`npx skills add soia-team/soia-open-cwork-office-skills -a <agent> -s soia-cwork-processon-diagrams`（`<agent>` 换成明确宿主；仅明确选择全局时加 `-g`）。整域需明确选择：`claude plugin marketplace add soia-team/soia-open-skills && claude plugin install soia-cwork-office@soia`（Codex 用 `codex plugin add soia-cwork-office@soia`）。**WorkBuddy** 使用[专家安装说明](https://github.com/soia-team/soia-open-skills/blob/main/docs/install/workbuddy.md)，不由 npx 代装。上述命令不构成安装或发布授权。依赖 Playwright 与专用登录态配置，安装步骤与 [assets/config.example.yml](assets/config.example.yml) 说明见 [setup.md](references/setup.md)。

### 私密信息与中间数据

用户名、密码、短信码、Cookie、Token 与 Local Storage 由独立专用浏览器 profile 保留（拒绝默认 Chrome 目录），绝不读取、写入公共仓或回执；长任务运行包存 `${XDG_STATE_HOME:-~/.local/state}/soia-cwork-processon-diagrams/runs/<run-id>/`，正式批量下载优先放交付根同文件系统 staging 子目录实现无复制归档，临时清理仅处理带技能标记目录。

### 日志与完成回执

汇报扫描/导出/归档范围与缺口；浏览器每批必报模式、专用 profile、`pages_seen_at_start`、`stale_pages_closed`、`scoped_pages_opened/closed` 与 `pages_closed_at_exit`（**正常完成与异常退出都必须返回关闭回执**）；目录盘点完成不得冒充资产归档完成，增量盘点报告两份快照 SHA-256。

## 运行边界与硬门禁

- **独立浏览器与进程约束**：`正式批量只能使用技能专用 profile`；`禁止附着客户默认 Chrome profile`，禁止在客户主 Chrome 跑自动化。专用 context 启动仅保留一父页与至多一临时 popup 且 `finally` 关闭，初始导航遵循 SPA settle（默认 2000ms，细节见 [download-workflow.md](references/download-workflow.md)）。
- **验证与只读边界**：仅当前视口真实可见并阻断的验证码才停下交客户手动接管（隐藏/零尺寸预加载 iframe 不算）；绝不通过猜测 URL/逆向接口越权访问，不绕过认证或代填密码。
- **阶段隔离与单写入**：多 Agent 仅限只读分析，checkpoint、`download-progress.json` 与 `audit` 必须单写入串行；未知类型保留 `unknown` 入人工队列，`collision` 与覆盖须明确确认与 `--collision overwrite --allow-overwrite`。

## 三阶段工作流

1. **阶段 1 · `inventory`（初始化或增量盘点）**：查找官方能力未果时使用 `processon_browser_runner.py` 专用 profile 浏览读取，读 [ProcessOn 能力与格式](references/processon-capabilities.md) 与 [递归盘点中间状态](references/inventory-checkpoint.md)；按 3–6 个目录保存不可变批次并 `audit`（`discovered - visited - blocked = 0` 方可算全量）。增量使用两份通过审计的快照跑 `diff_processon_inventory.py`（有稳定 ID 方报告 move，重复无 ID 隔离至 `ambiguous_entries`）。
   ```bash
   python3 scripts/processon_inventory_state.py init --run-id '<run-id>' --root-path '<logical-root>' --source-url '<team-url>'
   python3 scripts/processon_inventory_state.py audit --run-dir <run-dir>
   python3 scripts/diff_processon_inventory.py --previous <prev>/inventory/checkpoint.json --current <curr>/inventory/checkpoint.json --output <curr>/analysis/inventory-delta.json
   ```
2. **阶段 2 · `archive`（计划、下载与监督归档）**：仅在阶段 1 审计通过后执行，从生成计划、staging receipt、`processon_archive_batch.py`（跨盘 fail-closed）到 `processon_archive_supervisor.py` 续跑与未知/碰撞/VSDX 隔离的完整规则见 [archive-stage.md](references/archive-stage.md)、[download-workflow.md](references/download-workflow.md) 与 [失败模式参考](references/known-failure-modes.md)。
3. **阶段 3 · `analyze`（询问后再解析）**：完成后由客户决定“仅归档 / 提取结构 / 转换 / 升级”；内容盘点用 `inspect_processon_export.py`（缩略图证据不冒充结构原文），VSDX 转编辑由客户用 draw.io/Visio 另行处理。

## 验证与前向测试

```bash
python3 -m py_compile scripts/inspect_processon_export.py scripts/finalize_processon_download.py scripts/processon_inventory_state.py scripts/build_processon_archive_plan.py scripts/processon_archive_state.py scripts/diff_processon_inventory.py scripts/processon_browser_runner.py scripts/processon_archive_batch.py scripts/processon_archive_supervisor.py scripts/prepare_processon_collision_confirmation.py scripts/inspect_processon_unknown_types.py
python3 -m unittest discover -s tests -p 'test_processon_*.py' -v
python3 scripts/audit_skills.py --strict
```

真实前向验证须用临时专用 profile 跑 `status`/`snapshot` smoke（核对进程无主 Chrome、异常退出全关页面）、三级目录递归审计与恢复、`finalize` SHA-256 核对（不把真实 fixture 写入公开文档）；参考索引还包含 [local-check-scripts.md](references/local-check-scripts.md)。
