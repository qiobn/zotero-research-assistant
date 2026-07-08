# Zotero 智能文献助手

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
[![MCP](https://img.shields.io/badge/protocol-MCP-purple.svg)](https://modelcontextprotocol.io/)

**[English](./README.md)** | **[中文](./README_zh.md)**

---

> **将 Zotero 变成 AI 驱动的科研引擎。**
>
> 按语义搜索、跨 2 亿篇文献发现关联论文、获取个性化阅读推荐、管理学术工作流——一切通过自然语言完成。

支持 **Cursor**、**Claude Desktop**、**Cherry Studio**、**Trae**、**OpenAI Codex CLI** 及所有兼容 MCP 协议的客户端。

## 写在前面

本项目的出发点是帮助没有太多计算机操作基础的学生和科研工作者，让他们也能利用 AI 增强的 Zotero 来提升学术研究效率。因此文档会写得尽量详细、步骤尽量清晰，并且选择了 Cherry Studio 作为主要的交互界面——它提供了友好的图形化操作，不需要使用终端命令行。我们相信，强大的科研工具应该让每个人都能用上，而不只是程序员。

**如果你没有编程基础**，请直接阅读 [docs/cherry-studio-setup.md](./docs/cherry-studio-setup.md)，跟着里面的步骤一步步操作即可。尽量独立完成——如果遇到问题，把报错信息复制给任意一个 AI 对话工具（ChatGPT、DeepSeek、Kimi 等）寻求帮助。把这次配置当作你接触程序和 AI 工具的第一步，比你想象的简单。

---

### 核心亮点

| | |
|---|---|
| **35 个 MCP 工具** | 一个工具对应一个意图，大模型总能选对 |
| **混合 RAG 搜索** | 关键字 + 语义（bge-m3，100+ 语言）+ 交叉编码器重排序 |
| **语义分块** | 段落感知切分，自动检测参考文献段、图表标注，7 维质量评分 |
| **文本清洗引擎** | 52 条黑名单规则去除期刊 boilerplate（页眉页脚、文章信息栏），中英文全覆盖 |
| **章节级上下文扩展** | 命中 chunk → 自动展开到所在完整章节，赋予 LLM 更丰富的语境 |
| **SQLite 元数据库** | 7 张关系表（论文、章节、chunk 元数据、图、表），与向量库分离 |
| **检索可观测性** | JSONL 全链路追踪 + 字节偏移索引，可回溯任意历史检索的排序细节 |
| **多源文献发现** | OpenAlex + CrossRef + Semantic Scholar 并行检索，三索引交叉验证杜绝编造引用 |
| **引用网络扩展** | 语料优先策略 + 正向/反向引用 + OpenAlex 相关论文 |
| **反幻觉机制** | 零编造策略 + `[MATERIAL GAP]` 结构化标签；每篇论文附可验证来源链接 |
| **RAG 评估** | Recall@K、MRR、NDCG 指标 + A/B 基线对比 + 60 条黄金查询 |
| **嵌入质量诊断** | 6 阶段分析：论文内/间分离度、离群 chunk 检测、长度相关性、章节类型分析 |
| **个性化推荐** | 基于阅读行为和标注推荐下一篇该读什么 |
| **文献综述生成** | 选定论文 → 提取证据及引用 → AI 生成主题综述 |
| **智能标签建议** | 自动分析元数据推荐方法论/领域/数据标签（建议制，不自动应用） |
| **论据发现器** | 从文库中寻找支持或反对你观点的证据 |
| **CNKI 集成** | 可选的中文知网检索，带期刊等级标签（CSSCI/北大核心/CSCD） |
| **OA PDF 瀑布流** | arXiv → Unpaywall → OpenAlex → S2 → CORE → PMC 自动获取全文 |
| **写入安全** | 所有写操作需明确确认（默认 dry-run 预览） |

---

## 目录

- [功能特性](#功能特性)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [客户端配置](#客户端配置)
  - [Cursor](#cursor)
  - [Claude Desktop](#claude-desktop)
  - [Cherry Studio](#cherry-studio)
  - [OpenAI Codex CLI](#openai-codex-cli)
- [使用示例](#使用示例)
- [MCP 工具一览 (32)](#mcp-工具一览-32)
- [配置项说明](#配置项说明)
- [CNKI 配置（可选）](#cnki-配置可选)
- [升级方式](#升级方式)
- [常见问题](#常见问题)
- [架构说明](#架构说明)
- [开发指南](#开发指南)
- [致谢](#致谢)
- [免责声明](#免责声明)
- [许可证](#许可证)

---

## 功能特性

### 本地文库智能

- **混合搜索** — Zotero 关键字搜索 + ChromaDB 语义搜索，通过 RRF 融合排序；回退至 Zotero 全文索引
- **纯筛选模式** — 空查询 + `year_from` / tags 实现按条件列表
- **交叉编码器重排序** — 可选 `ms-marco-MiniLM-L-6-v2` 提升精度
- **多语言支持** — `BAAI/bge-m3` 嵌入模型（1024 维，100+ 语言含中英文）
- **页码溯源** — 检索段落标注精确 PDF 页码
- **全文与大纲** — 阅读论文完整文本或 PDF 目录
- **增量索引同步** — 基于版本号增量更新；MCP 启动时自动同步

### 语义 RAG 管线

- **文本清洗** — 52 条黑名单正则规则在分块前去除期刊 boilerplate 噪声（英文：文章信息栏、页眉；中文：卷期号、中图分类号、基金信息；通用：页码、DOI 行、重复标点）。由 `ZRA_CLEAN_ENABLED=true` 控制（默认开启）。清洗统计随 `sync_index` 报告。
- **段落感知分块** — 按自然边界（段落→句子）切分，自适应合并至目标 600 字符；中文感知断句（`。！？` 无需空格即断），并修复 PDF 排版软换行（`满\n意度`→`满意度`），避免句子被从中间切断
- **Chunk 质量评分** — 每个 chunk 附带 7 维质量字段：`coherence_score`（句子长度变异度）、`information_density`（停用词占比）、`boilerplate_ratio`、`sentence_count`、`starts_with_conjunction`、`language`（zh/en/mixed）、`quality_flag`（good/noisy/incomplete/boilerplate）。存入 ChromaDB 元数据，支持质量感知过滤。
- **章节检测** — IMRaD 分类通过正则标题匹配（英文编号 "1. Introduction"，中文 "一、引言"）；自动识别并排除参考文献/boilerplate 段落
- **图表标注检测** — 识别 `Figure/Fig./Table/图/表` 等标注格式，标记含图表的 chunk 以便精准检索
- **表格 / 图交叉引用** — 表格和图都作为轻量"标题锚点记录"入库，**不做单元格结构化**。表格保留：在哪里、标题、以及从标题到正文恢复之间的原始内容块（让表内数值仍可被检索）；图仅保留：在哪里 + 标题大致内容（不做图像识别）。正文里"如表3所示 / 如图2所示"的段落会自动链接到对应记录，`get_paper_content` 返回 `referenced_tables` / `referenced_figures`。（真正的表格结构化是视觉问题——可选视觉解析器见 [表格与图](#表格与图)。）
- **章节级上下文扩展** — `expand_context=True` 时，命中 chunk 的同时从 SQLite + ChromaDB 获取其所在完整章节，赋予 LLM 完整的段落语境而非孤立片段。缓存批量查询保证性能。
- **SQLite 元数据库** — 独立的关系型数据层（位于 `.chroma_db/papers.db`），含 7 张表：`papers`（标题、摘要、作者、年份、DOI、关键词）、`sections`（层级化 IMRaD 结构）、`chunks_meta`（位置 + 质量评分）、`figures`、`table_records` 及交叉引用表。零用户配置——首次同步自动创建。
- **嵌入质量诊断** — 6 阶段分析管线：每篇论文内部相似度、论文间分离度比率、离群 chunk 检测（质心一致性 < 0.3）、chunk 长度与相似度 Pearson 相关性、章节类型嵌入分离度、自动问题检测与修复建议。
- **评估框架** — 60 条黄金查询（直接命中、跨文档综合、无答案拒绝三类），指标：Recall@5/10/20、MRR、NDCG@10。CLI 支持 `--save-baseline` 和 `--compare` 进行 A/B 对比。
- **检索日志** — 每次搜索输出 JSONL 全链路追踪（查询、策略、候选数、重排序详情、top-20 结果及分数、关键词/语义/重排序/总计延迟分解）。字节偏移索引文件支持按 trace ID 快速回溯。三个查询工具：`recent_retrievals`、`retrieval_trace`、`retrieval_stats`。
- **分块版本化** — 策略变更自动触发全量重建索引，杜绝陈旧数据
- **索引诊断** — `inspect_index` 展示 chunk 统计、质量标签分布、章节分布、图表 chunk 数量、乱码检测
- **召回率自测** — `test_recall` 验证论文自身 chunk 是否出现在 top-20 结果中
- **健康监控** — `check_health` 诊断连接、索引、嵌入模型和配置状态

### 在线文献发现

- **多源并行检索** — 同时查询 OpenAlex、CrossRef、Semantic Scholar，出版商多样性排序
- **语料优先策略** — 当已知论文的参考文献可用时，优先从已知引用扩展引用网络
- **学科过滤** — 可选 `fields_of_study` 参数限定学科领域，避免跨领域干扰
- **相关论文发现** — 提供论文元数据 → 自动生成分层配对查询 → 多源检索 → 后过滤 → 去重返回
- **三索引交叉验证** — 每个有 DOI 的结果在 CrossRef、OpenAlex、S2 中交叉核实；无法验证的自动过滤
- **来源可溯** — 每篇返回结果附可验证链接（DOI URL、S2 URL 或知网链接）
- **反幻觉护栏** — 搜索零结果时生成 `[MATERIAL GAP]` 结构化标签

### CNKI（中文文献）

- **知网集成** — 可选的浏览器自动化中文期刊检索（默认关闭）
- **期刊等级标签** — 结果包含索引标识（CSSCI、北大核心、CSCD、SCI、EI）
- **直接导入 Zotero** — 从知网导出到 Zotero，无需手动查 DOI
- **论文详情提取** — 完整元数据（摘要、关键词、DOI、机构）
- **智能翻页** — AI 在需要更多结果时主动翻页

### 阅读洞察与推荐

- **阅读状态检测** — 启发式分类（深度阅读/浏览过/未读），基于标注数量、笔记和 PDF 打开记录
- **个性化推荐** — 识别最投入的论文 → 并行查询 OpenAlex + S2 推荐 → 按交叉命中频率排序
- **聚焦主题提取** — 从近期阅读标签中提炼活跃研究方向
- **文献综述生成** — 选择多篇论文 → 提取相关段落附页码引用 → 供 AI 综合为主题综述
- **智能标签建议** — 分析标题/摘要推荐方法论、领域和数据类型标签；建议制（不自动应用）
- **论据发现** — 给定观点，从文库中按立场分类（支持/反对/中立）检索证据

### 文库管理

- **添加论文** — 支持 DOI、arXiv、ISBN、BibTeX 或出版商 URL
- **OA PDF 瀑布流** — arXiv → Unpaywall → OpenAlex → S2 → CORE → PMC
- **重复合并** — 按 DOI/标题检测，合并前预览确认
- **标注功能** — 跨文库搜索高亮；在 PDF 上创建标注
- **写入安全** — 所有写/删操作先预览再确认
- **Zotero 混合模式** — 本地快速读取 + Web API 写入

---

## 环境要求

| 组件 | 版本/说明 |
|------|-----------|
| **Python** | 3.11+ |
| **Zotero** | 7+ 桌面版，需开启本地 API |
| **MCP 客户端** | Cursor、Claude Desktop、Cherry Studio、Trae、Codex CLI 等 |
| **大模型** | 支持 tool/function calling 的模型（Claude、GPT-4o、DeepSeek、Qwen、Gemini…） |
| **磁盘** | 首次运行约 2.5 GB（嵌入模型 `bge-m3`） |
| **Git** | 仅源码安装（方式 B）需要 |

> **路径建议：** 安装路径不要含空格或中文字符，例如 `~/zotero-research-assistant`（macOS/Linux）或 `C:\Dev\zotero-research-assistant`（Windows）。

---

## 快速开始

### 1. 安装

**方式 A：pip 安装（推荐大多数用户）**

```bash
pip install zotero-research-assistant
```

需要知网功能：
```bash
pip install "zotero-research-assistant[cnki]"
```

安装后运行 `zra-mcp` 启动 MCP 服务端。跳到[第 2 步](#2-配置-zotero)。

**方式 B：从源码安装（开发或自定义）**

```bash
git clone https://github.com/qiobn/zotero-research-assistant.git
cd zotero-research-assistant
```

安装 [uv](https://github.com/astral-sh/uv)（快速 Python 包管理器）：

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
irm https://astral.sh/uv/install.ps1 | iex
```

创建虚拟环境并安装：

```bash
uv venv .venv --python 3.13      # 若不可用则使用 3.12 或 3.11
uv pip install -e .
```

验证安装：

```bash
# macOS / Linux
source .venv/bin/activate
python -c "from project_a_mcp.server import mcp; print('OK')"

# Windows (PowerShell)
.venv\Scripts\activate
python -c "from project_a_mcp.server import mcp; print('OK')"
```

> 首次运行会下载嵌入模型（约 2.3 GB）。国内用户建议先设置镜像：`export HF_ENDPOINT=https://hf-mirror.com`（或写入 `.env` 文件），再运行即可正常下载。

### 2. 配置 Zotero

**启用本地 API**（必需）：

1. 打开 Zotero → **编辑 → 首选项 → 高级**
2. 勾选 **"允许其他应用通过本地 API 访问 Zotero"**
3. 验证：浏览器访问 http://localhost:23119/api/ 应返回 JSON

**设置环境变量：**

源码安装用户在项目目录创建 `.env`：
```bash
cp .env.example .env
```

pip 安装用户在工作目录创建 `.env` 或设置 shell 环境变量。

最低配置（**只读模式** — 搜索、阅读、引用）：
```ini
ZOTERO_LOCAL=true
```

需要**写操作**（添加论文、笔记、标签）还需设置 [Zotero API Key](https://www.zotero.org/settings/keys)：
```ini
ZOTERO_LOCAL=true
ZOTERO_LIBRARY_ID=12345678
ZOTERO_API_KEY=your_api_key_here
```

### 3. 构建向量索引（首次）

MCP 服务端启动时**自动同步**（`ZRA_AUTO_SYNC=true`）。首次启动会自动解析所有 PDF 并构建语义索引。

手动构建：
```bash
python scripts/index_library.py
```

索引存储在 `.chroma_db/`（仅本地）。

> **首次建索引会比较慢，建议挂后台等待。** 第一次需要解析每篇 PDF 并计算语义向量，
> 文献越多越久（参考：100 篇约 3-5 分钟、500 篇约 10-15 分钟，CPU 或大库会更久）。
> 自动同步在后台线程进行、不阻塞客户端使用；手动跑 `index_library.py` 时也可以放后台
> （如 `nohup python scripts/index_library.py &`）。索引只在首次或文献变动时需要等待，
> 之后启动都是秒级增量同步。

### 4. 连接 AI 客户端

参见下方[客户端配置](#客户端配置)。

### 5. 测试连接

1. 启动 **Zotero 桌面端**
2. 在 MCP 客户端中打开**新对话**
3. 发送：*"列出我 Zotero 中的所有分组"*

若能看到分组列表，配置完成。

---

## 客户端配置

本项目是一个基于 **stdio** 的 MCP 服务端。配置内容到处都一样，区别只在于*放在哪里*。有两种基本写法：

- **pip 安装：** 命令就是 `zra-mcp`。
- **源码安装：** 填项目内 Python 解释器的完整路径，并设置工作目录。

```bash
# 源码安装 —— 获取 Python 路径（在项目目录内执行）
# macOS / Linux：
echo "$(pwd)/.venv/bin/python"
# Windows (PowerShell)：
echo "$PWD\.venv\Scripts\python.exe"
```

> **Windows 源码安装：** `command` 和 `cwd` 都用 `<项目>\.venv\Scripts\python.exe`。

### Cursor

**设置 → MCP → 添加 MCP 服务器**，或编辑 `.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "zra-mcp": { "command": "zra-mcp" }
  }
}
```

源码安装 —— 改用完整路径：

```json
{
  "mcpServers": {
    "zra-mcp": {
      "command": "/Users/you/zotero-research-assistant/.venv/bin/python",
      "args": ["-m", "project_a_mcp.server"],
      "cwd": "/Users/you/zotero-research-assistant"
    }
  }
}
```

重启 Cursor，Agent 模式下即可看到工具。

### Claude Desktop

**配置文件位置：**
- **macOS：** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows：** `%APPDATA%\Claude\claude_desktop_config.json`（例如 `C:\Users\你的用户名\AppData\Roaming\Claude\claude_desktop_config.json`）

创建或编辑该文件：

```json
{
  "mcpServers": {
    "zra-mcp": { "command": "zra-mcp" }
  }
}
```

源码安装 —— 把 `command` 换成 Python 完整路径，并加上 `args` + `cwd`：

```json
{
  "mcpServers": {
    "zra-mcp": {
      "command": "/Users/you/zotero-research-assistant/.venv/bin/python",
      "args": ["-m", "project_a_mcp.server"],
      "cwd": "/Users/you/zotero-research-assistant"
    }
  }
}
```

重启 Claude Desktop，输入区出现锤子图标即成功。点击可查看 35 个可用工具。

> **注意：** Claude Desktop 需要 Pro 或 Team 订阅。`.env` 文件自动从项目目录（源码安装）或当前工作目录（pip 安装）读取，shell 环境变量同样生效。

### Cherry Studio

**设置 → MCP 服务器 → 添加 → JSON 模式。** Cherry Studio 需要额外三个字段（`name`、`type`、`isActive`）：

```json
{
  "mcpServers": {
    "zra-mcp": {
      "name": "zra-mcp",
      "type": "stdio",
      "isActive": true,
      "command": "zra-mcp"
    }
  }
}
```

源码安装 —— 把 `command` 换成 Python 完整路径，并加上 `args` + `cwd`：

```json
{
  "mcpServers": {
    "zra-mcp": {
      "name": "zra-mcp",
      "type": "stdio",
      "isActive": true,
      "command": "/Users/you/zotero-research-assistant/.venv/bin/python",
      "args": ["-m", "project_a_mcp.server"],
      "cwd": "/Users/you/zotero-research-assistant"
    }
  }
}
```

随后：
1. **设置 → 模型服务** — 配置 LLM（DeepSeek、GPT-4o、Claude、Qwen 等）。推荐 Claude 或 GPT-4o，工具调用准确度更高。
2. 新建对话 → 点击聊天输入栏的 **MCP 开关**（插头图标）启用工具。
3. MCP 服务器状态应显示 **"已连接"**，并列出 35 个工具。

如果 `.env` 文件不在默认搜索路径中，在 JSON 中添加 `"cwd": "/你/.env所在目录"`。

完整图文教程见 [docs/cherry-studio-setup.md](./docs/cherry-studio-setup.md)。

### OpenAI Codex CLI

**pip 安装**（最简单）：

```json
{
  "mcpServers": {
    "zra-mcp": { "command": "zra-mcp" }
  }
}
```

**源码安装：**

```json
{
  "mcpServers": {
    "zra-mcp": {
      "command": "/Users/you/zotero-research-assistant/.venv/bin/python",
      "args": ["-m", "project_a_mcp.server"],
      "cwd": "/Users/you/zotero-research-assistant"
    }
  }
}
```

添加至 `~/.codex/config.json`（全局）或项目级 `<project>/.codex/config.json`。之后运行 `codex "帮我找文库中关于城市研究的论文"`，35 个工具会自动被发现。

运行 `codex mcp list` 可验证服务器已连接且所有工具已注册。环境变量从 `cwd` 目录的 `.env` 文件或 shell 环境中读取。

> **其他 stdio MCP 客户端**（Trae、Windsurf 等）配置方式完全相同 —— 指向上面的 `command` / `args` / `cwd` 即可；环境变量自动读取 `<项目>/.env`。

---

## 使用示例

### 文献发现

```
用户：找 2020 年后关于 15 分钟城市的论文
  → search_papers（本地文库）

用户：在线搜索城市绿色基础设施的最新研究
  → search_online_literature（OpenAlex + CrossRef + S2）

用户：我在读这篇论文 [标题, 关键词]，帮我找相关文献
  → find_related_literature（5 种策略并行，结果验证）

用户：这篇论文被谁引用了？它引用了什么？
  → expand_citation_network（正向 + 反向引用）
```

### 阅读与分析

```
用户：这篇论文的研究方法是什么？
  → get_paper_content（论文内语义搜索）

用户：把这 5 篇论文总结成关于"方法演进"的文献综述
  → generate_review_note → AI 生成带引用的主题综述

用户：我的论点是"公共服务分配不均"——帮我找证据
  → find_arguments（按立场分类返回证据）

用户：我下一步该读什么？
  → recommend_papers（基于标注行为推荐）

用户：这篇论文的图表讲了什么？
  → get_paper_content（筛选含图表标注的 chunk）
```

### 写作与引用

```
用户：我正在写"步行性是城市品质的关键指标…"——帮我推荐引用
  → suggest_citations（匹配草稿与文库证据）

用户：导出前 3 个结果的 BibTeX
  → export_bibliography

用户：添加这篇论文：10.1016/j.cities.2025.105902
  → add_paper（预览 → 确认 → 自动下载 OA PDF）
```

### 文库组织

```
用户：分析这些论文并推荐标签
  → suggest_tags（方法论/领域/数据分类，建议制）

用户：给这些论文打上"核心阅读"标签
  → edit_tags（预览 → 确认）

用户：哪些论文我读过？哪些没读？
  → reading_status（启发式判断：标注、笔记、PDF 打开记录）
```

### 系统诊断

```
用户：系统正常吗？
  → check_health（连接、索引、嵌入模型、配置诊断）

用户：我的索引质量怎么样？
  → inspect_index（chunk 统计、章节分布、图表数量）

用户：这篇论文能被正确检索到吗？
  → test_recall（用标题搜索，检查自身 chunk 是否在 top-20 内）
```

> **写入安全**：所有破坏性操作（添加论文、笔记、标签、合并重复）均先预览再确认。

---

## MCP 工具一览 (35)

| 类别 | 工具 |
|------|------|
| **发现** | `search_papers`, `search_online_literature`, `search_cnki_literature`, `find_related_literature`, `expand_citation_network`, `cnki_paper_detail`, `cnki_navigate_pages`, `find_similar_papers`, `browse_library`, `find_duplicates`, `merge_duplicates` |
| **阅读** | `get_paper`, `get_paper_content`, `search_annotations`, `create_annotation` |
| **写入** | `suggest_citations`, `export_bibliography`, `add_paper`, `cnki_add_to_zotero` |
| **管理** | `add_note`, `edit_tags`, `manage_collections` |
| **洞察** | `reading_status`, `recommend_papers`, `generate_review_note`, `generate_reading_note`, `suggest_tags`, `find_arguments` |
| **管控** | `sync_index`, `check_health`, `inspect_index`, `test_recall`, `recent_retrievals`, `retrieval_trace`, `retrieval_stats` |

<details>
<summary>展开工具详情</summary>

### 发现
- **`search_papers`** — 本地文库主搜索。混合关键字 + 语义。空查询 + `year_from` / tags 可做纯筛选。
- **`search_online_literature`** — 在线英文/国际文献发现（OpenAlex、CrossRef、Semantic Scholar）。支持 `fields_of_study` 学科过滤。
- **`search_cnki_literature`** — 知网中文期刊搜索（可选模块，默认关闭）。仅在用户明确要求中文文献时触发。
- **`find_related_literature`** — 多策略相关论文搜索。支持语料优先、关键字、引用网络扩展、S2 推荐并行执行。
- **`expand_citation_network`** — 通过引用关系发现论文（OpenAlex 正/反向引用），支持多 DOI 种子。
- **`cnki_paper_detail`** — 知网论文详细元数据。
- **`cnki_navigate_pages`** — 知网结果翻页/排序。
- **`find_similar_papers`** — 查找与已知论文相似的文献。
- **`browse_library`** — 浏览分组、标签、最近添加。
- **`find_duplicates`** / **`merge_duplicates`** — 检测并合并重复（默认 dry-run）。

### 阅读
- **`get_paper`** — 元数据 + 摘要。
- **`get_paper_content`** — 模式：语义查询、页码范围、全文、大纲；可选标注覆盖。
- **`search_annotations`** — 跨论文搜索高亮/评论。
- **`create_annotation`** — 在 PDF 上创建高亮（默认 dry-run）。

### 写入与管理
- **`suggest_citations`** — 将你的草稿文本匹配到文库证据。
- **`export_bibliography`** — 导出 BibTeX 或格式化引用。
- **`add_paper`** — 通过 DOI / arXiv / ISBN / BibTeX / URL 导入（默认 dry-run）。
- **`cnki_add_to_zotero`** — 直接导入知网论文（无需 DOI）。
- **`add_note`**, **`edit_tags`**, **`manage_collections`** — 文库组织操作（默认 dry-run）。

### 洞察
- **`reading_status`** — 分析阅读进度：深度阅读/浏览过/未读。
- **`recommend_papers`** — 个性化推荐。
- **`generate_review_note`** — 多论文证据提取，生成文献综述素材。
- **`generate_reading_note`** — 单篇论文结构化阅读笔记。
- **`suggest_tags`** — 分析元数据推荐标签，建议制。
- **`find_arguments`** — 按立场分类查找支持/反对证据。

### 管控
- **`sync_index`** — 增量同步向量索引。启动时自动运行。报告质量摘要、清洗统计，并检测分块版本变更。
- **`check_health`** — 诊断连接、索引、嵌入模型、在线 API 和配置。中英双语输出含修复建议。
- **`inspect_index`** — 索引质量检视：chunk 统计、质量标签分布、章节分布、图表 chunk 数量、乱码检测、单论文详情。
- **`test_recall`** — 对特定论文测试检索质量：用标题查询，验证自身 chunk 是否出现在 top-20 结果中。
- **`recent_retrievals`** — 浏览最近的检索记录（可按策略过滤：hybrid/semantic/keyword/fallback）。查看查询内容、返回结果数、延迟分解。
- **`retrieval_trace`** — 按 trace ID 回放某次检索的完整链路：查询、候选数、重排序详情、排序结果列表。用于排查"为什么这篇论文排在第 5 位而不是第 1 位？"
- **`retrieval_stats`** — 聚合统计：总查询数、平均延迟、策略分布、回退率。

</details>

---

## 配置项说明

将 [`.env.example`](./.env.example) 复制为 `.env` 并修改：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ZOTERO_LOCAL` | `true` | 从本地 Zotero API 读取（快） |
| `ZOTERO_API_KEY` | — | 写操作必需（混合模式） |
| `ZOTERO_LIBRARY_ID` | `0` | 你的 Zotero 用户 ID |
| `EMBEDDING_BACKEND` | `auto` | 后端选择：`auto`（优先 ONNX INT8）、`onnx_int8`、`sentence_transformers` |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | 语义搜索用的 sentence-transformer（仅 FP32 后端；ONNX INT8 自动使用预量化模型） |
| `EMBEDDING_MAX_SEQ_LEN` | `1024` | 嵌入序列长度上限；防止异常长输入撑爆 GPU/MPS 显存 |
| `HF_ENDPOINT` | — | HuggingFace 镜像地址（国内用户用 `https://hf-mirror.com` 加速模型下载） |
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | 重排序模型（设 `none` 禁用） |
| `CHROMA_PERSIST_DIR` | `.chroma_db` | 本地向量数据库路径 |
| `ZRA_AUTO_SYNC` | `true` | MCP 启动时自动增量同步 |
| `ZRA_CLEAN_ENABLED` | `true` | 分块前去除期刊 boilerplate 噪声（页眉页脚、文章信息栏） |
| `SEMANTIC_SCHOLAR_API_KEY` | — | 可选；提升在线搜索速率 |
| `OPENALEX_MAILTO` | — | 可选；OpenAlex 礼貌池 |
| `UNPAYWALL_EMAIL` | — | 可选；Unpaywall OA PDF 查找 |
| `CORE_API_KEY` | — | 可选；CORE 仓库全文 |
| `CNKI_ENABLED` | `false` | 启用知网浏览器搜索 |
| `CNKI_CDP_URL` | — | Chrome 远程调试 URL |

所有数据**保留在本地**：Zotero 文库、`.chroma_db/`、HuggingFace 模型缓存（`~/.cache/huggingface/`）。

---

## 表格与图

表格和图**不会**被解析成结构化单元格。可靠的表格结构化本质上是一个视觉问题：
基于文本/几何的检测在无框"三线表"上会产出垃圾，甚至把多栏正文、参考文献误判
成几十栏的假表。因此本项目不做这种"伪结构化"，而是保留轻量的**标题锚点记录**：

- **表格** —— 标题（如"表3 …"）、所在页、以及从标题到正文恢复之间的原始内容块
  （让表内**数值**仍可被检索），不含单元格/列结构。
- **图** —— 仅标题（图大致展示了什么），不解码图像。
- 正文里"如表3所示 / 如图2所示"会被链接到对应记录，段落与其引用的图表一起返回
  （`get_paper_content` 的 `referenced_tables` / `referenced_figures`）。

**需要真正的结构化表格？** 可用专门的视觉文档解析器预处理 PDF，把结果
（Markdown/HTML）存为笔记或附件，再作为文本入库。推荐：

| 工具 | 说明 |
|------|------|
| [MinerU](https://github.com/opendatalab/MinerU) | 学术论文、**中日韩**、复杂/嵌套表格的首选；保留表结构、公式转 LaTeX（AGPL-3.0） |
| [Docling](https://github.com/docling-project/docling) | IBM；版面强 + `TableFormer` 表结构模型，导出 Markdown/JSON，原生集成 LangChain/LlamaIndex（MIT） |
| [Marker](https://github.com/datalab-to/marker) | 快速 PDF→Markdown/JSON、可批量（Surya OCR），表格支持良好 |
| [PyMuPDF4LLM](https://github.com/pymupdf/RAG) | 轻量、无需 ML —— **数字版（非扫描）** PDF 最快；与本项目所用的 PyMuPDF 同一引擎 |

前三者基于视觉/ML 模型（更重、更慢），有意不放进默认流程；PyMuPDF4LLM 轻量但仅支持数字版 PDF。

---

## CNKI 配置（可选）

> **知网模块默认关闭。** 仅在需要检索中文期刊论文时使用。当你首次向 AI 请求中文文献（如"搜索知网…"或"检索中文文献"），AI 会提示你完成以下配置。

知网没有公开 API，本项目通过 [Playwright](https://playwright.dev/) 连接你已登录的 Chrome 浏览器（CDP 协议），方案参考 [cookjohn/cnki-skills](https://github.com/cookjohn/cnki-skills)。

### 第 1 步：安装可选依赖

```bash
uv pip install -e ".[cnki]"
playwright install chromium
```

### 第 2 步：启动带远程调试的 Chrome

```bash
# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222

# Windows
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222

# Linux
google-chrome --remote-debugging-port=9222
```

### 第 3 步：登录知网

在该 Chrome 窗口中打开 https://www.cnki.net/ 并登录（通常需要校园网或机构 VPN）。

### 第 4 步：在 `.env` 中启用

```env
CNKI_ENABLED=true
CNKI_CDP_URL=http://127.0.0.1:9222
```

### 第 5 步：重启 MCP 服务端

重新打开对话窗口或重启 MCP 客户端。

### 验证

向 AI 发送：*"在知网搜索 2020 年以来地理探测器的高被引论文"*

如果返回结果（含标题、作者、期刊、引用次数和期刊等级标签），配置成功。

### 工作原理

1. `search_cnki_literature` → 返回含 `export_id` 和 `journal_level` 的结果
2. 选择论文 → AI 调用 `cnki_add_to_zotero(export_ids=[...])` → 论文出现在 Zotero 中
3. 无需 DOI 查找，元数据从知网内部导出 API 获取

### 注意事项

- **触发条件：** 仅在你明确提到中文文献、知网、CNKI、核心期刊、CSSCI 等时调用
- **验证码：** 若出现腾讯滑块验证，在 Chrome 窗口中完成后重试
- **导入 Zotero：** 需要 Zotero 桌面端运行
- **合规性：** 需要合法的机构知网访问权限
- **每次使用前：** 确保 Chrome 窗口在运行且知网登录有效

### 已知问题

> 知网模块当前不够稳定，默认关闭。浏览器自动化本身脆弱。

| 问题 | 原因 | 解决 |
|------|------|------|
| **搜索超时** | 知网加载慢/反爬 | 简化查询，稍后重试 |
| **Chrome 连接拒绝** | 未用 `--remote-debugging-port` 启动 | 关闭所有 Chrome 窗口重新启动 |
| **会话过期** | 知网约 30 分钟超时 | 重新登录 |
| **连续超时** | 频率限制 | 等待 30 秒重试 |
| **导出 Zotero 失败** | Zotero 未运行 | 确保 Zotero 启动且 API 可访问 |

若知网持续失败，可使用英文在线搜索（`search_online_literature` / `find_related_literature`），稳定且无需浏览器自动化。

---

## 升级方式

**pip 用户：**
```bash
pip install --upgrade zotero-research-assistant
```

**源码用户：**
```bash
cd ~/zotero-research-assistant
git pull
uv pip install -e .
```

知网用户：
```bash
uv pip install -e ".[cnki]"
playwright install chromium
```

重启 MCP 客户端。

> **注意：** 若新版本更新了分块策略，`sync_index` 会自动检测版本变更并重建整个索引。

---

## 常见问题

| 问题 | 解决 |
|------|------|
| **连接拒绝/无结果** | 确保 Zotero 桌面端运行且本地 API 已启用 |
| **新论文搜不到** | 说"同步我的索引"或重启 MCP（启动时自动同步） |
| **写操作失败** | 在 `.env` 中设置 `ZOTERO_API_KEY` + `ZOTERO_LIBRARY_ID` |
| **首次启动慢** | 嵌入模型下载约 2.3 GB；设置 `HF_ENDPOINT=https://hf-mirror.com` |
| **Windows 脚本被阻止** | PowerShell 执行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| **MCP 工具未被调用** | 使用支持 function calling 的模型；客户端开启 MCP/工具 |
| **AI 不确认就执行写操作** | 系统提示词加入"执行写操作前必须等待明确确认" |
| **搜索结果差** | 发送"检查系统健康" → `check_health` 诊断问题；发送"显示最近的检索记录" → `recent_retrievals` 查看检索链路 |
| **"为什么没搜到这篇论文？"** | 发送"显示最近的检索" → 获取 trace ID → "回放检索 trace [id]" → `retrieval_trace` 查看完整排序细节 |
| **索引似乎过时** | 发送"检查我的索引" → `inspect_index` 展示版本和质量指标 |
| **CNKI 显示"搜索已禁用"** | 完成 [CNKI 配置](#cnki-配置可选) 步骤 |
| **CNKI 验证码** | 在 Chrome 窗口中完成滑块验证后重试 |

---

## 架构说明

```
research_core/          # 核心库 — Zotero 客户端、RAG 管线、搜索适配器、工具
  parsers/              #   PDF 提取、中英文感知分块、文本清洗器（52 规则）、
                        #   章节检测器（IMRaD）、chunk 质量评分、图表标注检测
  rag/                  #   ChromaDB 索引/存储/检索、SQLite 元数据库、
                        #   嵌入质量诊断、评估框架、检索日志
  tools/                #   35 个工具实现（按领域分文件）
  zotero/               #   Zotero 本地 + Web API 客户端
project_a_mcp/          # MCP 服务端入口（stdio 传输）
scripts/                # CLI 工具（index_library, index_sample, audit_index,
                        #   run_evaluation, generate_eval_queries）
tests/                  # 单元 + 集成测试，60 条黄金评估查询
docs/                   # 详细配置指南（Cherry Studio 中英文）
```

每个工具对应**一个用户意图** — 发现工具返回 `item_key`，读写工具消费它。

---

## 开发指南

```bash
uv pip install -e ".[dev]"
pytest tests/ -v
ruff check .
ruff format .
```

知网集成测试（需活跃知网会话）：
```bash
CNKI_ENABLED=true CNKI_CDP_URL=http://127.0.0.1:9222 pytest tests/mcp/test_cnki.py -v
```

---

## 致谢

本项目受以下项目启发：

- **[zotero-mcp](https://github.com/54yyyu/zotero-mcp)** — 通过 MCP 连接 Zotero 与 AI 助手的先驱工作。
- **[cnki-skills](https://github.com/cookjohn/cnki-skills)** — 优雅的知网浏览器自动化方案。
- **[academic-research-skills](https://github.com/Imbad0202/academic-research-skills)** — 语料优先搜索策略和反幻觉模式的灵感来源。
- **[nature-skills](https://github.com/Yuan1z0825/nature-skills)** — 三索引交叉验证方法的灵感来源。

感谢以上项目作者的开源贡献。

---

## 免责声明

1. **生成质量取决于接入的大语言模型。** 尽管本项目实现了多重防幻觉机制（三索引交叉验证、`[MATERIAL GAP]` 结构化标记、来源可溯），但文献综述、摘要和推荐的最终质量仍取决于你所使用的 AI 模型。请务必在正式引用前核实 AI 生成的文献是否真实存在。AI 有可能且确实会编造参考文献——请将所有输出视为需要人工核实的草稿。

2. **仅供学习交流使用。** 本项目为开源项目，仅用于个人学术研究和学习交流，不作任何商业用途。如本项目的任何内容无意中侵犯了第三方权益，请通过 Issue 告知。

3. **知网模块合规性。** 知网浏览器自动化模块仅为便利性而提供。用户必须拥有合法的机构知网访问权限。该模块默认关闭。

4. **数据隐私。** 默认情况下所有处理均在本地完成。但若配置了云端大语言模型，论文内容将发送至相应外部服务。处理敏感研究的用户请注意。

5. **商标声明。** "Zotero" 是 Corporation for Digital Scholarship 的注册商标。本项目是独立社区工具，与 Zotero 官方无任何关联。

---

## 许可证

MIT
