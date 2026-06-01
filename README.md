# secondbrain | 长脑子 | 脑子不够用了

> **你的第二个脑子,装在 GitHub repo 里,永远不会丢。**
> A local, file-based knowledge graph you fully own — for capturing, searching, linking, and recalling notes across every AI conversation.

```
┌──────────────────────────────────────────────┐
│  secondbrain                                │
│  ───────────                                │
│  your files. your schema. your backup.     │
│  one SQLite file. one git repo. forever.  │
└──────────────────────────────────────────────┘
```

---

## 为什么这个比 mem0 / Obsidian 好?

| | mem0 | Obsidian | **secondbrain** |
|---|---|---|---|
| 你的数据存在哪? | 别人的云 / 别人的 Postgres | 你的硬盘 (`.md` 文件) | **你的硬盘 + 你的 git** (`brain.db`) |
| AI agent 能直接读写吗? | ✅(API 收费) | ❌ (纯本地 markdown) | ✅(`python3 scripts/brain_cli.py …`) |
| 全文搜索 | 云端向量 | 本地索引 (快) | **本地 FTS5 (sqlite) + 可选向量** |
| 跨会话记忆 | 一次性 API | 手动复制粘贴 | **自动 — agent 直接调 CLI** |
| 关系图谱 (wiki links) | ❌ | ✅(但要装插件) | ✅(写时冻结,不会漂移) |
| Vendor lock-in | ⚠️ 高 | ✅ 无 | ✅ **无** — 一个 SQLite 文件 |
| 装环境? | pip install + API key | 装客户端 | **零依赖,系统自带 Python 即可** |
| 备份 | 跟 mem0 走 | 自己想办法 | **`git push` 完事** |

### 三个核心卖点

1. **🔒 你的文件都是你的。** 一个 `~/.secondbrain/brain.db`,标准 SQLite,任何客户端都能打开。没有 "数据迁出" 这个概念。
2. **📦 你的脑子可以存在一个 GitHub repo,不怕丢。** `git push` 整个脑子上云,`git clone` 整脑子回家。mem0 倒了,Obsidian 改收费了,你还在。
3. **🤖 AI agent 原生。** 不是给人用的笔记 App,是给 agent 用的 memory store。每个 AI 会话都自动接上你的脑子,知道"我去年写过这个"。

> 不是把笔记托管给第三方的 SaaS。是把"你的脑子"变成一个可以 `git clone` 的小文件,然后让每一个 AI 都接上去。

---

## 安装

### 零依赖(只要 Python 3.8+)

```bash
git clone https://github.com/stancsz/secondbrain.git
cd secondbrain
python3 scripts/brain_cli.py stats    # 第一次会自动建库 ~/.secondbrain/brain.db
```

就这样。SQLite 走 Python 标准库,没有 `pip install`、没有 `npm install`、没有 Docker。

### 可选:放到 PATH(让命令更短)

```bash
# 方式 1: 软链
ln -s "$(pwd)/scripts/brain_cli.py" /usr/local/bin/brain

# 方式 2: 加 alias
echo 'alias brain="python3 ~/path/to/secondbrain/scripts/brain_cli.py"' >> ~/.zshrc
```

之后:

```bash
brain add "Attention Is All You Need" "<abstract>" --tags ml,transformers --collection Research
brain search "RAG"
brain stats
```

---

## 在 Claude Code 里使用

这个 repo 本身就是一个 [Claude Code skill](https://docs.claude.com/en/docs/claude-code/skills) — `SKILL.md` 就是 skill 的入口,定义触发短语和行为契约。

### 方式 1: 项目级(推荐)

在你自己的项目里:

```bash
# 从 GitHub 拉
mkdir -p .claude/skills
git clone https://github.com/stancsz/secondbrain.git .claude/skills/secondbrain
```

或者用 submodule:

```bash
git submodule add https://github.com/stancsz/secondbrain.git .claude/skills/secondbrain
```

之后在任何 Claude Code 会话里说:

- *"帮我记一下这个..."* → 自动 `add`
- *"我之前写过 RAG 吗?"* → 自动 `search RAG` 然后引用 drawer id
- *"catch me up on project X"* → 自动 `list --collection X`
- *"Braid 项目里我缺什么?"* → 自动 gap 分析

agent 会从你的人格子里调数据,**用你的话回答你**,而不是从训练数据里瞎编。

### 方式 2: 全局(personal scope)

所有项目都能用:

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/stancsz/secondbrain.git ~/.claude/skills/secondbrain
```

### 方式 3: 在当前项目里直接 symlink

如果你已经在 `secondbrain/` 目录里跑 Claude Code:

```bash
ln -s ../. .claude/skills/secondbrain   # 把整个 repo 当成 skill
```

---

## 30 秒上手

```bash
# 1. 存一条
python3 scripts/brain_cli.py add "RAG" "检索增强生成:用检索 + LLM 回答" \
  --collection AI --tags rag,llm

# 2. 找一条
python3 scripts/brain_cli.py search "RAG"

# 3. 关联两条
python3 scripts/brain_cli.py add "Vector Search" "向量检索,见 [[RAG]]" --collection AI
# 上面的 [[RAG]] 在保存时自动解析成 references 关系
# 如果 RAG 还不存在,就进 pending_links,等你以后创建 RAG 时自动连上

# 4. 看图谱
python3 scripts/brain_cli.py related <id>
python3 scripts/brain_cli.py traverse <id> --depth 2

# 5. 导出(给 Obsidian / Notion / 任何工具)
python3 scripts/brain_cli.py export --format markdown --output brain.md
```

---

## 文件结构

```
secondbrain/
├── SKILL.md                    ← Claude Code skill 入口(触发条件 + 行为契约)
├── README.md                   ← 你正在看的这个
├── references/
│   └── architecture.md         ← schema 设计、FTS5 正确性、wikilink 冻结规则
├── scripts/
│   ├── brain.py                ← 核心(SecondBrain class, ~600 行,零依赖)
│   ├── brain_cli.py            ← CLI 封装
│   └── schema.sql              ← SQLite schema v2.1
└── secondbrain.skill           ← 打包好的 skill 文件(供支持 .skill 导入的环境)
```

数据库存在 `~/.secondbrain/brain.db`(可被 `litestream` 之类的工具自动备份到 S3)。

---

## 备份到 GitHub(脑子永存)

```bash
# 一行命令就能备份:把数据库放进一个私人 repo,定时 push
cd ~/.secondbrain
git init && git add brain.db && git commit -m "brain snapshot"
git remote add origin git@github.com:<you>/my-secondbrain.git
git push -u origin main

# 然后加个 cron / launchd 每天 push
```

或者更简单 — 把整个 `secondbrain/` 目录就是 repo,数据库在 home 目录,gitignored,定期用 `litestream` 推到 S3 / GitHub。schema 是显式的 `schema.sql`,数据是 SQLite,迁移、对比、恢复都是标准操作。

---

## 路线图

- ✅ **v2.1 (现在)** — FTS5 / 软删除 / wikilink 写时冻结 / `pending_links` 表
- 🔜 **Phase 2** — MCP server、向量检索 (sqlite-vec)、自动 link
- 💭 想法 — Markdown 双向同步、Obsidian 兼容 export、加密本地副本

参见 `references/architecture.md` 了解 schema 细节和 v2 → v2.1 改了什么。

---

## License

MIT — 你的脑子,你的自由。
