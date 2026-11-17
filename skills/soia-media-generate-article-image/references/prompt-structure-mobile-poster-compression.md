# 信息结构：长文转海报 30–45 秒手机阅读压缩与公众号配图规划（Mobile Poster Compression & WeChat Visual Plan）

本文件定义 `information_structure=mobile_poster_compression`，吸收 `xxd-article-poster` 的「30–45 秒手机阅读认知压缩法则」与 `@cheery9998` 公众号全自动配图工作流的「封面类型映射 + 正文三类配图克制原则」。适用于将长文章、公众号草稿、网页或笔记提炼为单张/系列竖版信息海报（`3:4` / `4:5`）、公众号正文小结卡（`16:9` / `3:2`）及成套文章配图。

## 一、30–45 秒手机端阅读预算（Reading Budget，硬门禁）

把长文变成海报或信息图时，**严禁逐行照搬原文或靠缩小字号硬塞“小字墙”**。必须先按以下 5 步认知顺序提炼：
1. **核心主张（Central Claim）**：这篇文章最重要的一句话判断是什么？
2. **痛点共鸣（Why Care）**：读者为什么要花 30 秒看这张图？
3. **关键支撑（Key Points）**：3–4 组最具说服力的论据、步骤或对比是什么？
4. **认知迁移（Shift）**：看完后读者的行动或认知会发生什么改变？
5. **记忆金句（Memory Line）**：最后留在脑海里的一句话是什么？

### 单张海报字数预算（超出必须拆分为多张系列图，不得缩小正文字号）
- **主标题（Title）**：`8–18` 个汉字（或 `3–8` 个英文词），超大字号承担第一视觉锚点。
- **核心判断句（Core Claim）**：`1` 句强有力的话（≤30 字）。
- **信息分组（Sections）**：`3–4` 组，每组 `1–3` 条短句或短语（用具象名词、动词、数字和对比，删掉过渡套话）。
- **全图可见正文总预算**：**≤ `220` 个汉字**（或 `≤ 120` 英文词）。若源材料信息量无法在 220 字内讲清，自动切换 `batch_strategy=adaptive_series` 拆分为 2–4 张同底座海报。

## 二、四种海报信息骨架（Structure Archetypes）

编译 Prompt 时必须从以下 4 种骨架中显式指定 1 种：
1. **`one_shot_insight`（一击洞察型）**：超大主标题 + 1 句核心判断 + 3 个并列支撑块 + 底部记忆金句。
2. **`framework`（方法框架型）**：主标题 + 顶层原则 + 编号分组模块（`01 / 02 / 03 / 04`）+ 底部边界说明。
3. **`contrast`（前后对比型）**：误区 vs 真相 / 手动繁琐 vs 自动工作流 / 破局前 vs 破局后双栏或上下对照。
4. **`process`（步骤流水线型）**：3–5 个清晰步骤节点（带流向箭头）+ 1 条关键避坑提示。

## 三、微信公众号封面与正文配图规划法则（联动 `publish-wechat-draft`）

当输入为微信公众号文章（或同时需要导出公众号 + X 双端配图）时，按以下规则规划全套视觉资产：

### 1. 双端封面规格与内容类型风格路由
- **公众号首图（`wechat-cover.png`）**：固定 `2.35:1`（标准 `900×383` 或 2x 高清 `1880×800`），中央 `1:1`（`383×383` / `800×800`）为朋友圈/聊天转发裁切安全区；封面上最多放 1–2 行精炼大字标题（或采用 `wechat_editorial_clean` 纯意境无字封面），绝不堆砌小字。
- **X Article 封面（`x-cover.png`，可选联动）**：固定 `5:2` 比例，与公众号首图共享同一视觉母题。
- **按文章类型自动匹配封面视觉方向**：
  | 文章内容类型 | 封面视觉方向 | 推荐组合轴 |
  |---|---|---|
  | AI 工具教程 | 干净科技感、焦点明确 | `poster_type_stage × oversized_type × bright_modern` |
  | 工作流 / 自动化教程 | 桌面、文件、流程结构感 | `presentation_grid × modular_grid × editorial_aesthetic` |
  | 强观点 / 商业评论 | 杂志感、大字标题突出、高对比留白 | `poster_type_stage × oversized_type × editorial_aesthetic` |
  | 个人经验 / 人文随笔 | 浅色宣纸感、温和留白或中国传统构图 | `archival_print × chinese_traditional_composition × chinese_traditional_colors` |

### 2. 正文配图「三类克制原则」（禁止每小节机械配图）
公众号长文配图过多会打断阅读节奏，整篇正文配图控制在 **2–4 张**，只保留以下三类功能图，并在 manifest 中写明每张图的插入锚点（置于哪一节/哪一段之后）：
1. **流程图 / 架构图（Workflow Diagram）**：在最需要解释步骤、机制或前后对比的章节插入（采用 `process` 或 `contrast` 骨架）。
2. **结果预览 / 真实证据图（Proof / Preview）**：展示真实产出、界面或数据对照，证明工作流真实跑通。
3. **意境分隔图（Atmospheric Divider）**：在长文转折处缓解阅读疲劳，承载 1 句核心金句与 2–3 条凝练判断。

## 四、编译字段与验收（Level B 契约）

- `compile_fields`：
  - `poster_archetype`：`one_shot_insight | framework | contrast | process` 四选一
  - `cognition_flow_5_steps`：核心主张、痛点、3–4 组要点、认知改变、记忆金句
  - `mobile_text_budget`：标题字数（8–18 字）、分组数（3–4 组）与全图可见总字数（≤220 汉字）核算
  - `inline_figure_role_and_anchor`：若为文章正文配图，标明属于「流程图 / 预览证据图 / 意境分隔图」哪一类及插入段落位置
- `acceptance`：
  - `readable_in_30_to_45_seconds`：手机默认预览尺寸下主标题一眼可读，阅读路径唯一清晰
  - `no_wall_of_small_text`：全图可见正文 ≤220 字，无密集小字段落墙
  - `no_orphan_single_char_line`：核心判断句、卡片描述与底部金句不得出现“句末单字孤行（如单独一行只有『余。』『上。』『座。』）”，发现孤字成行时微调字号或字距使其单行收拢
  - `no_html_or_watermark_noise`：画面干净，无伪造二维码、无水印、无多余英文装饰标签
