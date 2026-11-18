---
name: soia-media-generate-article-image
description: 将文章、开源项目、品牌 Logo 或公开 X Prompt Deck 编译为可验收的图片与矢量资产，按组合轴生成 Prompt 并完成事实、文字和视觉验收。
version: 3.18.1
created_at: 2026-07-09 20:56:44
updated_at: 2026-11-18 10:00:00
created_by: claude opus 4.6
updated_by: gemini-4-hydrogen
---

# soia-media-generate-article-image

生成真实 PNG/JPG 与 SVG 矢量资产，并为每次生成保存可复用 Prompt、来源边界、manifest 和验收结果。本技能不负责文章排版、平台发布、账号登录或凭空补写事实。

## 客户可读说明

### 这个技能可以做什么

| 客户想要 | 技能会做 | 客户能看到 |
|---|---|---|
| 公众号全套配图（首图 + 分享图 + 正文图） | 生成 `2.35:1` 首图（中央 `1:1` 分享安全区必检）、可选 `5:2` X 封面，并按「流程图 / 预览证据图 / 意境分隔图」克制规划 1–3 张正文图 | 成套高清 PNG、中央 `1:1` 裁切复核、文字与垂直重心验收及插入位置清单 |
| 长文转「45 秒一图读懂」手机海报 | 提炼核心主张与 3–4 组要点，严控全图可见正文 ≤220 字且无孤字成行 | `3:4` 或 `4:5` 竖版信息海报、Prompt 与字数预算核验 |
| 国风 / 人文随笔意境封面与插图 | 调用中国传统构图 20 式（平远法、一河两岸式、边角式、折枝式、计白当黑等）与 742 种中华传统色 | 宣纸水墨质感封面/插图，无色彩 HEX 参数泄露 |
| 康奈尔学习笔记信息图（1–6 页） | 运行 `resolve_cornell_pagination.py` 按知识模块自适应分页（每页 6–10 个模块，左上角 `NN/TT` 页码） | 高密度单页或系列 PNG、系列 `manifest.yml` |
| 开源仓库推荐 / 小红书轮播 / 品牌 Logo | 提取仓库/技能真实事实生成宣传卡或 `3:4` 轮播；插件图标与品牌 Logo 先出方向稿再确定性 SVG 矢量重绘 | 事实清单、通过 OCR/二维码校验的卡片，或 SVG 母版 + 彩色/单色/反白变体 |
| 公开 X Prompt Deck 进化导入 | 拆分基础视觉系统、主题佐料与系列变量，转化为本技能可复用的组合轴 | `evolution.yml`、`series-index.yml`、`bibles/` 与重编译 `prompts/` |

### 客户如何使用

用自然语言说明**来源（文章路径/主题/仓库）、用途或投放平台、张数与必须逐字出现的文字**，内部轴与默认值由 Agent 自动推导并回显确认：

- 「帮这篇公众号文章生成一张 `2.35:1` 首图（平远法 + 中华传统色）和 2 张正文配图，主标题写『行至半山，止息内耗』。」
- 「把这篇文章压成一张 45 秒能看懂的 `3:4` 竖版手机海报，采用方法框架型结构。」
- 「把这篇长文做成康奈尔笔记信息图；如果有参考图，按参考图的版式密度分页。」

### 依赖与安装

