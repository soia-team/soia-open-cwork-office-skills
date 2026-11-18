# Changelog

本文件由 soia-meta-skill-release 在每次正式发版时自动更新，与 GitHub Release 同源；
更早的版本演进见 git 提交历史与 GitHub Releases。

## v1.11.8 — 2026-11-18

瘦身 6 个 SKILL.md 并外提小红书网页发布实操参考

## 维护
- refactor(skills): slim SKILL.md for strong models and extract rednote web guide (#98)
- chore(release): open next train after v1.11.7 (#97)

## v1.11.7 — 2026-11-17

新增中国传统构图20式与中华传统色、45秒一图读懂手机海报压缩及公众号 copy.html 一键复制与双比例预览工作台

## 新增
- feat(media-skills): add Chinese traditional composition/colors, 45s mobile poster compression, and copy.html workbench (#95)

## 维护
- chore(release): open next train after v1.11.6 (#94)

## v1.11.6 — 2026-11-17

完善公众号封面/分享图/正文配图成套规格与字号门禁，增强 archive.py --move 静态资源连带迁移，新增 compose 微信原创去重门禁

## 新增
- feat(media-skills): complete wechat cover/share/inline image pack rules, archive --move asset migration, and compose originality gate (#92)

## 维护
- chore(release): open next train after v1.11.5 (#91)

## v1.11.5 — 2026-11-16

为 publish.py 新增微信草稿箱 draft/update 原地覆盖更新与 media_id 持久化支持

## 新增
- feat(wechat-draft): add in-place draft/update support via --draft-media-id and --save-draft-id (#89)

## 维护
- chore(release): open next train after v1.11.4 (#88)

## v1.11.4 — 2026-11-16

规范文档头部「序」（series_intro/preface/digest）只作多平台推荐语而不进公众号正文，支持 preface 摘要回退

## 新增
- feat(media-skills): keep frontmatter preface/series_intro out of wechat body and support preface digest fallback (#86)

## 维护
- chore(release): open next train after v1.11.3 (#85)

## v1.11.3 — 2026-11-16

新增公众号开篇「序」与合集/单篇摘要分离规范，补充原创赞赏作者校验

## 新增
- feat(media-skills): require wechat opening preface and separate collection bio from article digest (#83)

## 维护
- chore(release): open next train after v1.11.2 (#82)

## v1.11.2 — 2026-11-16

新增 compose 发表前勘误清单与极简意象体模式、公众号 --theme minimal 纯墨排版及无字 2.35:1 封面规范

## 新增
- feat(media-skills): add compose proofread/minimal-essay guidance, clean wechat cover rules, and --theme minimal (#80)

## 维护
- chore(release): open next train after v1.11.1 (#79)

## v1.11.1 — 2026-11-16

增强公众号 Obsidian 双链嵌图/YYYY-MM 归档与默认作者回退，补充生僻字与宽封面 HTML 混合渲染规范

## 新增
- feat(media-skills): support Obsidian embeds, html-out, YYYY/MM archiving, and CJK hybrid_exact_text fallback (#77)

## 维护
- chore(release): open next train after v1.11.0 (#76)

## v1.11.0 — 2026-11-16

补全微信公众号草稿 API 投递、完整 Markdown 排版与多账号配置

## 新增
- feat(wechat-draft): complete API draft flow, markdown rendering, and multi-account configs (#73)

## 维护
- chore(release): bump dev train to 1.11.0-SNAPSHOT (#74)
- chore(release): open next train after v1.10.0 (#72)

## v1.10.0 — 2026-11-11

并入 5 个内容技能（2 → 7），域展示名改为「办公与内容」；技能名与技能行为不变

## 合并
- 原 `soia-open-media-content-skills` 仓的 5 个技能原名搬入本仓：`soia-media-compose-article-draft`、`soia-media-generate-article-image`、`soia-media-publish-wechat-draft`、`soia-media-publish-rednote-card`、`soia-media-publish-x`；脚本、参考、资产与对应测试一并搬入，技能版本号不变
- 技能名不变，所以已装副本、`~/.config/soia-skills/<技能名>/` 配置目录与公众号 / 小红书 / X 的登录 profile 都不受影响；旧版落盘路径的只读回退保持原样
- 5 个技能正文里的安装命令改为指向本仓与 `soia-cwork-office` 插件；原域插件 `soia-media-content` 不再更新
- 更早的内容技能版本记录见原仓各版 Release 说明

## 维护
- 域展示名改为「办公与内容」（Office & Content）；仓名与插件名 `soia-cwork-office` 不变
- WorkBuddy 专家仍是一个：原「新媒体运营」人设的能力、流程与「只建草稿不发布」边界并入 `soia-cwork-office` 专家，职业名改为「Soia · 办公与内容助手」
- README 中英、技能目录、三份插件清单、第三方声明（新增 X Articles 解析来源与 `licenses/` 许可副本）同步到 7 个技能
- 审计脚本与技能目录生成器接受 `media` 域前缀

## v1.9.1 — 2026-11-11

README 去掉失效链接；技能功能不变

## 维护
- README 中英「环境安装」一句去掉已失效的仓库链接，改为按各工具官方文档

## v1.9.0 — 2026-11-11

合并飞书两个技能（3 → 2），安装说明改为项目最小安装

## 合并
- `soia-cwork-feishu-doc-git-sync` 并入 `soia-cwork-feishu-cli`（保留该名）：同步脚本、参考与配置模板随之搬入，「同步飞书知识库」「备份到 Git」等触发词保留，旧入口不留别名
- 同步配置新位置 `~/.config/soia-skills/soia-cwork-feishu-cli/doc-git-sync.yml`；旧目录 `soia-cwork-feishu-doc-git-sync/config.yml` 只读回退，环境变量 `SOIA_CWORK_FEISHU_DOC_GIT_SYNC_CONFIG_FILE` 不变

## 维护
- 安装说明默认项目、明确宿主、单技能；不再教 `-g -a '*' -y`
- README 中英、技能目录、WorkBuddy 专家清单同步到 2 个技能

## v1.8.4 — 2026-09-14

交付渐进加载仓库规则：缩小常驻指令与强制读取范围，保留安全、权限、测试及正式发布门禁；技能正文与行为不变。

## v1.8.3 — 2026-09-14

飞书只读调研按需读取错误与权限指南，保留最小权限和身份分离。

## 修复
- fix(skills): reduce redundant reading and confirmation (#58)

## 维护
- chore(release): open next train after v1.8.2 (#57)

## v1.8.2 — 2026-09-07

Clarify instruction autonomy and preserve explicit approval gates

## 维护
- docs: clarify scoped autonomy and preserve approval gates (#55)
- chore(release): open next train after release

## v1.8.1 — 2026-08-06

安装章节三宿主覆盖、config 归位 assets

## 维护
- chore(skills): 补上安装章节改动遗漏的版本 bump (#52)
- docs(skills): 安装章节补齐三个一等宿主 (#51)
- docs(agents): branch off main; releases fast-forward dev onto main (#50)
- chore(release): switch dev train to patch level (#49)
- docs(changelog): seed with current release baseline (#48)
- docs(agents): dev-branch integration workflow (#47)
- chore(release): open dev branch — audit on dev, version train 1.9.0-SNAPSHOT

## v1.8.0 — 2026-08-01

飞书文档与办公协同自动化。
