# SOIA 办公协作技能库

面向飞书知识协作与 ProcessOn 图表归档的可复用 AI 技能集合。

## 技能目录

| 技能名 | 一句话简介 |
|---|---|
| `soia-cwork-feishu-cli` | 使用飞书官方 `lark-cli` 以应用或用户身份执行最小权限的知识库、云盘和文档只读调研。 |
| `soia-cwork-feishu-doc-git-sync` | 将飞书知识库或云文档只读同步为保留目录与来源元数据的本地 Markdown。 |
| `soia-cwork-processon-diagrams` | 安全盘点 ProcessOn 目录，并按授权导出、校验和归档图表。 |

## 安装

将 `<技能>` 替换为上表中的技能名：

```bash
npx skills add soia-team/soia-open-cwork-office-skills -g -a '*' -s <技能> -y
```

例如安装飞书 CLI 技能：

```bash
npx skills add soia-team/soia-open-cwork-office-skills -g -a '*' -s soia-cwork-feishu-cli -y
```

## 生态导航

规范真源与全生态目录见 [soia-team/soia-open-skills](https://github.com/soia-team/soia-open-skills)。

## License

MIT License，详见 [LICENSE](./LICENSE)。
