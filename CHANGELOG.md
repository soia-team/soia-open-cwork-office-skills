# Changelog

本文件由 soia-meta-skill-release 在每次正式发版时自动更新，与 GitHub Release 同源；
更早的版本演进见 git 提交历史与 GitHub Releases。

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
