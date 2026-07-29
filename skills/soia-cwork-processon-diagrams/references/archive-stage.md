# 阶段 2：`archive` — 下载并归档已确认源文件

> 仅在阶段 1 产生最新、完整且审计通过的 checkpoint 后开始。
> 三个阶段互斥，本文件只在真正执行 archive 时读取。


仅在阶段 1 产生**最新、完整且审计通过**的 checkpoint 后开始。先生成和验证可恢复的资产计划：

```bash
python3 scripts/build_processon_archive_plan.py build \
  --checkpoint <run-dir>/inventory/checkpoint.json \
  --output <run-dir>/artifacts/archive-plan.json
```

脚本为每个条目生成稳定 `artifact_id`、完整逻辑目录、类型证据、默认格式、回退格式、同名风险和状态；流程图默认 `vsdx`，思维导图默认 `xmind`，`unknown` 只进入 `pending_confirmation`。`ready_for_known_artifacts=true` 且 `archive_status=known_ready_pending_confirmation` 时可以下载已确认类型，但不得宣称全量资产归档完成；`ready_for_archive=true` 才代表全部条目均可进入归档。盘点继续变化或恢复后，先重新生成计划，禁止沿用旧计划。

下载过程中用以下命令检查计划仍对应当前 checkpoint：

```bash
python3 scripts/build_processon_archive_plan.py verify \
  --plan <run-dir>/artifacts/archive-plan.json \
  --checkpoint <run-dir>/inventory/checkpoint.json
```

校验失败时停止下载，先重新生成计划；不要把陈旧计划中的成功数合并到新一轮报告。然后按计划逐项执行：

先初始化或恢复正式下载队列，并按计划顺序领取下一小批：

```bash
python3 scripts/processon_archive_state.py init \
  --plan <run-dir>/artifacts/archive-plan.json \
  --progress <run-dir>/artifacts/download-progress.json
python3 scripts/processon_archive_state.py next \
  --plan <run-dir>/artifacts/archive-plan.json \
  --progress <run-dir>/artifacts/download-progress.json \
  --limit 10
```

`init` 在进度文件已存在时只接受相同计划 SHA-256，保留既有成功/失败/阻断证据并机械刷新计数；计划变更时 fail closed。`next` 默认跳过已完成、失败和阻断项，显式重试时才使用 `--include-failed` 或 `--include-blocked`。

批处理默认同样跳过终态。若某个**已失败**条目已有针对性的修复证据，只能以 `--retry-failed` 加逐条 `--artifact-id` 重试；缺少任一开关、ID 重复、ID 不在当前计划或当前并非 `failed` 都会 fail closed。该入口不会重试 `blocked`、未知项、碰撞项或整条失败队列：

```bash
python3 scripts/processon_archive_batch.py \
  --plan <run-dir>/artifacts/archive-plan.json \
  --progress <run-dir>/artifacts/download-progress.json \
  --team-url '<team-url>' --config <private-config.yml> \
  --retry-failed --artifact-id '<artifact-id-1>' --artifact-id '<artifact-id-2>' \
  --workers 1 --limit 2 --dry-run
```

已阻断条目只有在原阻断条件已经消失、且新的验证路径可复现时，才用 `--retry-blocked` 加精确 artifact 白名单重试。它与 `--retry-failed` 互斥，并拒绝 unknown、未确认项和 collision-risk 项；安全隔离和空画布不能仅靠重跑解除：

```bash
python3 scripts/processon_archive_batch.py \
  --plan <run-dir>/artifacts/archive-plan.json \
  --progress <run-dir>/artifacts/download-progress.json \
  --team-url '<team-url>' --config <private-config.yml> \
  --retry-blocked --artifact-id '<artifact-id>' \
  --workers 1 --limit 1 --dry-run
```

归档计划已经标记同目录同标题碰撞时，先生成与当前计划 SHA-256 绑定的私有顺序确认，再以单 worker 专用批次执行。该确认只授权**计划中已经存在的碰撞组**，不能替代未知类型确认，也不能给盘点后新出现的同名搜索结果强行绑定：

