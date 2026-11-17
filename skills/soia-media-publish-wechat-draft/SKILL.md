---
name: soia-media-publish-wechat-draft
description: 把成文草稿排版成符合微信公众号限制的内联样式 HTML，机械校验通过后推入微信公众号草稿箱；只建草稿，绝不自动群发。Triggers：「把这篇发成公众号」「排版这篇公众号」「推到公众号草稿箱」「公众号 draft」
version: 3.3.0
created_at: 2026-07-02 17:49:40
updated_at: 2026-11-17 11:43:00
created_by: claude opus 4.6
updated_by: gemini-4-hydrogen
---

# soia-media-publish-wechat-draft

媒体内容工作流的**公众号发布环节（发）**：把 `compose` 产出的成文草稿排版、校验，并在确认后推入微信公众号草稿箱。

> 蓝本：vault 里一篇关于 Obsidian + AI 公众号排版工作流的归档文章——本 skill 是它的落地实现；不要在公开 skill 中写入个人 vault 的真实路径。

## 客户可读说明

### 这个技能可以做什么

对写好的成稿文章默认不改正文、不重写标题，将其排版成遵守微信公众号红线的内联样式 HTML（支持 `default` 与 `minimal` 纯墨主题），同步生成 `<stem>.copy.html` 一键复制与 390px 手机端双比例（`2.35:1` 首图 + 中央 `1:1` 分享安全框 + `5:2` X 封面）预览工作台；支持本地一键复制富文本、浏览器辅助存草稿、官方 API `draft/add` 新建与 `--draft-media-id` (`draft/update`) 原地覆盖更新草稿箱（支持多账号），并在人工发布后通过 `archive.py --move` 连同本地 `assets/` 配图一并归档。只建草稿，绝不自动群发。

| 客户想要 | 技能会做 | 客户能看到 |
|---|---|---|
| 完成本技能覆盖的工作 | 读取用户请求、必要上下文和本技能正文流程，执行最小可靠步骤 | 客户会看到 Obsidian/vault 文件变更、终端日志、生成产物路径和最终回执。 |
| 缺少依赖、权限、配置或 key | 停止需要外部状态的动作，明确指出缺什么 | 安装命令、申请地址、配置路径或需要客户确认的问题 |
| 执行完成 | 汇总成功、跳过、失败、文件变更和验证结果 | 一段可复制进工单/日志的完成回执 |

### 客户如何使用

1. 用自然语言说明目标，并提供必要输入：文件、URL、repo、workspace、proposal、vault 或平台账号状态。
2. 能 dry-run 或预览的动作先给预览；涉及删除、覆盖、发送、发布、写远端状态时先征求客户确认。

### 依赖与安装

整域需明确选择（装整个领域插件，包含本仓全部技能）：

```bash
claude plugin marketplace add soia-team/soia-open-skills
```

```bash
claude plugin install soia-cwork-office@soia
```

默认项目单技能（`<agent>` 换成明确宿主，执行前核实当前参数；仅明确选择全局时加 `-g`）。与域插件二选一，避免双份索引各自漂移：

```bash
npx skills add soia-team/soia-open-cwork-office-skills -a <agent> -s soia-media-publish-wechat-draft
```

配置约定：

```text
~/.config/soia-skills/soia-media-publish-wechat-draft/config.yml
SOIA_MEDIA_PUBLISH_WECHAT_DRAFT_CONFIG_FILE=<custom-config-path>
```

- 如果本技能不需要私有配置，可以不创建 `config.yml`。
- 如果需要 API key、cookie、session、provider home 或本机路径，只能放进私有 `config.yml`、进程环境或 provider 自己的登录态里，不能写进仓库、vault 正文或日志。
- 第三方 skill 只能声明依赖和安装方式，不直接修改第三方 skill 文件。

