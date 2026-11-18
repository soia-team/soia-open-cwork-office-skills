---
name: soia-media-compose-article-draft
description: 把 distill 提炼出的观点写成成文草稿。以用户观点为骨、vault 摘抄为料，生成可继续交给 publish 的文章。可指定公众号/知乎/随笔风格。Triggers：「把这些观点写成一篇」「把 X 主题写成文章」「compose 这篇」「写成草稿」「起草这篇」
version: 3.1.5
created_at: 2026-07-02 17:57:11
updated_at: 2026-11-18 10:00:00
created_by: claude opus 4.6
updated_by: gemini-4-hydrogen
---

# soia-media-compose-article-draft

媒体内容工作流的**成文环节（写）**：以用户真实观点为骨架、vault 摘抄为论据，组织成可直接送入配图与发布技能的文章草稿，绝不替用户虚构立场。

## 客户可读说明

### 这个技能可以做什么

| 客户想要 | 技能会做 | 客户能看到 |
|---|---|---|
| 把观点或主题写成成文草稿 | 读取个人说明书与指定笔记/观点综述，按公众号、知乎或随笔（极简意象体）风格组织结构 | 带规范 frontmatter（`tags`/`title`/`series_intro`/`preface`/`digest`/`sources`）的草稿 |
| 区分合集引语与单篇摘要 | 把 `series_intro`、`preface` 与单篇 `digest`（20–40 字）分离写入 frontmatter，不混入正文首段 | 干净起笔的正文与可直接复用的多平台头部元数据 |
| 发表前勘误与防撞库打散重写 | 检查重字、错别字与标点缺漏列对照表；基于他人文章重创作时打散结构并校 `6-gram`/`8-gram` 重叠 | 勘误对照清单、`0.00%` 重叠率核验与高原创度定稿 |

### 客户如何使用

- 「把我在 `[[xxx]]` 里的观点写成一篇公众号文章，走极简随笔风。」
- 「参考这篇底稿的核心立意打散重写一篇，彻底换掉句式与段落骨架，确保通过微信原创声明检测。」
- 「帮我检查这篇草稿有没有错别字或重字，并补齐单篇 `digest` 摘要，先列勘误对照再改。」

### 依赖与安装

默认项目单技能：`npx skills add soia-team/soia-open-cwork-office-skills -a <agent> -s soia-media-compose-article-draft`（`<agent>` 换成明确宿主；仅明确选择全局时加 `-g`）。整域需明确选择：`claude plugin marketplace add soia-team/soia-open-skills && claude plugin install soia-cwork-office@soia`（Codex 用 `codex plugin add soia-cwork-office@soia`）。**WorkBuddy** 使用[专家安装说明](https://github.com/soia-team/soia-open-skills/blob/main/docs/install/workbuddy.md)，不由 npx 代装。上述命令不构成安装或发布授权。纯 LLM 工作流，无外部脚本与 API 依赖。

### 私密信息与中间数据

仅读写用户指定 vault 内的观点与草稿文件，不访问外部网络；若有路径偏好仅放 `${XDG_CONFIG_HOME:-~/.config}/soia-skills/soia-media-compose-article-draft/config.yml`，不进仓库或日志。

### 日志与完成回执

每次交付汇报：来源笔记、采用体裁/角度、新建或修改草稿路径、字数与结构，以及勘误/去重结果和推荐下游（配图或 `publish-*`）。

## 成文流程与规范

1. **收料定调**：收集指定笔记 `## 我的看法`、观点综述或摘抄，缺料直接询问不编造；按本次要求与个人说明书定风格（无定义则用自然清晰口吻）。
2. **结构与元数据分离**：开头（钩子/问题）→ 主体（观点 + `[[双链]]` 佐证）→ 结尾（收束）；要求先审大纲则等确认，否则直接起草。在 frontmatter 写入 `tags:[草稿]`、`status:草稿`、`source_notes`，并分置 `series_intro`（合集总推）、`preface`（长序）与 `digest`（20–40 字单篇微信摘要），**绝不把“序/推荐语”写进正文首段**。
3. **标题体裁边界**：沿用用户指定标题；仅在未给定时按账号定调出候选——**流量钩子体**（知乎/科普：提问、反常识、盘点）与**极简意象体**（自省随笔：四至六字词牌、八字对仗、合集编号式）严禁混用。
4. **勘误、去 AI 味与原创声明门禁**：
   - **原稿勘误只检不擅改**：检查重字、「其它/其他」、`的/地/得`、顿号/引号、漏多字、同音错别字，先报「位置 / 原文 / 建议 / 原因」确认后再改，保留作者原语调。
   - **去 AI 味**：清套路开场、空洞大词、假金句、破折号与多余列表；仅命中轻改，若像提纲/素材堆则征询后写新文件深改（不覆盖初稿）。
   - **原创撞库防线**：源自外部文章且拟开微信原创声明时，必须打散章节骨架并彻底改写长句，自检 `6-gram` / `8-gram` 连续重合趋近 `0%`。
5. **硬边界**：只落草稿目录，不改摘抄原文，不顺手越界执行排版或发布。
