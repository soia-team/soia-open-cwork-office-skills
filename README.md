# SOIA 办公协作技能库

工作协作-办公：飞书 CLI/知识库镜像、ProcessOn 盘点导出（soia-cwork-*）

## 域说明

本仓负责 `cwork` 域，技能名称统一使用 `soia-cwork-*` 前缀。

## 技能

| 技能 | 用途 |
|---|---|
| `soia-cwork-feishu-cli` | 使用飞书官方 `lark-cli` 只读调研知识库、云盘、文档和权限。 |
| `soia-cwork-feishu-doc-git-sync` | 将飞书知识库只读镜像为本地 Markdown，并接入 Git、Obsidian 或 VitePress。 |
| `soia-cwork-processon-diagrams` | 可恢复地盘点 ProcessOn 目录，并按授权导出、校验和归档图表。 |

## 安装

```bash
npx skills add soia-team/soia-open-cwork-office-skills -g -a '*' -s <skill> -y
```

本仓属于 SOIA 技能生态，规范真源见 [soia-team/soia-open-skills](https://github.com/soia-team/soia-open-skills)。
