---
name: soia-media-publish-rednote-card
description: 把成文草稿改写成小红书（rednote）笔记：标题、3–5 段短文、标签与配图建议；获当次授权可驱动已登录浏览器在网页端完成发布，不用逆向包。触发：「发成小红书」「小红书笔记」「改成 rednote」「帮我发到小红书」
version: 2.4.3
created_at: 2026-07-16 15:44:20
updated_at: 2026-11-18 10:00:00
created_by: gpt-5.6-luna
updated_by: gemini-4-hydrogen
dependencies:
  optional: [soia-media-generate-article-image]
---

# soia-media-publish-rednote-card

把成文草稿改写成小红书笔记文案。默认只输出 Markdown 文案供客户自行发布；仅当客户**当次明确授权**时，驱动**客户已登录的浏览器**在小红书创作服务平台官方网页端完成发布。严禁调用私有接口、逆向签名工具或第三方自动化发帖包。

## 客户可读说明

### 这个技能可以做什么

| 客户想要 | 技能会做 | 客户能看到 |
|---|---|---|
| 把文章改成小红书笔记文案 | 提炼单一分享角度，生成承诺明确的标题、3–5 段短文、相关话题标签与配图建议 | 一份可直接复制的 rednote Markdown 文案（不覆盖原稿） |
| 处理 >1000 字长文 | 推荐走官方「写长文」富文本/一键排版通道，或仅在跨平台复用时拆成 4–8 张自产文字卡 | 合规的长文排版路线、≤20 字标题与导读描述 |
| **代为网页端发布**（需当次授权） | 在客户已登录浏览器传图、填文、逐个点选蓝色标签、按需挂 Red Skill 组件，发布前停下确认 | 最终确认截图与笔记管理页/正式链接核实回执 |

### 客户如何使用

- 「把这篇文章发成小红书，目标读者是独立开发者，不要夸大效果。」
- 「原稿超过 1500 字，按写长文形态整理标题、描述框导语和配图建议。」
- 「我已经登录小红书创作服务平台，把图和这套文案代我传上去，最后点发布前给我确认。」

### 依赖与安装

默认项目单技能：`npx skills add soia-team/soia-open-cwork-office-skills -a <agent> -s soia-media-publish-rednote-card`（`<agent>` 换成明确宿主；仅明确选择全局时加 `-g`）。整域需明确选择：`claude plugin marketplace add soia-team/soia-open-skills && claude plugin install soia-cwork-office@soia`（Codex 用 `codex plugin add soia-cwork-office@soia`）。**WorkBuddy** 使用[专家安装说明](https://github.com/soia-team/soia-open-skills/blob/main/docs/install/workbuddy.md)，不由 npx 代装。上述命令不构成安装或发布授权。纯文案模式零外部依赖；需要位图时可选衔接 `soia-media-generate-article-image`；代为发布需要宿主具备浏览器工具且客户已在浏览器自行登录。

### 私密信息与中间数据

cookie、token 与登录态始终留在客户浏览器，本技能不读取、导出、存储或在回执中输出账号密码或截图里的私人信息；临时脚本用完即停，不留常驻进程，配图放系统临时目录或客户指定位置。

### 日志与完成回执

汇报输入来源、选定分享角度、标题/段落/标签/配图数量及写入位置；代为发布另报当次授权原话、原创声明开关状态、传图尺寸核验、蓝色标签确认数、Red Skill 组件及**笔记管理页与对外正式链接核实结果**（绝不以生成文案或 URL 出现 `published=true` 冒充线上生效）。

## 改写规范

1. **定单主线**：选一个清晰角度（解问题/避坑/复盘/方法）；若原文 frontmatter 含 `preface` / `series_intro` / `digest`，转化为自然开篇导语或钩子；多主线同等重要时先询问。保留原文事实与限定条件，不编造案例或数据。
2. **标题与正文**：1 个主标题 + 至多 2 个备选（具体信息承诺/冲突，禁用空泛干货/夸张收益）；正文 3–5 段短文，每段单一信息点，5–10 个收窄相关的真实话题标签。
3. **配图与长文**：给出封面（主体/构图/文字/比例，如 `1:1` 或 `3:4`）与 0–3 张内页建议，需出图时交给 `soia-media-generate-article-image`。正文 **>1000 字**首选官方「写长文」路线，跨平台复用才拆自产文字卡，具体模板规格与字数限制见 [网页端发布与长文实操参考](references/web-publish-guide.md)。
4. **交付格式**：仅产出回复文本（含 `## 标题`、`## 正文`、`## 话题标签`、`## 配图建议`），客户指定路径才另存草稿。

## 网页端代发硬规则（未授权严禁触发）

1. **当次授权与终审**：仅「写小红书」不等于授权打开/写入网页；点「发布」前必须截图确认，开启**原创声明**（需同意《原创声明须知》）必须另获单独授权。
2. **五步流程**：切到「上传图文」tab → 传图 → 填标题与正文 → 逐个输入 `#关键词` 并从下拉点选确认（编辑器呈**蓝色**）→ 按需在「添加组件 → 添加 Red Skill」挂我发布的对应技能。
3. **浏览器传图与串行防误**：禁用第三方逆向 MCP/CLI 避免泄露 cookie 或封号；优先用 SVG `foreignObject`+`canvas`（有 HTML 源）或「只写剪贴板 `osascript` + 页面 Clipboard API 读回并用 `createImageBitmap` 校验宽高」（纯位图），严禁 `osascript` 盲打键盘；传图前确认无并行改剪贴板进程。完整 JS/Bash 代码见 [网页端发布与长文实操参考](references/web-publish-guide.md)。
4. **验证与前向测试**：未获授权只产文本；传图后页内预览张数、蓝色标签状态、原创开关默认关闭、发布后以笔记管理页及对外可见链接为唯一验收凭据。