```bash
python3 scripts/prepare_processon_collision_confirmation.py \
  --plan <run-dir>/artifacts/archive-plan.json \
  --progress <run-dir>/artifacts/download-progress.json \
  --output <run-dir>/artifacts/collision-confirmation.json \
  --confirm-inventory-order
python3 scripts/processon_archive_batch.py \
  --plan <run-dir>/artifacts/archive-plan.json \
  --progress <run-dir>/artifacts/download-progress.json \
  --team-url '<team-url>' --config <private-config.yml> \
  --collision-confirmation <run-dir>/artifacts/collision-confirmation.json \
  --workers 1 --limit 10 --dry-run
```

盘点计划中的 unknown 只能用固定 ProcessOn 文件行图标做只读观察；标题行不存在、同名不可唯一定位或 provider 图标不唯一时继续保留 unknown，和客户一起确认：

```bash
python3 scripts/inspect_processon_unknown_types.py \
  --plan <run-dir>/artifacts/archive-plan.json \
  --progress <run-dir>/artifacts/download-progress.json \
  --team-url '<team-url>' \
  --output <run-dir>/artifacts/unknown-type-observation.json
```

1. 先用 runner `snapshot` 取得当前目录可见文字和语义控件，再生成小批次 action JSON；根据快照定位目标的“下载/导出”，不依赖固定坐标或私有 CSS。点击文件标题后，兼容官方同页进入编辑器和新 popup 两种行为；在编辑器中按可见的“文件 → 导出为”导出，不再假定列表行菜单始终存在。ProcessOn 文件列表可能虚拟化；目标条目未进入当前视口时先用 `scroll` 并重新快照，不能把定位超时写成文件不存在。按已确认类型选择：
   - 流程图默认导出 `.vsdx`；runner 先选当前编辑器的 `导出全部画布 （.vsdx）`/旧标签 `导出全部画布 (.vsdx)` 保留全部画布，该项不可见时再兼容 `VISIO文件` / `VISIO文件 beta`；
   - 思维导图默认选 `Xmind文件`/`.xmind`；其官方编辑器的 `导出为` 是一级菜单，不要求也不尝试流程图的“文件”菜单。定位同时检查可见正文和 ProcessOn 已见的 `aria-label`、`title`、`data-title`、`data-tooltip`，但只接受技能内固定白名单的菜单名，不接受调用方 CSS；
   - 无法确认类型的文件加入“待人工确认”清单，不自动打开下载菜单。
   默认格式不可用、会员/权限阻断或下载失败时回退 POS，并明确记录“原请求格式、实际格式、降级原因”，不得静默替换。列表页点击无文件时进入官方编辑器重试 XMind/POS/POSM；若这些原生格式均无文件、但 Markdown 能下载，只能把 Markdown 作为诊断证据并将 artifact 标为 `blocked`，不能把 Markdown 冒充 XMind/POS 完成。格式选择见 [ProcessOn 能力与格式](processon-capabilities.md)。
2. 首次需要受管临时目录时运行 `paths --ensure`；由 runner `download` 动作捕获真实下载事件，不凭 Toast 判断成功。正式 batch 用 `download.save_as(<managed-root>/<run-id>/<artifact_id>/<原文件名>)` 直接保存，每个 artifact 独占目录；随后结构/语义校验并用同文件系统 hard-link + atomic replace + unlink 完成 no-copy move，manifest 落盘失败时回滚目标且保留源文件。ProcessOn 导出是异步任务：同一 worker 页一次只发起一个 artifact，必须等真实落地并完成结构/语义校验后才能切换条目。默认串行；只有存在当前计划对应的 `concurrency-proof.json`，且两份独立 VSDX/XMind 的文件内文字反证均通过时，才允许 `processon_archive_batch.py --workers 2`。并发下载仍由一个 writer 串行执行 finalize、`metadata.yml`、source-links、record、进度镜像和 audit。同目录同标题的 collision-risk 项在 1 路和多路模式下都不进入自动队列，必须取得行级稳定 ID/URL 或人工逐项确认后走专用流程。固定 `sleep`、点击成功和短时下载事件超时都不能替代落盘信号。临时源页面必须在下载完成/失败后的 `finally` 中关闭。先 dry-run，再归档。下面的单文件 finalizer 默认复制；正式 batch 自动显式使用 `--move`：

```bash
python3 scripts/finalize_processon_download.py paths --ensure
python3 scripts/finalize_processon_download.py finalize <browser-downloaded-file> --dry-run
python3 scripts/finalize_processon_download.py finalize <browser-downloaded-file>
```

