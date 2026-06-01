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

## 为什么这个比 [mem0 / Obsidian / Logseq / ChatGPT Memory / Notion AI / Quivr] 都好?

> **不是把笔记托管给第三方的 SaaS。是把"你的脑子"变成一个可以 `git clone` 的小文件,然后让每一个 AI 都接上去。**

### 故事先讲清楚

十年前你的笔记存在 Evernote,后来它差点倒闭,大家连夜导出。
五年前你的笔记存在 Notion,现在每年涨价,API 收紧,导出要 Pro。
两年前你的"AI 记忆"存在 mem0 / ChatGPT / Claude Projects 里,服务器一关、API 一改、账号一封,你的第二大脑就没了。
**每一次,你的脑子都在被租回来给你。**

secondbrain 走另一条路:你的脑子,一个 SQLite 文件,你的 home 目录,你的 git repo,一个 `cp` / `scp` / `git push` 就能备份到任何地方。**没有 "数据迁出" 这个概念,因为从来没有进过别人的服务器。**

### 跟所有这些的对比

| 工具 | 数据在哪 | AI 能直接读吗 | vendor lock-in | 跨会话记忆 | 关系图谱 | 备份 | 装环境 |
|---|---|---|---|---|---|---|---|
| **Notion AI** | Notion 的云 | ❌(锁在 UI 里) | ⚠️⚠️⚠️ 极强(导出丢结构) | ❌(每次重新开始) | ❌ | 跟 Notion 走 | 浏览器 |
| **ChatGPT Memory** | OpenAI 的云 | ❌(你看不到原始) | ⚠️⚠️⚠️ 完全黑盒 | ✅(但你无法控制) | ❌ | 跟 OpenAI 走 | 浏览器 |
| **Claude Projects** | Anthropic 的云 | ❌ | ⚠️⚠️⚠️ | ✅(项目级,跨对话) | ❌ | 跟 Anthropic 走 | 浏览器 |
| **mem0** | 别人的 Postgres / vector DB | ✅(按 API 调用收费) | ⚠️⚠️(SDK 绑定) | ✅(API 实现) | ❌ | 跟 mem0 走 | `pip install` + API key |
| **Obsidian** | 你硬盘的 `.md` | ❌(AI 插件另装,各自为战) | ✅ 几乎无 | ❌(要自己写脚本) | ✅(要装插件) | 自己想办法(`.md` 文件散落) | 装客户端 |
| **Logseq** | 你硬盘的 `.md` / `.org` | ❌ | ✅ 无 | ❌ | ✅ | 同上 | 装客户端 |
| **Anytype** | 你硬盘(IPFS-like) | ❌ | ✅ 无 | ❌ | ✅(原生) | 自己同步 | 装客户端 |
| **Quivr / privateGPT / RAGFlow** | 本地向量 DB | ❌(给你 API) | ✅ 无 | ❌(自己接) | ❌ | 自己搞 | `docker compose up` 起服务 |
| **Apple Notes / Google Keep / OneNote** | 厂商云 | ❌ | ⚠️⚠️⚠️(苹果/谷歌/微软) | ❌ | ❌ | 跟厂商走 | 系统自带 |
| **Evernote** | Evernote 云 | ❌ | ⚠️⚠️⚠️(历史教训) | ❌ | ❌ | 跟 Evernote 走 | 客户端 |
| **Roam / Tana / Mem.ai** | 厂商云 | 部分(自家 AI) | ⚠️⚠️ | 部分 | ✅ | 跟厂商走 | 浏览器 |
| | | | | | | | |
| **secondbrain** | **你的 `~/.secondbrain/brain.db`** | ✅ **CLI / Python 库直接调** | ✅ **零 — 一个 SQLite 文件** | ✅ **agent 原生** | ✅ **wikilink 写时冻结** | ✅ **`git push` 一个文件** | ✅ **`git clone` 然后跑** |

### 三个只有 secondbrain 能给的承诺

1. **🔒 你的文件都是你的。** `~/.secondbrain/brain.db` 是一个普通 SQLite 文件。`sqlite3 brain.db` 就能开,`pg_dump` 风格的备份不存在,因为不需要 — 复制粘贴就是备份。`brain.db` 的 schema 在 `scripts/schema.sql` 里,你的脑子永远是可读的、十年后可解的、跟语言无关的。

2. **📦 你的脑子可以存在一个 GitHub repo,不怕丢。** `brain.db` 一个文件,几 MB 到几百 MB,`git push` 完事。mem0 倒了,Notion 改收费了,OpenAI 改了 memory 政策,你还在 — 因为你根本不在他们那儿。私人 repo,免费,加密,带历史 diff。

3. **🤖 AI agent 原生,不是给人用的。** Obsidian 给人看,Notion 给人点,mem0 给另一个 AI 调。secondbrain 是 **给任何 agent 调的** — `python3 scripts/brain_cli.py search "X"` 一行命令,agent 就有上下文。Claude Code、Cursor、Continue、Aider、自家脚本,谁接谁用,不用付 API 费,不用注册账号,数据不过境。

### 这不是为所有人设计的

secondbrain 适合你,如果:
- 你用 AI agent (Claude Code / Cursor / Aider) 而且想让它"记得"你说过什么
- 你信不过 SaaS 把你的脑子存在别人的 Postgres 里
- 你想要一个 `git clone` 就能搬家、`rm` 就能销毁的脑子
- 你能接受一个 200 行的 Python CLI(没有华丽的 UI)

secondbrain **不适合**你,如果:
- 你想要一个漂亮的、所见即所得的笔记 GUI → 用 Obsidian
- 你想给非技术朋友用 → 用 Notion / Apple Notes
- 你想做团队的 wiki → 用 Notion / Confluence
- 你要 1M+ 条带向量检索的笔记 → 升级到 Quivr / dedicated vector DB(Phase 2 会加)

### 跟"开源向量记忆"那一类(Quivr / privateGPT)比

- 那些是 **RAG pipeline**,你给一堆 PDF,问问题回答。secondbrain 是 **knowledge graph + FTS**,你主动写、主动搜、主动连。
- 那些要 Docker / GPU / 模型权重。secondbrain 零依赖,纯 stdlib + SQLite。
- 那些是 **read-only** 的:导入,不再修改。secondbrain 是 **read-write**:agent 能存、能改、能删、能建关系。

> 一句话:**你不是在找一个"更聪明的笔记 App"。你是在找一个你的 AI 真正能"住进去"的地方。**

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