**WorkBuddy** 使用[专家安装说明](https://github.com/soia-team/soia-open-skills/blob/main/docs/install/workbuddy.md)，不由 npx 代装。上述命令不构成安装或发布授权。

### 日志与完成回执

每次执行都要让客户看见过程和结果。最低回执格式：

```markdown
完成：<一句话说明本次完成了什么>。

日志摘要：
- started: <检查到的输入/配置/依赖，不打印秘密值>
- processed: <数量或范围>
- created/updated: <数量或路径>
- skipped/failed: <数量和原因>

文件变化：
- <绝对路径或“未改动文件”>

验证：
- <运行过的检查、命令或人工核对点>

问题与下一步：
- <缺 key / 缺依赖 / 需要客户确认 / 建议下一条命令；没有则写“无”>
```

## 动笔前：读公众号上下文

如果客户提供了个人说明书，先读取其中的公众号读者与口吻定位，再结合下文格式规则执行。没有该文件或缺少公众号定义时，回退到通用公众号语气。

## 公众号发布（主流程）

### 微信平台红线（必须遵守）

公众号自带的编辑器（网页粘贴、`draft/add` 接口）会主动过滤/剥离大量 HTML/CSS 写法——
排版好的 HTML 一旦踩中下面的红线，粘贴或推送后样式大概率整段消失。渲染前先照这份
清单自查；`scripts/render_wechat.py` 已按此实现，`scripts/validate_wechat_html.py`
是这份清单的机械校验器（有硬性违规直接 exit 1）。

**不能做（禁用，出现即会被吃掉/剥离）**

- `<div>` —— 微信编辑器不识别，块级容器一律用 `<section>`（多行容器）或 `<p>`（单段）替代。
- `class="..."` / `id="..."` —— 无论是复制粘贴还是接口推送都会被剥离，样式只能靠内联 `style=""` 存活。
- `position` / `float` —— 布局类 CSS 编辑器不支持，用 `margin`/`padding`/`text-align` 等流式布局替代。
- `@media` / `@keyframes` —— 响应式断点和动画规则整段被丢弃，公众号正文既没有断点也用不到动画。
- CSS 变量 `var(--x)` 用法及 `--x: ...` 声明 —— 自定义属性不被支持，颜色/尺寸必须写死字面值
  （本 skill 的替代方案：主题色用 Python 常量在渲染脚本顶部集中定义，而不是 CSS 变量）。
- `<style>...</style>` 整块 —— 内嵌/外部样式表规则一律不生效，样式必须逐个内联在标签的 `style=""` 上。

**必须这样做（强制，否则粘贴后大概率掉样式）**

- 所有样式内联写在 `style=""` 属性里，不依赖任何选择器。
- 每一个可见文字节点都用 `<span style="...">` 包一层——编辑器/复制粘贴经常按"标签边界"取样式，
  裸文字（直接挂在 `<p>`/`<section>`/`<li>` 下、没有再套一层 `<span>`）最容易在粘贴时掉样式。
  `<code>`/`<pre>` 里的等宽文本本身就是"原样展示"，不强制再套。
- 标点风格统一（中文正文建议全角，代码/英文用半角），避免全角半角混排显得凌乱。
- 图片：正文先用 `<img src="...">` 占位，真正发布前必须把图传到微信素材库换成 CDN URL
  （`scripts/publish.py` 负责这一步），本地路径/外链图片在公众号后台大概率显示不出来。
- 代码块：微信不支持真正的语法高亮组件，用内联样式的 `<section>` 包一层
  `<code style="...font-family:monospace...">` 模拟等宽字体 + 背景色的"代码块"观感。

自查顺序：先人工过一遍上面的"不能做"清单，再跑 `validate_wechat_html.py` 做机械复核——
人工 + 脚本双保险，有硬性违规先修渲染结果，通过（exit 0）了才进入推草稿箱一步。

### 排版规则：强调密度模型

三个层级，越往下越"重"、越要控制数量：

| 层级 | 用途 | Markdown / 实现 | 篇幅内数量 |
|------|------|------|------|
| 锚点层 | 标题、全文最重要的判断句（金句） | `#`/`##` 标题；单独成句的结论 | 全篇 ≤5 处（标题+金句合计） |
| 标记层 | 段内关键词/短语高亮，帮读者扫读 | `**加粗**`（主题色）、`==高亮==`（红色加粗） | 每段 1–3 处 |
| 容器层 | 需要跳出正文视觉的内容块 | 引用块 → 金句卡、代码块、图片 | 按需触发，不设硬性上限但不滥用 |

- 锚点层「全篇 ≤5 处」是硬约束，标题和金句合计计数，不是每种各 5 处。
- 标记层沿用原三级强调里的 L1/L2：`**...**` 是短语级强调（主题色加粗），`==...==` 是结论/
  警告/关键数字级强调（红色加粗），两者合计每段 1–3 处，避免通篇加粗看起来像广告文案。
- 容器层按内容形态触发，不是为了"凑视觉丰富度"——有引用才用金句卡、有代码才用代码块。
- 总强调占比（标记层文字量 / 全文字数）仍建议 **<15%**。禁背景色 / 框（容器层例外）。
- 文章类型：frontmatter `workflow_type: tech`（有代码/表格）或 `editorial`（纯文字）；
  `render_wechat.py` 目前是统一简洁主题，`render.py`（早期简版）保留 tech/editorial 两风格。
- 配图：Alt 为空 → 图下不显示 caption；Alt 有内容 → 写清图里是什么（别用"配图""截图"这种无信息词）。

### 6 步流程与三级渐进发布通道

> **核心生产边界（必守）**：当输入是一篇已经写好的成稿文章时，本技能**默认不改正文内容、不重写标题**，只接管成稿后的配图衔接、内联 HTML 排版、草稿箱投递与发布归档等生产动作（仅在步骤 2 呈报重字/错别字勘误对照供作者拍板，或仅在作者显式要求润色/降重时才修改正文）。

1. **检查元数据与封面/配图规划**：
   - 检查 frontmatter 的 `workflow_type` / `wechat_theme`（默认红黑强调，或 `minimal` 克制纯墨）与文档头部推荐语：`digest` 优先对应本篇单篇内容（**20–40 字**，不拿合集 `series_intro` 顶替；未填 `digest` 时可由 `preface` 回退），以上「序」/推荐语只进入公众号摘要栏而不渲染进正文（正文从首段自然起笔）；若文章拟开启原创与赞赏，核对 `author` 与账号共用的**微信赞赏账户名**一致。
   - 若文章尚未配图，先调用 `soia-media-generate-article-image` 生成公众号首图（`2.35:1`，标准 `900×383` 或 2x `1880×800`，确保中央 `1:1` 分享裁切安全区完整，可选联动生成 `5:2` 的 `x-cover.png`），并按**正文三类配图克制原则**（① 流程图/架构图、② 真实操作截图/结果预览图、③ 意境分隔图，全篇 1–3 张，不每小节配图）保存到文章专属资源目录（`assets/<日期-篇名>/` 或 `images/`）。
2. **发表前勘误与克制润色**：先核查重字、错别字与标点缺漏并呈报勘误对照（只检不擅改原意）；随笔/自省类优先 `minimal` 纯墨主题或克制加粗，避免大面积鲜红 `==高亮==` 破坏书卷静气；技术长文按密度模型保持锚点层 ≤5、标记层每段 1–3。
3. **渲染与生成一键复制工作台（`.html` + `.copy.html`）**：
   - `scripts/render_wechat.py` 把 Markdown → 遵守"微信平台红线"的内联样式 HTML（`<stem>.html`）。
   - 同时生成 **`<stem>.copy.html` 一键复制与手机端双比例预览工作台**（含 `2.35:1` 首图 + 中央 `1:1` 红色虚线分享安全框预览、`1:1` 转发卡片预览、`5:2` X 封面预览、`390px` 手机视口正文预览、标题/作者/摘要单点复制、「复制公众号富文本」与「复制 HTML 源码」按钮、发布前 7 项检查清单）。
4. **校验**：`scripts/validate_wechat_html.py` 机械复核红线——有硬性违规先回到渲染结果/脚本修，通过（exit 0）才能进入下一步；warning 不阻塞，但建议顺手清一遍。
5. **三级渐进投递草稿箱（推草稿前先确认）**：
   - 默认先执行 `--dry-run` 产出 `<stem>.html` 与 `<stem>.copy.html`，向客户确认风格、封面与投递方式（唯一跳过确认的条件是客户当前明确说“直接推/不用确认”）：
   - **方式一：本地 `.copy.html` 一键复制富文本（零配置最稳兜底）**——适合未认证个人订阅号（无 `draft/add` 接口权限）、未配 IP 白名单或希望先在本地预览的人。打开 `<stem>.copy.html` 点击「复制公众号富文本」，进入 `https://mp.weixin.qq.com`（新的创作 → 图文消息）直接粘贴并保存草稿。
   - **方式二：浏览器已登录态辅助存草稿**——客户扫码登录公众号后台后，由浏览器工具将标题、作者、摘要、封面与 `article-wechat.html` 填入图文编辑器并保存草稿（绝不点发表/群发）。
   - **方式三：官方 API 自动推草稿箱（`scripts/publish.py`）**——已配 `WECHAT_APP_ID` / `WECHAT_APP_SECRET` 与 IP 白名单时，自动上传正文图换微信 CDN URL、上传封面取 `thumb_media_id`，新建走 `draft/add`，修改已有草稿走 `--draft-media-id` (`draft/update`) 原地覆盖（避免草稿箱产生多份重复草稿）。
6. **草稿箱 7 项预览检查与发布后归档**：
   - 提醒客户在公众号草稿箱完成 **7 项最终预览检查**（① 封面手机端清晰度与 1:1 裁切框、② 标题错别字与赞赏署名、③ 摘要转发卡片自然度、④ 正文图片完整性、⑤ 手机端段落间距、⑥ 链接可用性、⑦ 结尾无多余 AI 味）后手动发表。
   - **人工确认发布后** → 运行 `scripts/archive.py --move` 标记 `status: 已发布`、写入文章 URL，并将源草稿 `.md` 与同目录 `assets/<篇名>/` 配图一并迁入 `50_写作与发布/20_发布/YYYY/MM/`（自动清理草稿区临时生成的 `.html` 与 `.copy.html`）。

### 脚本规格（scripts/）

- `render_wechat.py [--file <in.md>] [--output <out.html>] [--copy-html <copy.html>] [--cover <png>] [--x-cover <png>] [--strip-h1] [--theme default|minimal]`（不传 `--file` 则读 stdin，不传 `--output` 则打印到 stdout）：当前推荐渲染器，只用微信平台红线允许的标签/内联样式；`--theme minimal`（或 frontmatter `wechat_theme: minimal`）使用克制纯墨强调与暖灰引文边线，适合自省随笔；`--copy-html` 额外生成带双比例封面安全框、390px 手机端预览与「复制公众号富文本」按钮的独立工作台页面。支持标题、`**加粗**`、`==高亮==`、`*斜体*`、`~~删除线~~`、Markdown 外链、Obsidian `[[双链]]` 清洗、`>` 金句卡、无序/有序列表、Markdown 表格、分割线、代码块（内联 `<section>` 模拟）、标准 Markdown 图片及 Obsidian 图片嵌入（如 `![[img.png|图注|600]]`，自动忽略纯宽度后缀）与 caption，每个文字节点套 `<span>`。`--strip-h1` 可剥离正文首个 `# ` 一级标题（推草稿箱时标题由元数据字段承载）；frontmatter 解析自动忽略未加引号字段后的行内 `# 注释`。
- `validate_wechat_html.py [--file <html>] [--json]`（不传 `--file` 则读 stdin）：机械校验上面「微信平台红线」，报告每条违规的行号 + 类型；硬性违规（div/class/id/position/float/media/keyframes/css 变量/`<style>` 块）exit 1，干净 exit 0；裸文字节点降级为 warning，不阻塞。纯 `html.parser` 实现，无第三方依赖。
- `render.py <in.md> <out.html> [--style tech|editorial]`：早期简版渲染器，未做红线校验/`<span>` 包裹，保留兼容使用——用它渲染完，正式发布前也必须过 `validate_wechat_html.py`。
- `publish.py --article <md> [--cover <png>] [--x-cover <png>] [--account <alias>] [--config <path|name>] [--theme default|minimal] [--html-out <path>] [--copy-html <path>] [--no-copy-html] [--draft-media-id <id>] [--draft-index 0] [--save-draft-id] [--dry-run] [--list-accounts] [--check-auth]`：
  - 默认使用 `render_wechat.py`（剥离首个 H1）并通过 `validate_wechat_html.py` 红线门禁（硬违规直接阻断）；`--html-out` 可将排版 HTML 写到指定路径（不传则写回源 `.md` 同名 `.html`），并默认同步生成 `<stem>.copy.html` 一键复制与手机端双比例预览工作台（传 `--no-copy-html` 可关闭）。
  - 支持 Obsidian 短附件名（如 `![[cover.png]]`）在文章目录及上层 `assets/` / `_attachments/` 唯一匹配（多处重名时 fail-closed 报错）；`author` 按 `--author` → frontmatter `author` → 账号配置 `author` / `WECHAT_AUTHOR` 回退。
  - `--draft-media-id`（或 frontmatter `wechat_draft_media_id:`）存在时，自动调微信 `POST /cgi-bin/draft/update` 原地替换该草稿而非再建新草稿；加传 `--save-draft-id` 可把当前草稿 `media_id` 写回源 Markdown frontmatter。
  - `--list-accounts` 脱敏列出已配置的多个公众号账号；`--check-auth` 仅测换取 `access_token` 验证凭据与 IP 白名单；`--config` 可显式指定某个 `.yml` 文件路径或配置目录下文件名（如 `config.<alias>.yml`）。
  - `--dry-run` 只渲染、校验、生成 `.copy.html` 并核对本地正文图片与封面文件存在性，不调用微信 API。
  - 正式推送草稿箱顺序：`GET /cgi-bin/token` → 扫描 HTML 本地 `<img>` 调 `POST /cgi-bin/media/uploadimg` 换微信 CDN URL → 封面调 `POST /cgi-bin/material/add_material?type=image` 取 `thumb_media_id` → 新建走 `POST /cgi-bin/draft/add` 或原地更新走 `POST /cgi-bin/draft/update`。**绝不调用群发接口**。
- `archive.py --article <md> [--url <link>] [--date YYYY-MM-DD] [--platform 公众号] [--account <alias>] [--archive-dir <dir>] [--move] [--force]`：保留原有 frontmatter 字段与顺序，更新 `tags`（`草稿`/`待审核` → `已发布`）、`status: 已发布`、`published_at`、`platform`、`url`（可选写 `wechat_account`）；传入 `--archive-dir` 时按 `<dir>/YYYY/MM/YYYY-MM-DD-<平台>-<标题>.md` 归档。传入 `--move` 时，不仅迁出源草稿 `.md` 并清理同目录临时生成的 `<stem>.html` 与 `<stem>.copy.html`，还会将文章引用的同目录 `assets/<篇名>/` 本地配图一并迁入 `<dir>/YYYY/MM/assets/<篇名>/`，确保发布归档区独立自包含、草稿区只留未发布草稿。

> 首次搭 <1 小时（规则 15 分 + AI 生成脚本 20 分 + Skill 文档 10 分），之后每篇排版 ≈ 0。

### 多账号配置与安全边界

- **两种组织方式均支持**（可单选或混合使用，完整示例见 `assets/config.example.yml`）：
  1. **单文件多配置**：在私有 `config.yml` 中配置 `accounts:` 与可选 `default_account:`。
  2. **多文件分账号**：在同目录按名字拆分多个文件，如 `config.<alias>.yml` 或 `accounts/<alias>.yml`（各文件按常规 `env:` 写 `WECHAT_APP_ID` / `WECHAT_APP_SECRET` / `WECHAT_ACCOUNT_NAME`），通过 `--account <alias>` 或 `--config config.<alias>.yml` 指定。
  - 单账号继续兼容 `env.WECHAT_APP_ID` / `env.WECHAT_APP_SECRET`。
- **失败关闭（fail-closed）**：配置了多个账号且未设 `default_account` 时，不传 `--account` / `--config`（或 frontmatter `wechat_account`）会直接报错列出可选 alias；显式指定不存在的 `--account` 也绝不静默回退到其他账号。

## ⚠️ 安全规则（必守）

- **只建草稿，绝不自动群发**——群发必须用户在公众号后台手动点。
- 凭据存 skill-specific 私有 `config.yml`（`WECHAT_APP_ID` / `WECHAT_APP_SECRET` 或 `accounts.<alias>`），**不提交 Git**，日志/异常中对 `app_id`/`app_secret`/`access_token` 严格脱敏。
- 需先在 developers.weixin.qq.com / mp 后台启用开发 API、配 IP 白名单。

## 私密信息与中间数据

- 私有凭据与账号映射只存 `${XDG_CONFIG_HOME:-~/.config}/soia-skills/soia-media-publish-wechat-draft/config.yml`（只读兼容旧 v1 路径并提示迁移）；不写入 vault 正文、公开仓库或日志。
- 渲染得到的 `.html` 仅作为与源 `.md` 同目录的排版产物；不缓存 `access_token` 到磁盘。

## 验证

运行随仓单元测试与真实 fixture 前向测试（含渲染、红线校验、`.copy.html` 一键复制工作台生成、多账号凭据解析、脱敏、mocked 微信 `draft/add` & `draft/update` 链路与 `archive.py --move` 连带 `assets/` 归档验证）：

```bash
python3 -m unittest tests.test_wechat_draft
```

## 如何获取并配置微信公众号 AppID / AppSecret

- **入口**：微信公众平台 https://mp.weixin.qq.com，用该公众号绑定的**管理员微信扫码登录**。
- **前提条件**：`draft/add` 等接口只对**已认证的订阅号/服务号**开放；未认证的个人订阅号没有这些接口权限，调用会直接报类似 `48001 api unauthorized`。
- **取 AppID / AppSecret 的路径**：登录后台 → 左侧菜单最下方「设置与开发」→「开发」→「基本配置」（旧版菜单是「开发」→「开发接口管理」→「基本配置」）→ 页面上「公众号开发信息」区块：
  - **开发者ID（AppID）**：直接可见、可复制，对应下面的 `WECHAT_APP_ID`。
  - **开发者密码（AppSecret）**：点「重置」，管理员扫码确认后才显示，**只显示这一次**，当场复制保存，对应 `WECHAT_APP_SECRET`；重置会让旧 secret 立即失效，别处如果还在用会一并断掉。
- **IP 白名单（关键，漏了必失败）**：同一张「基本配置」页里有「IP白名单」，必须把实际调用 API 的机器的**公网 IP** 加进去，否则换 access_token 时会报 `40164 invalid ip`。本地跑就先查一下本机公网 IP（如 `curl ifconfig.me`），换网络后要记得更新。
- **放哪**：秘钥只放私有 `config.yml`，不进 vault、不进这个开源 skill 仓库、也不进 shell 启动文件。`publish.py` 已接入 `scripts/publish_env.py`，跑起来会**自动探测并加载** `$SOIA_MEDIA_PUBLISH_WECHAT_DRAFT_CONFIG_FILE`（或兼容别名 `$SOIA_MEDIA_PUBLISH_ENV_FILE`）以及默认路径 `~/.config/soia-skills/soia-media-publish-wechat-draft/config.yml`，并对旧命名空间做只读回退，**无需手动 source**：

  ```
  # ~/.config/soia-skills/soia-media-publish-wechat-draft/config.yml
  env:
    WECHAT_APP_ID: "wx************"
    WECHAT_APP_SECRET: "********************************"
  ```

  ```
  # 直接跑，脚本自己会读私有 config.yml
  python3 scripts/publish.py --article xxx.md --cover xxx.png
  ```

  （凭据放在非默认路径时：`SOIA_MEDIA_PUBLISH_WECHAT_DRAFT_CONFIG_FILE=/你的/路径/config.yml python3 scripts/publish.py ...`。）

- **自检**：配好后可以先让 AI「用这份凭据换一次 access_token 看通不通」，通了再往下走 render → validate → 推草稿箱这条主流程。

## 执行后回执

推完草稿后告诉用户：① 草稿在公众号后台（哪篇、什么风格）② 提醒“确认无误后你手动群发” ③ 群发后回来说一声，再运行 `archive.py` 归档。若只是排版或校验，也要回报产物位置和校验结果。

## 闭环位置

```
clip(收) → organize(整理) → distill(点) → compose(写) → ★publish-wechat-draft(公众号发)
```

上游 `compose` 给成文草稿；本 skill 负责公众号排版 + 草稿箱投递。凭据与“手动群发”这道人工闸门由用户把控。