3. 核对文件非空、扩展名与内容类型一致；VSDX 必须是有效 ZIP/OOXML 且包含 `visio/document.xml`，图像核对尺寸，POS/XMind 核对标题和可提取文字，所有文件记录 SHA-256。VSDX 提取文字后、写入交付目录前必须扫描疑似明文凭据赋值和对象存储预签名 URL 参数；命中 `密码/password/passwd/pwd` 或 `X-Amz-Credential/X-Amz-Signature` 时只报告命中类型和数量，不回显秘密值，原件进入受限隔离区，不得直接写入 Git 归档。客户明确要求解决该安全阻断时，只能用 `--retry-blocked --redact-security-block <artifact_id>` 从经 SHA-256 校验的隔离证据生成同格式 `--sanitized.vsdx` 副本；副本必须复扫零命中，metadata 记录 `sanitized_derivative`、原件 SHA 和命中类型/数量，原件继续隔离。文件名只能作为候选证据：若异步队列产生 `(1)` 后缀、标题漂移或同名不同 SHA，调用 `soia-dev-drawio-visio-diagrams` 提取 VSDX 页面文字反证来源；无法唯一对应 artifact 时保持 pending，不移动、不改名、不调用 `record`。VSDX 完整标题片段未出现时，只有在稳定 `remote_id/source_url` 已先核对、且文件内至少命中两个互不重叠的中文二字片段时，才允许以 `chinese_bigram_pair` 作为补充语义证据；单个泛词不得放行。状态脚本直接拒绝把个人 `~/Downloads` 根目录中的任何平铺文件登记为完成；未编号的第一份也无法证明来源绑定。历史平铺记录从可信 `completed` 排除并列入 `revalidation_pending`，先用 `reopen` 原子移入隔离区、回到可领取队列，再按 artifact_id 独立重下。批量下载只能逐个执行；官网未明确支持时不声称存在批量 API。清理临时目录必须先 `cleanup --dry-run`，再由客户确认。

历史批量迁移使用换行分隔的 artifact 清单，整个状态提交失败时归档文件会回滚原位：

```bash
python3 scripts/processon_archive_state.py reopen \
  --plan <run-dir>/artifacts/archive-plan.json \
  --progress <run-dir>/artifacts/download-progress.json \
  --artifact-id-file <run-dir>/artifacts/revalidate-artifact-ids.txt \
  --reason "legacy flat Downloads source requires revalidation" \
  --quarantine-dir <archive-root>/_quarantine/<run-id>/legacy-flat
```

4. `finalize` 成功后立即把 artifact、实际交付文件和 finalizer manifest 绑定到进度；下载事件未被浏览器观察到但真实文件已校验时，使用 `not_observed_verified_file`，不能误写成浏览器事件成功：

```bash
python3 scripts/processon_archive_state.py record \
  --plan <run-dir>/artifacts/archive-plan.json \
  --progress <run-dir>/artifacts/download-progress.json \
  --artifact-id <artifact-id> \
  --download-source <browser-downloaded-file> \
  --destination <archived-file> \
  --manifest <finalizer-manifest.json> \
  --actual-format vsdx \
  --download-event not_observed_verified_file
```

会员限制、真实可见验证码、权限或格式失败用 `mark --outcome blocked|failed --reason <reason>` 落盘；不得混入未开始项。已有诊断文件时用可重复的 `--evidence-file` 归档到运行包，状态脚本会复制、哈希并在 `audit` 时重放，避免 `Downloads` 清理后证据消失：

```bash
python3 scripts/processon_archive_state.py mark \
  --plan <run-dir>/artifacts/archive-plan.json \
  --progress <run-dir>/artifacts/download-progress.json \
  --artifact-id <artifact-id> \
  --outcome blocked \
  --reason "XMind/POS/POSM 无文件落盘；Markdown 仅用于证明下载通道可用" \
  --evidence-file <diagnostic-export.md>
```

同目录同名条目没有稳定远端 ID 时，交付目录必须附加稳定 `artifact_id`（建议前 8 位），例如 `未命名文件--8ba9f60f/`；不能只依赖浏览器自动生成的 `(1)` 文件名。每批结束运行 `audit`，重新检查计划指纹、计数、交付文件、阻断证据、SHA-256 和 finalizer manifest：

```bash
python3 scripts/processon_archive_state.py audit \
  --plan <run-dir>/artifacts/archive-plan.json \
  --progress <run-dir>/artifacts/download-progress.json
```
