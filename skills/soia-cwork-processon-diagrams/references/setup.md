# 依赖与安装

> 一次性配置。装好之后日常执行不需要再读本文件。


安装本技能：

```bash
claude plugin marketplace add soia-team/soia-open-skills
```

```bash
claude plugin install soia-cwork-office@soia
```

只要这一个技能时，可用 npx 路线。注意技能会落进共享真源 `~/.agents/skills`；若同时装了插件，同一技能会出现两份索引且各自漂移，建议二选一：

```bash
npx skills add soia-team/soia-open-cwork-office-skills -g -a '*' -s soia-cwork-processon-diagrams -y
```

| 依赖 | 类型 | 安装 / 配置 | 缺失时怎么处理 |
|---|---|---|---|
| ProcessOn 账号与目标资源权限 | 强依赖 | 客户在 ProcessOn 官方页面登录，并确保自己可见目标空间 | 停止远端读取，列出缺失权限 |
| Playwright + Chrome/Chromium | 远端操作强依赖 | `pip install playwright && python -m playwright install chromium` | 改为客户手动导出后解析本地文件 |
| Agent 自带 browser/computer-use | 可选诊断能力 | 只允许客户明确同意的一次性排障；禁止作为正式批量主路线 | 直接使用宿主无关 runner，不影响功能 |
| Python 3.10+ | 本地解析与归档依赖 | 系统 Python 即可 | 只完成浏览器侧盘点/导出 |
| PyYAML | 私有配置可选依赖 | 使用 `config.yml` 时安装：`python3 -m pip install pyyaml` | 改用 CLI 参数、环境变量或安全默认值 |
| `soia-dev-drawio-visio-diagrams` | VSDX 理解/升级可选依赖 | 从同一 SOIA skills 仓安装 | 仍可下载和归档 VSDX，但不做 draw.io 转换与元素级升级 |
| ProcessOn API 服务 | 可选商业能力 | 企业按官方流程申请 JS-SDK/格式转换凭证 | 不影响普通账号的浏览器工作流 |

私有配置默认位置：

```text
~/.config/soia-skills/soia-cwork-processon-diagrams/config.yml
SOIA_CWORK_PROCESSON_DIAGRAMS_CONFIG_FILE=<custom-config-path>
```

配置优先级为 CLI 参数 → 进程环境变量 → 私有 v2 `config.yml` → 只读 v1 配置回退 → 跨平台安全默认值。命中旧 v1 时会输出建议的 `mv` 迁移命令，绝不自动移动数据。配置键、默认路径和命令见 [下载归档工作流](download-workflow.md)。私有配置只保存路径和保留策略，不保存用户名、密码、Cookie、Token 或浏览器 profile。

正式远端操作先执行宿主无关 runner；任何 Claude Code、Codex、Gemini CLI、OpenCode 或普通终端都可调用：

```bash
python3 scripts/processon_browser_runner.py login --url '<team-url>'
python3 scripts/processon_browser_runner.py status --url '<team-url>'
python3 scripts/processon_browser_runner.py snapshot --url '<folder-url>'
python3 scripts/processon_browser_runner.py run \
  --actions <actions.json> --download-dir <managed-temp-dir>
python3 scripts/processon_archive_batch.py \
  --plan <archive-plan.json> --progress <download-progress.json> \
  --team-url '<team-url>' --config <private-config.yml> \
  --workers 1 --limit 12 --dry-run
python3 scripts/processon_archive_supervisor.py \
  --plan <archive-plan.json> --progress <download-progress.json> \
  --team-url '<team-url>' --profile-dir <dedicated-profile> \
  --workers 1 --limit 8 --max-batches 10
```

runner 的 action 文件只允许导航、有名称的语义点击/悬停、滚动、等待、受控 popup、下载、固定的只读 `inspect_text` 结构检查和 ProcessOn 专用 `row_menu`；不接受任意 CSS/无名称控件/调用方脚本，并机械拒绝删除、编辑、移动、分享、发布等远端变更入口；不提供填表、读取 Cookie/Storage 或注入凭据。动作格式和标签生命周期见 [下载归档工作流](download-workflow.md)。
