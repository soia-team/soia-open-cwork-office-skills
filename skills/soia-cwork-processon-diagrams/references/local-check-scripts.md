# 本地检查脚本


`scripts/inspect_processon_export.py`：

- 支持单文件或目录（可递归）。
- 解析 POS 的元数据、流程图元素或思维导图节点文字。
- 解析 XMind 的 `content.json` / `content.xml` 主题文字。
- 读取 PNG/JPEG/GIF/WebP 尺寸、SVG 文字与 `viewBox`。
- 对 PDF 和其他文件至少记录大小、扩展名与 SHA-256。
- 默认只读，不修改源文件。

`scripts/finalize_processon_download.py`：

- 解析 CLI、环境变量、私有 YAML 和跨平台默认路径。
- 初始化带安全标记的临时目录，拒绝认领非空共享目录。
- 先检查再原子复制；默认同名改名，覆盖需要双重显式开关。
- 仅对受管临时目录开放 `--move` 和清理；move 要求同一文件系统，使用 hard-link + atomic replace，manifest 提交后才 unlink 源文件。
- 交付目录和审计目录不得放在临时目录内部。

`scripts/processon_inventory_state.py`：

- 初始化独立 JSON 检查点，原子保存 `discovered_paths`、`visited_paths`、`blocked_paths` 和逐目录文件清单。
- 每个浏览器小批次通过 `record` 幂等合并；同一完整目录快照重复记录不会重复累计文件。
- `record` 自动刷新 `handoff/progress.md`；原始批次同时记录语义哈希和落盘文件哈希。
- `status` 直接计算可恢复的 `pending = discovered - visited - blocked`，会话中断后从 `pending_paths` 继续。
- `audit` 逐批校验 SHA-256、重新构造 checkpoint、核对 `run.json`；只有差集和受阻项均为 0 才生成完成回执。
- 拒绝相对路径穿越、符号链接状态文件和无效 schema；状态文件权限为 `0600`。

`scripts/build_processon_archive_plan.py`：

- 从已持久化 checkpoint 生成资产归档计划，不访问 ProcessOn、不读取凭据，也不下载文件。
- 输出每个条目的稳定 `artifact_id`、默认导出格式、POS 回退策略、unknown 确认队列和同名风险，并区分 `ready_for_known_artifacts` 与全量 `ready_for_archive`。
- 在归档前验证 checkpoint SHA-256、条目内容和阶段标志，防止目录盘点更新或计划被改写后继续使用旧计划。

`scripts/processon_archive_state.py`：

- 从已验证的归档计划初始化或恢复单写入者下载队列，计划 SHA-256 不一致时拒绝合并旧进度。
- `next` 给出下一批可执行 artifact；`record` 将实际文件、finalizer manifest、大小和 SHA-256 绑定到稳定 artifact_id；`mark` 单列失败和阻断；`reopen` 把旧完成证据无复制移入同盘持久隔离区并进入 `revalidation_pending`，重下成功后自动清除该状态。隔离区不要放在会被保留期清理的 `_staging` 下。
- `audit` 重放已完成证据并重新计算计数；进度文件原子写入、权限为 `0600`，未知类型只进入人工确认队列。

`scripts/processon_browser_runner.py`：

- 从任何 AI host/普通终端启动技能专用 Playwright profile，不依赖 Codex/Claude 浏览器工具，也不附着客户主 Chrome。
- `login` 只负责独立窗口中的人工登录；`status`、`snapshot`、`run` 默认 headless。
- action 合同只允许 ProcessOn HTTPS 导航、有名称的语义 click/hover、scroll、wait、受控 popup 和下载；拒绝任意 CSS、无名称控件和远端变更标签，没有填表、脚本执行、Cookie/Storage 或凭据接口。
- 启动时关闭专用 profile 的陈旧多余页面；每个 popup 在 `finally` 中关闭，整次 context 在所有正常/异常退出路径关闭并输出计数回执。

`scripts/processon_archive_batch.py`：

- 使用一个技能专用 persistent context 和最多三个固定 headless worker 页；不附着客户主 Chrome，不为每份 artifact 累积新标签。
- `--workers > 1` 必须提供同一计划的并发语义 proof；两路和三路分别验证；collision-risk 项在所有 worker 数下均不进入自动批次。
- 浏览器下载可并行；finalize、metadata、source-links、record、进度镜像和 audit 在全局 orchestrator lock 内单写入。
- 每份 artifact 使用 `<managed-root>/<run-id>/<artifact_id>/` 独立 staging；源标题含路径分隔符时只转义标题组件并附 artifact_id，不能把标题误拆成子目录。
- VSDX 读取页面文字并匹配标题特征，XMind 核对根标题；建议文件名正确但文件内语义不匹配时保持 pending，不写进度。
- VSDX 菜单使用有界的精确标签候选，优先全画布导出并兼容当前编辑器全角空格标签及单画布旧标签；标题点击可同页进入编辑器或打开 popup，实际命中的菜单标签写入 artifact metadata 与批次回执。
- 每批生成不可变 JSON receipt，并对 worker 页、popup 与 context 的关闭数做机械对账；历史进度里直接来自 `~/Downloads` 的所有平铺文件都会进入复核清单，不能因未带 `(n)` 或旧状态写成 completed 就省略来源复核。

`scripts/prepare_processon_collision_confirmation.py`：

- 从当前计划与进度生成 plan-bound、inventory-order 的私有碰撞确认，并用批处理的同一严格 loader 回放验证。
- 只包含尚未完成、类型已知、且计划明确标为 collision-risk 的条目；拒绝 unknown、非碰撞项、计划漂移和无显式确认开关。

`scripts/inspect_processon_unknown_types.py`：

- 在技能专用 profile 中逐项只读观察 ProcessOn 固定文件行图标，输出 plan-bound 的 unknown 类型证据。
- 固定图标不能唯一证明类型、标题行无法唯一定位或目录漂移时记录错误并保持 unknown，不修改计划和进度。

`scripts/processon_archive_supervisor.py`：

- 只顺序调用同一技能的 bounded batch runner；不使用 Codex/Claude computer-use，也不附着客户主 Chrome。
- 每批后重放 archive state audit 并原子保存私有状态；`--max-batches` 是强制上限，异常、碰撞和未分类 pending 一律停止。
- 仅消费 batch receipt 中可精确证明的 XMind 菜单缺失，不凭标题、浏览器下载列表或猜测自动标记失败。

`scripts/diff_processon_inventory.py`：

- 对两个完整 checkpoint 做 fail-closed 的本地快照差分，不访问 ProcessOn、不读取凭据，也不宣称事件/API 增量。
- 仅在稳定 `remote_id/id` 存在时标记移动或重命名；无 ID 文件按安全回退身份比较，无法确认移动时保留新增/移除候选。
- 拒绝 incomplete、blocked、跨范围和重复的稳定 ID；重复无 ID 身份隔离为 `ambiguous_entries`，移除只保留 `removed_candidates`，从不驱动远端或本地删除。
