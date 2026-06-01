# secondbrain | 长脑子

> 面向 AI agent 的本地文件型知识图谱。一个 SQLite 文件,零依赖,数据完全归你。
>
> [English](./README.md) · [架构设计](./references/architecture.md) · [Skill 定义](./SKILL.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org)
[![Dependencies: 0](https://img.shields.io/badge/dependencies-0-green.svg)](#安装)
[![Schema: v2.1](https://img.shields.io/badge/schema-v2.1-blueviolet.svg)](./scripts/schema.sql)

---

> 你不是在养第二个脑子,你在租一个。每隔几年,房租就涨一次 — 导出要 Pro、API 收紧、公司差点倒闭(或者被收购)。你迁一次数据,丢一次结构,然后循环重来。`secondbrain` 的赌注是:一个文件,在你的 home 目录,版本化在你的 git repo。没有"迁移计划"这个概念,因为从来没有进过别人的服务器。

---

## 这是什么

`secondbrain` 是一个为 AI agent 设计的个人知识存储。笔记保存在 `~/.secondbrain/brain.db` 这一个 SQLite 文件中,只依赖 Python 标准库 —— 没有 `pip install`,没有 `docker compose up`,没有云服务账号。

笔记之间通过正文里的 `[[wikilinks]]` 自动建立关联,在你写作的同时就构建出知识图谱。系统支持全文检索、类型化关系、标签、集合、软删除,以及与 Markdown 的双向导出。

仓库根目录的 `SKILL.md` 让 `secondbrain` 成为一个开箱即用的 [Claude Code skill](https://docs.claude.com/en/docs/claude-code/skills):任何加载该 skill 的 agent 都能在对话中保存、搜索、链接你脑子里的笔记。

## 为什么做这个

目前大多数"AI 记忆"产品都把你的数据存放在第三方云端,API 收费,而且供应商可以随时调整定价、政策,甚至关停。即使是 Obsidian 这类本地优先的工具,也无法原生与 agent 对接 —— 你最终要在两个工具之间切换:一个给"人"用,一个按调用付费给"AI"用。

`secondbrain` 是一个克制的替代方案:

- **一个文件。** SQLite 数据库,任何客户端都能打开,`cp` 就能复制,`rsync` 就能备份,`git` 就能版本化。
- **标准 schema。** 表结构以 `scripts/schema.sql` 形式直接进仓库,纯 SQL —— 没有私有格式,没有迁移服务。
- **Agent 原生。** 每个操作都是一条 CLI 命令。Agent 和人用同一套接口读、同一套接口写。
- **零依赖。** 只要 Python 3.8+ 和 SQLite,就能跑。

## 功能

- **扁平知识图谱。** 笔记(Drawer)携带标签、可选的 collection(集合),以及类型化的关系。没有需要维护的目录树。
- **`[[wikilinks]]` 自动建链。** 正文里的双向链接在写入时即被解析,关系永远不会与正文漂移。
- **Pending links(待定链接)。** 指向尚未存在笔记的前向引用会先存在一张带索引的表里,目标笔记一创建就自动转正。
- **全文检索。** SQLite FTS5,自动忽略软删除的笔记。在 5 万条笔记规模下返回结果 < 100ms。
- **软删除是默认。** `delete` 可撤销;`delete --hard` 才是永久删除。
- **类型化关系。** `references`(引用)、`contradicts`(矛盾)、`expands`(扩展)、`related`(相关),可附 strength(强度)权重。
- **图谱遍历。** 基于递归 CTE,从任一节点出发遍历子图。
- **导入 / 导出。** 支持 JSON、Markdown(兼容 Obsidian)、CSV 三种格式的双向转换。
- **Distill 与 Archive。** 目标导向的 `distill --query "X"` 写出一个聚焦的工作脑子(原脑子不动,加 `--activate` 才替换);`archive --older-than-days 180` 把长期不碰的笔记搬到冷库,顺手 VACUUM 把工作脑子收小。要找回来用 `merge-brain --from <archive>`。
- **Phase 2(规划中)。** 通过 `sqlite-vec` 提供的可选向量检索,以及 MCP server 接口。

## 安装

```bash
git clone https://github.com/stancsz/secondbrain.git
cd secondbrain
python3 scripts/brain_cli.py stats    # 首次运行会自动创建 ~/.secondbrain/brain.db
```

可选 —— 把命令缩短成 `brain`:

```bash
ln -s "$(pwd)/scripts/brain_cli.py" /usr/local/bin/brain
# 或
alias brain='python3 ~/path/to/secondbrain/scripts/brain_cli.py'
```

唯一运行依赖是 Python 3.8+ 自带的 `sqlite3`。Schema 使用 FTS5、JSON1、递归 CTE:Python 自带 SQLite 3.9+ 已包含这些,否则需要 SQLite 3.41+。

## 快速上手

```bash
# 存
python3 scripts/brain_cli.py add "RAG" "检索增强生成" \
  --collection AI --tags rag,llm

# 找
python3 scripts/brain_cli.py search "RAG"

# 连(正文里的 [[RAG]] 自动解析为 references 关系;
# 如果 RAG 还不存在,就进 pending_links,等 RAG 创建时自动连上)
python3 scripts/brain_cli.py add "Vector Search" "见 [[RAG]]" --collection AI

# 遍历图谱
python3 scripts/brain_cli.py related <id>
python3 scripts/brain_cli.py traverse <id> --depth 2

# 脑子健康度
python3 scripts/brain_cli.py summary

# Distill:基于目标做聚焦,原脑子留着做时间点备份
python3 scripts/brain_cli.py distill --query "RAG" --output focused.db --activate

# Archive:把长期不碰的笔记挪到冷库,工作脑子收小
python3 scripts/brain_cli.py archive --output archive-2026.db --older-than-days 180

# 把归档的笔记找回来
python3 scripts/brain_cli.py merge-brain --from archive-2026.db

# 导出(兼容 Obsidian)
python3 scripts/brain_cli.py export --format markdown --output brain.md
```

## 在 Claude Code 中使用

本仓库本身就是一个 Claude Code skill —— `SKILL.md` 定义了触发条件与行为契约。三种安装方式:

**项目级**(只对当前项目生效):

```bash
mkdir -p .claude/skills
git clone https://github.com/stancsz/secondbrain.git .claude/skills/secondbrain
```

**个人级**(所有项目都生效):

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/stancsz/secondbrain.git ~/.claude/skills/secondbrain
```

**Submodule**(锁定版本,跟随项目升级):

```bash
git submodule add https://github.com/stancsz/secondbrain.git .claude/skills/secondbrain
```

装好后,agent 会自动识别 "记一下"、"我之前写过 X 吗"、"catch me up on project Y" 这类表达,并从你脑中的笔记里找答案。

## 与同类工具的对比

| 工具 | 数据位置 | Agent 可读 | 锁定 | 备份 | 跨会话记忆 | 安装方式 |
|---|---|---|---|---|---|---|
| Notion AI | Notion 云 | 否 | 高 | 厂商控制 | 否 | 浏览器 |
| ChatGPT Memory | OpenAI 云 | 否 | 完全黑盒 | 厂商控制 | 是(不可见) | 浏览器 |
| Claude Projects | Anthropic 云 | 否 | 高 | 厂商控制 | 是(项目内) | 浏览器 |
| mem0 | 厂商 Postgres | 是(按 API 收费) | 中(SDK 绑定) | 厂商控制 | 是(API) | `pip install` + key |
| Obsidian | 本地 `.md` | 否(需插件) | 无 | 手动 | 否(自己接) | 桌面 App |
| Logseq | 本地 `.md` | 否 | 无 | 手动 | 否 | 桌面 App |
| Anytype | 本地(P2P) | 否 | 无 | 手动同步 | 否 | 桌面 App |
| Quivr / privateGPT | 本地向量库 | 通过 API | 无 | 手动 | 否 | Docker + 模型 |
| Apple Notes / Keep / OneNote | 厂商云 | 否 | 高 | 厂商控制 | 否 | 系统自带 |
| Evernote | 厂商云 | 否 | 高(历史教训) | 厂商控制 | 否 | 桌面 / Web |
| **secondbrain** | **本地 SQLite** | **是(CLI)** | **无** | **`cp` / `git push`** | **是(agent 原生)** | **`git clone`** |

**这份列表里,只有 `secondbrain` 能给的承诺:**

1. **数据完全归你。** 存储就是一个普通 SQLite 文件,`sqlite3 brain.db` 直接打开。Schema 在仓库里以 `scripts/schema.sql` 形式维护。没有"导出"这个流程,因为从来没有进过别人的服务器。
2. **可版本化。** 整个脑子就一个文件。`git init` 它,`git push` 到私人 GitHub repo,免费拿到历史、diff、灾备。
3. **Agent 原生。** CLI 就是 API。不存在一个独立的"AI 模式"需要你再付一笔钱。

## 适合你,如果

- 你使用 AI agent(Claude Code、Cursor、Aider、Continue、自建脚本),并希望它们跨会话"记得"。
- 你希望知识库在任何一家供应商消失后依然存在。
- 你能接受一个 200 行的 Python CLI + 一个 SQLite 文件。
- 你希望人和 agent 用同一份数据、同一个接口。

## 不适合你,如果

- 你要的是面向非技术用户的所见即所得笔记 App → 用 Obsidian 或 Notion。
- 你要的是带权限、评论的团队 Wiki → 用 Notion 或 Confluence。
- 你要存几百万条文档、跑大规模向量检索 → 用专业向量数据库;`secondbrain` 是个人量级的。
- 你本地不能跑 Python → 用托管笔记服务。

## 架构

参见 [`references/architecture.md`](./references/architecture.md),包含:

- 数据模型(3 张表 + FTS + `pending_links`)
- FTS5 正确性说明(v2 的 bug 与 v2.1 的修复)
- Wikilink 解析规则(写时冻结)
- 软删除语义
- Phase 2 的 MCP 接口契约
- v1 → v2 迁移
- 性能目标

## 备份策略

推荐方案:把 `~/.secondbrain/brain.db` 放进一个私人 GitHub repo 做版本化。整个数据库就一个文件,即使 5 万条笔记也通常 < 100 MB,`git push` 完全没问题。

要持续备份,可配 [litestream](https://litestream.io/) 把 WAL 流复制到 S3、Backblaze 或任何 S3 兼容的对象存储。Schema 迁移与灾难恢复都是标准 SQLite 操作。

## 路线图

- **v2.1(当前)。** FTS5、软删除、写时冻结的 wikilinks、`pending_links` 表、递归遍历。
- **Phase 2。** MCP server,基于 `sqlite-vec` 的向量检索,相似度超阈值时自动建立 `inferred` 类型关系。
- **想法。** Markdown 双向同步、Obsidian 兼容导出改进、本地加密副本。

## 贡献

欢迎提 Issue 和 PR。Schema 就是 API —— 加表、加列前请先开个 Issue 讨论。

## 许可

[MIT](./LICENSE) © 2026 secondbrain contributors