默认项目单技能：`npx skills add soia-team/soia-open-cwork-office-skills -a <agent> -s soia-media-generate-article-image`（`<agent>` 换成明确宿主；仅明确选择全局时加 `-g`）。整域需明确选择：`claude plugin marketplace add soia-team/soia-open-skills && claude plugin install soia-cwork-office@soia`（Codex 用 `codex plugin add soia-cwork-office@soia`）。**WorkBuddy** 使用[专家安装说明](https://github.com/soia-team/soia-open-skills/blob/main/docs/install/workbuddy.md)，不由 npx 代装。上述命令不构成安装或发布授权。`imagegen` 路径依赖 imagegen + `view_image`；`html_render` 路径只需无头浏览器，不依赖 imagegen。本技能不保存 API key、cookie 或 token。

### 私密信息与中间数据

配置放私有 skill-specific 根；中间候选放 OS 临时目录并在结束后清理。provider 缓存、cookie、token 与临时路径不进入公开交付（详见 [交付契约](references/delivery-contract.md)）。

### 日志与完成回执

每次交付至少汇报：匹配路由（`image_type` / `preset` / `family` / `aspect`）、Prompt/图片/manifest 路径与尺寸，以及事实、文字、比例和 `view_image` 验收结果与未通过项。

## 渐进式选择与加载（必须遵守）

### 需求不明确时：先反问，不生成

仅当客户请求含糊到无法确定用途或输出形态时，先运行 L0 目录命令并用最小清单反问；需求已足够路由时直接进入编译。不要猜 `preset`，也不要先生成样图：

```text
我先确认一下需求。我们目前支持：
1. 文章封面 / 公众号成套配图（2.35:1 首图 + 1:1 分享图 + 正文三类配图） / 45 秒一图读懂海报 / 研究小结卡
2. 康奈尔笔记信息图（1–6 页自适应）
3. 视觉隐喻海报
4. 技能库宣传单图 / 小红书轮播
5. 插件或应用图标
6. 品牌 Logo 系统（图形标、字标、组合和变体）
7. 公开 X Prompt Deck → image 技能进化导入

请回复：
- 用途：封面、小结、笔记、海报、宣传卡、轮播、插件图标还是品牌 Logo？
- 来源：文章/仓库/X Prompt Deck，还是只给一个主题？
- 风格：从支持目录选一个 family（支持点名中国传统构图 20 式 / 中华传统色），或描述你想要的视觉感觉？
- 版式：比例、平台、张数；有没有必须逐字出现的标题、数字、URL、字标或 Logo？
```

客户仅答部分时只追问会改变路由的缺失字段，其余给可确认默认值；客户明确说“直接生成”且主路由清晰时跳过反问。

### L0：支持目录

含糊请求先读 `template-registry.yml` 与 `prompt-composition-index.yml` 的 `support_catalog`（7 个交付模板、11 个 Prompt 家族、4 个 Logo 变体和 1 条外部提示词进化导入路线），运行 `python3 scripts/resolve_prompt_composition.py --list-supported`；路由清晰时直接进入 L1–L2。

### L1–L2：家族与组合轴

确定 `family`、`information_structure`、`visual_mechanism`、`aesthetic_system`、`text_strategy`、`model_adapter`，按需加载命中词条、[家族目录](references/prompt-family-catalog.md) 与 [组合块执行契约](references/prompt-block-contract.yml)。

### L3：生成与验收

读取所选模板、来源事实与质量门，落盘七个 canonical blocks 后调用生图；`view_image` 实测位图文字/比例/事实，失败则改单个主因后以新文件名重生（最多两次）。

### 康奈尔多页决策

选 `cornell_notes` 后运行 `scripts/resolve_cornell_pagination.py`（锁定 `dense_cornell_v1`，若给参考图则标 `style_density_reference`）。`page_count` 为 1–6，≤10 个模块保持单页，系列每页 6–10 个完整模块（不机械截断、不虚构填页），在 Prompt/manifest 记录 `density_audit` 与 `series_id`，左上角保留统一 `NN/TT` 页码徽章，不复制参考图标注红框；逐页 `view_image` 全过才算通过。

## 路由与执行边界

- **普通文章 / 公众号 / 国风 / 海报**：读 [输入契约](references/input-contract.md)、[模板注册表](references/template-registry.yml)、[组合索引](references/prompt-composition-index.yml)；按需加载 [研究编辑极简模板](references/prompt-editorial-research-minimal.md)（`editorial_research_minimal` / `editorial_summary_card`）、[中国传统构图 20 式](references/prompt-visual-mechanism-chinese-composition.md)、[中华传统色](references/prompt-aesthetic-chinese-traditional-colors.md)、[30–45 秒手机海报压缩（≤220 字）](references/prompt-structure-mobile-poster-compression.md)。
- **社交卡 / 图标 / Logo**：交付选 `social_card | carousel | icon | logo`（对应 `social_skill_catalog` / `plugin_icon` / `brand_logo`），社交卡先跑 `build_social_catalog_facts.py` 并在生图后跑 `validate_social_catalog_delivery.py`（规则见 [社交卡契约](references/social-card-contract.yml)）；图标与 Logo 终稿必须按 [Logo 系统 Prompt](references/prompt-brand-logo.md) 做确定性 `svg_deterministic` 矢量重绘。
- **X Prompt Deck 导入**：`source=x-profile-export` 时按 [X 导入契约](references/prompt-x-profile-import.md) 与 [X 提示词进化契约](references/x-profile-prompt-evolution.yml) 运行 `scripts/import_x_profile_prompt_deck.py` 与 `scripts/audit_x_profile_prompt_deck.py`，拆出 `base_visual_system`、`topic_seasoning`、`series_variables`、`render_plan`（GPT2 仅作来源标签）。
- **Prompt 骨架与渲染**：每个 `prompts/NN-*.md`（系列 `pNN-of-TT`）须含 `composition_axes` 与七个内容块（`source_grounding` / `primary_task` / `composition_and_layout` / `visual_style_and_materials` / `exact_text` / `aspect_and_output` / `constraints_and_avoid`，内部字段见 [组合块执行契约](references/prompt-block-contract.yml)），未落盘不得生图。按 `render_engine` 路由：`imagegen` 默认（provider 派发见 [imagegen-providers.md](references/imagegen-providers.md)），`html_render` 为无可用 imagegen 时的降级或超高字密首选，`svg_deterministic` 为图标/Logo 终稿，禁 Pillow/canvas 冒充终稿；验收细则见 [质量门](references/quality-gates.md)。
- **目录与硬界**：未指定 `output_dir` 时运行 `python3 scripts/resolve_output_dir.py --source <source> --json` 并在 manifest 记 `output_dir_origin`（禁 cwd/技能仓/vault 根/`<topic>-delivery-<date>`）；不伪造二维码、URL、数字或已有品牌身份；脚本返回 0 不等于图片通过；缺 `imagegen` 且走 `imagegen` 路线或任意路径缺 `view_image` 时停止。

## 验证与前向测试

```bash
python3 scripts/generate_skill_catalog.py --check
python3 scripts/generate_expert_manifest.py --check
python3 scripts/audit_skills.py --strict
python3 -m unittest discover -s tests -p 'test_*.py'
```

前向测试须逐张 `view_image` 核验真实位图；X 进化交付另核对四层变量与渲染计划，不以 YAML 存在代通过。
