# Zotero 智能文献助手

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
[![MCP](https://img.shields.io/badge/protocol-MCP-purple.svg)](https://modelcontextprotocol.io/)

**[English](./README.md)** | **[中文](./README_zh.md)**

---

> **将 Zotero 文库变成 AI 可检索的知识库。**
>
> 从 PDF 分块到双语语义检索的完整 RAG 管线，本地建立并检索 Zotero 文库。按语义找论文，而不只是匹配关键词。兼容所有 MCP 协议的 AI 客户端。

---

## 目录

- [RAG 管线](#rag-管线) — 核心
- [快速开始](#快速开始)
- [客户端配置](#客户端配置)
- [MCP 工具一览 (40)](#mcp-工具一览-40)
- [配置项说明](#配置项说明)
- [表格与图](#表格与图)
- [其他功能](#其他功能)
- [升级方式](#升级方式)
- [常见问题](#常见问题)
- [架构说明](#架构说明)
- [致谢](#致谢)
- [许可证](#许可证)

---

## RAG 管线

RAG 管线是本项目的核心。所有设计决策——从分块策略到嵌入后端、从多样性重排序到查询扩展——服务于一个目标：**在消费级硬件上最大化中英文学术论文的检索精度。**

### 管线全景

```
你的 Zotero 文库
      │
      ▼
┌──────────────────────────────────────────────────────┐
│ 1. PDF 提取 (PyMuPDF)                                │
│    逐页文本提取，多线程并行处理                       │
├──────────────────────────────────────────────────────┤
│ 2. 文本清洗 (52 条正则规则)                          │
│    去除期刊 boilerplate：文章信息栏、中图分类号、    │
│    基金信息、页码、DOI 等                             │
│    英文期刊 9 条 · 中文期刊 24 条 · 通用 19 条          │
│    实测平均行移除率 10.6%（CN 19.3%, EN 7.2%）       │
├──────────────────────────────────────────────────────┤
│ 3. 语义分块 (v3.0.0)                                 │
│    段落感知切分 + 中文断句 + 软换行修复                │
│    (满\n意度→满意度)                                  │
│    IMRaD 章节分类 (11 种类型)                         │
│    200 字符最小底线 (参考 FloTorch 2026 基准)         │
│    每块标记：语言 (zh/en/mixed)、完整性标签           │
├──────────────────────────────────────────────────────┤
│ 4. 嵌入 (bge-m3, ONNX INT8)                          │
│    1024 维稠密向量，100+ 语言                         │
│    ONNX Runtime INT8: ~347MB (vs 2.3GB FP32)         │
│    CPU 上 2-3x 加速, 精度损失 <1% R@5                │
│    ONNX 不可用时自动回退到 FP32                        │
├──────────────────────────────────────────────────────┤
│ 5. CHROMADB 索引                                      │
│    HNSW 余弦索引, 64 批量写入                         │
│    元数据含完整性标签 + 语言标记                      │
│    基于 Zotero 版本追踪的增量同步                    │
│    分块策略变更自动触发全量重建                       │
├──────────────────────────────────────────────────────┤
│ 6. SQLite 元数据库                                    │
│    7 张关系表（论文、章节、chunk_meta、图、表         │
│    + 交叉引用）                                       │
│    摘要存入但**不嵌入 ChromaDB**                      │
│    （防止摘要对所有查询都有中等匹配度）                │
└──────────────────────────────────────────────────────┘
      │
      ▼
┌──────────────────────────────────────────────────────┐
│ 7. 混合搜索 + 重排序                                  │
│    BM25 (稀疏) + ChromaDB (稠密)                      │
│    → 双路 RRF 融合 (k=60)                             │
│    → Cross-Encoder 重排序 (ms-marco-MiniLM-L-6-v2)   │
│    → MMR 多样性 (λ=0.4, 每篇最多 3 chunks)           │
│    → 客户端主导的双语检索策略                         │
│      多次中英查询、`expand_query` 术语提示与结果合并 │
│    → 双格式输出: JSON items + Markdown                │
│      context_block (blockquote 引用, ★★★ 分级)        │
└──────────────────────────────────────────────────────┘
```

### 搜索建议

- **`search_papers` 是单查询检索器**——接受中文、英文或混合查询，但服务端不会自动改写或翻译查询。
- **中文/混合查询需要跨语种召回时**，让 MCP 客户端遵循 `bilingual-search` skill：以多个中英文查询调用并按权重合并结果；快速标题或关键词匹配则可直接单次查询。
- **翻译专业术语前使用 `expand_query`**——它仅返回用户保存的中英同义词和匹配的 Zotero 标签，不含内置术语词典。
- **索引期双语富化仍在本地执行且可选**——重建索引时可用 OPUS-MT 翻译中文论文题名和关键词；这与查询改写无关，受 `ZRA_INDEX_BILINGUAL_ENRICHMENT` 控制。

### 管线核心特性

| 特性 | 说明 |
|------|------|
| **文本清洗** | 52 条黑名单正则规则，分块前去除期刊 boilerplate。英文（文章信息栏、页眉）、中文（卷期号、中图分类号、基金）和通用（页码、DOI）全覆盖。零误杀——没有论文正文会包含"〔中图分类号〕" |
| **中文感知分块** | `。！？` 后直接断句（无需空格）。PDF 软换行修复。段落感知合并/拆分，相邻 chunk 间 ~100 字符重叠确保上下文连续。IMRaD 章节检测（英文 "1. Introduction"、中文 "一、引言"）。参考文献段默认排除。 |
| **分栏感知提取** | 双栏/多栏期刊 PDF 按"左栏→右栏、栏内自上而下"读取，修复朴素提取产生的交错乱文。损坏提取——扫描件、乱码（替换符/NUL）、逐词碎片——被质量门控拦截：跳过并在 sync 报告中报告，而非静默入库。不加 OCR。 |
| **Chunk 标记** | 每个 chunk 带语言标记 (zh/en/mixed)、句数、完整性标签 (good/incomplete)。200 字符最小底线防止 <43 token 片段损害端到端准确率。 |
| **ONNX INT8 嵌入** | 默认后端使用 ONNX Runtime 预量化 bge-m3 模型（~347MB vs 2.3GB FP32）。CPU 上 2-3x 加速，体积缩减 4x，检索精度损失 <1%。onnxruntime 不可用时自动回退到 FP32。 |
| **SQLite 元数据库** | 7 张关系表独立于 ChromaDB。摘要存入但不嵌入向量库——防止"摘要匹配一切"问题。零用户配置。 |
| **章节级上下文扩展** | `expand_context=True` 为每个命中 chunk 获取完整章节文本（~2000 字符 vs 300），给 LLM 完整段落语境。邻居扩展（±1 chunk）作为轻量替代。 |
| **混合搜索 + RRF** | BM25 稀疏检索 + ChromaDB 稠密语义搜索，双路 RRF 融合。BM25 保护精确匹配（稀有术语、方法名、变量名），语义提供概念级发现。 |
| **Cross-Encoder 重排序** | 可选 ms-marco-MiniLM-L-6-v2（~80MB）对候选结果精排。查询相关——与静态质量分不同，只在相关时才生效。 |
| **双格式输出** | 核心工具同时返回 `items`（JSON 元数据）和 `context_block`（LLM 优化 Markdown）。blockquote 引用原文、★★★ 相关度分级、句边界截断。Markdown 为 LLM 主消费通道，JSON 服务程序化消费者。 |
| **相关度分级** | 每条结果附带基于 Cross-Encoder 分数百分位的 `relevance_tier`（high/medium/low）。LLM 对 ★★★ 的直觉理解远胜于 0.0321 这类原始浮点数。 |
| **MMR 多样性** | Chunk 级 Maximal Marginal Relevance（λ=0.4，网格搜索调优）。防止单篇论文主导搜索结果。硬 cap 每篇 3 chunks + per-document penalty。多样性提升 54%。 |
| **双语检索策略** | `search_papers` 有意保持单查询检索。对高召回的中文/混合查询，MCP 客户端遵循 `bilingual-search` skill 执行并合并多次中英文查询。服务端通过 `expand_query` 提供用户同义词和 Zotero 标签，但没有预置词典或查询期 NMT。 |
| **索引期双语富化** | 可在本地用 OPUS-MT 翻译中文论文题名和关键词，并以 `[Title_EN]` / `[Keywords_EN]` 写入索引文本。默认开启，可用 `ZRA_INDEX_BILINGUAL_ENRICHMENT` 控制，且与查询改写分离。 |
| **术语管理** | 5 个 MCP 工具：`expand_query`、`add_query_synonym`、`remove_query_synonym`、`list_query_synonyms`、`import_query_dict`。用户可构建并持久化自己的中英术语映射。 |
| **检索可观测性** | 每次搜索输出 JSONL 全链路追踪：查询、策略、候选数、重排序状态、top-20 结果及分数、延迟分解（关键词/语义/重排序/MMR/总计）。字节偏移索引支持快速回溯。 |
| **嵌入质量诊断** | 6 阶段分析：论文内/间相似度、离群 chunk 检测、chunk 长度-相似度 Pearson 相关性、章节类型嵌入分离度、自动问题检测与修复建议。 |
| **系统性评估** | 60 条黄金查询（直接命中/跨文档/无答案三类）。指标：Recall@5/10/20、MRR、NDCG@10。CLI 支持基线保存和 A/B 对比。 |
| **索引审计** | 7 阶段全库质量审计：分页扫描、逐论文评分、覆盖率、噪声检测、嵌入分离度、健康评分、建议。 |

---

## 快速开始

### 1. 安装

```bash
pip install zra-mcp
```

> 默认使用 ONNX INT8 嵌入（~347MB），CPU 上比 FP32 快 2-3 倍，体积小 4 倍。

### 2. 配置 Zotero

启用 Zotero 本地 API：**编辑 → 首选项 → 高级 →** 勾选"允许其他应用通过本地 API 访问 Zotero"。

在工作目录创建 `.env`（最低只读模式）：
```ini
ZOTERO_LOCAL=true
```

需要写操作时，添加 [Zotero API Key](https://www.zotero.org/settings/keys)：
```ini
ZOTERO_LOCAL=true
ZOTERO_LIBRARY_ID=12345678
ZOTERO_API_KEY=your_api_key_here
```

### 3. 连接 AI 客户端

参见[客户端配置](#客户端配置)。MCP 服务端启动时自动增量同步索引。

### 4. 验证

启动 Zotero，打开新对话，问：*"列出我 Zotero 中的所有分组"*

> 首次运行会为所有 PDF 建立向量索引。这是一次性开销——后续启动使用增量同步，秒级完成。

---

## 客户端配置

所有 MCP 客户端使用相同的 stdio 配置。两种写法：

- **pip 安装：** 命令就是 `zra-mcp`
- **源码安装：** 填 Python 完整路径 + `args: ["-m", "project_a_mcp.server"]` + `cwd`

### Cursor

**设置 → MCP → 添加 MCP 服务器**，或 `.cursor/mcp.json`：
```json
{ "mcpServers": { "zra-mcp": { "command": "zra-mcp" } } }
```

### Claude Desktop

编辑 `claude_desktop_config.json`：
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{ "mcpServers": { "zra-mcp": { "command": "zra-mcp" } } }
```

重启后输入区出现锤子图标。需 Pro 或 Team 订阅。

### Cherry Studio

**设置 → MCP 服务器 → 添加 → JSON 模式。** 需额外字段：
```json
{
  "mcpServers": {
    "zra-mcp": {
      "name": "zra-mcp", "type": "stdio", "isActive": true,
      "command": "zra-mcp"
    }
  }
}
```
随后：**设置 → 模型服务**（推荐 Claude/GPT-4o）→ 新建对话 → 开启 MCP 开关。完整教程见 [docs/cherry-studio-setup.md](./docs/cherry-studio-setup.md)。

### Codex CLI

`~/.codex/config.json`：
```json
{ "mcpServers": { "zra-mcp": { "command": "zra-mcp" } } }
```
验证：`codex mcp list`。

> **其他 stdio MCP 客户端** 配置方式完全相同。环境变量从 `<项目>/.env` 读取。

---

## MCP 工具一览 (40)

其中 36 个始终注册；4 个 CNKI 工具仅在 `CNKI_ENABLED=true` 时注册。

| 类别 | 工具 |
|------|------|
| **发现** | `search_papers`, `search_online_literature`, `search_cnki_literature`, `find_related_literature`, `expand_citation_network`, `cnki_paper_detail`, `cnki_navigate_pages`, `find_similar_papers`, `browse_library`, `find_duplicates`, `merge_duplicates` |
| **阅读** | `get_paper`, `get_paper_content`, `search_annotations`, `create_annotation` |
| **写入** | `suggest_citations`, `export_bibliography`, `add_paper`, `cnki_add_to_zotero` |
| **管理** | `add_note`, `edit_tags`, `manage_collections` |
| **洞察** | `reading_status`, `recommend_papers`, `generate_review_note`, `generate_reading_note`, `suggest_tags`, `find_arguments` |
| **管控** | `sync_index`, `check_health`, `inspect_index`, `test_recall`, `recent_retrievals`, `retrieval_trace`, `retrieval_stats`, `expand_query`, `add_query_synonym`, `remove_query_synonym`, `list_query_synonyms`, `import_query_dict` |

<details>
<summary>展开工具详情</summary>

### 发现
- **`search_papers`** — 主搜索。混合关键词+语义。支持 `expand_context`、`expand_neighbors`、`diversity_weight`（MMR，默认 0.4）。返回双格式输出：`items`（JSON 元数据）+ `context_block`（LLM 优化 Markdown，含 blockquote 引用和 ★★★ 相关度分级）。
- **`search_online_literature`** — 在线英文/国际文献（OpenAlex + CrossRef + S2）
- **`search_cnki_literature`** — 知网中文期刊搜索（可选，浏览器自动化）
- **`find_related_literature`** — 5 策略并行：语料优先、关键词、引用网络、S2 推荐、OpenAlex
- **`expand_citation_network`** — OpenAlex 正/反向引用
- **`find_similar_papers`** / **`browse_library`** / **`find_duplicates`** / **`merge_duplicates`** — 文库导航
- **`cnki_paper_detail`** / **`cnki_navigate_pages`** — 知网详情与翻页

### 阅读
- **`get_paper`** — 元数据 + 摘要
- **`get_paper_content`** — 语义查询/页码范围/全文/大纲，可选批注叠加
- **`search_annotations`** — 跨论文搜索高亮/评论
- **`create_annotation`** — PDF 高亮（默认 dry-run）

### 写入与管理
- **`suggest_citations`** — 草稿与文库证据匹配
- **`export_bibliography`** — BibTeX 或格式化引用
- **`add_paper`** — DOI/arXiv/ISBN/BibTeX/URL 导入（默认 dry-run）
- **`add_note`** / **`edit_tags`** / **`manage_collections`** — 文库组织（默认 dry-run）

### 洞察
- **`reading_status`** — 深度阅读/浏览过/未读分类
- **`recommend_papers`** — 个性化推荐（OpenAlex + S2）
- **`generate_review_note`** — 多论文证据提取生成综述
- **`generate_reading_note`** — 单篇结构化笔记
- **`suggest_tags`** — 方法论/领域/数据类型标签（建议制）
- **`find_arguments`** — 按立场分类搜索证据（支持/反对/中立）

### 管控
- **`sync_index`** — 增量同步，启动时自动运行
- **`check_health`** — 连接、索引、嵌入模型、API 诊断
- **`inspect_index`** — chunk 统计、完整性标签、章节分布等
- **`test_recall`** — 单篇论文检索质量测试
- **`recent_retrievals`** / **`retrieval_trace`** / **`retrieval_stats`** — 检索观测
- **`expand_query`** — 在客户端组织下一轮查询前，查询保存的中英同义词与匹配的 Zotero 标签
- **`add_query_synonym`** — 添加中英文同义词对
- **`remove_query_synonym`** — 移除用户自定义同义词对
- **`list_query_synonyms`** — 列出所有用户自定义同义词
- **`import_query_dict`** — 批量导入 CN→[EN...] 映射（JSON 字符串）

</details>

### 双语检索 Skills

高召回场景可选用的多调用加权双语检索与 GraphRAG 图扩展策略以独立 skill 文件发布——`.claude/skills/bilingual-search/SKILL.md` 与 `.claude/skills/graph-expansion/SKILL.md`。它们：

- 可被 Claude Code 等支持 skill 的客户端作为项目 skill 按需加载；
- 由 server 通过 FastMCP 原生 skills provider 作为 MCP resources 暴露
  （`skill://bilingual-search/SKILL.md`、`skill://graph-expansion/SKILL.md`），
  支持 skill 的 MCP 客户端可按需获取。安装后的 wheel 将它们放在 `project_a_mcp/skills`，源码检出使用 `.claude/skills`；可用 `ZRA_SKILLS_DIR` 配置其他目录。

所有 MCP 客户端即使不加载 skill 也能获得策略——`search_papers` 工具描述中保留了紧凑摘要（槽位/权重表 + 合并规则）。

---

## 配置项说明

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ZOTERO_LOCAL` | `true` | 从本地 Zotero API 读取 |
| `ZOTERO_API_KEY` | — | 写操作必需 |
| `ZOTERO_LIBRARY_ID` | `0` | 你的 Zotero 用户 ID |
| `EMBEDDING_BACKEND` | `auto` | `auto`（优先 ONNX INT8）、`onnx_int8`、`sentence_transformers` |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | FP32 后端模型（ONNX INT8 自动使用预量化模型） |
| `EMBEDDING_MAX_SEQ_LEN` | `1024` | 序列长度上限（内存安全） |
| `HF_ENDPOINT` | — | HuggingFace 镜像（国内用 `https://hf-mirror.com`） |
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | 重排序模型（`none` 禁用） |
| `CHROMA_PERSIST_DIR` | `.chroma_db` | 向量数据库路径 |
| `ZRA_CHROMA_MODE` | `server` | ChromaDB 模式：`server`（嵌入式，默认）或 `persistent` |
| `ZRA_CHROMA_HOST` | `127.0.0.1` | ChromaDB 服务器地址 |
| `ZRA_CHROMA_PORT` | `18000` | ChromaDB 服务器端口 |
| `ZRA_AUTO_SYNC` | `true` | 启动时自动增量同步 |
| `ZRA_CLEAN_ENABLED` | `true` | 分块前去除期刊 boilerplate |
| `ZRA_NMT_CACHE_DIR` | `{persist_dir}/hf_cache/` | OPUS-MT 索引期元数据翻译模型缓存目录 (~300MB) |
| `ZRA_SKILLS_DIR` | 已安装包的 `project_a_mcp/skills` | 作为 MCP resources 暴露的策略 skills 目录；源码检出回退到 `.claude/skills` |
| `ZRA_INDEX_BILINGUAL_ENRICHMENT` | `true` | 索引时追加 `[Title_EN]` / `[Keywords_EN]` 提示（仅用于消融实验，关闭后需重建索引） |
| `SEMANTIC_SCHOLAR_API_KEY` | — | 提升在线搜索速率 |
| `OPENALEX_MAILTO` | — | OpenAlex 礼貌池 |
| `UNPAYWALL_EMAIL` | — | Unpaywall OA PDF 查找 |
| `CORE_API_KEY` | — | CORE 仓库全文 |
| `CNKI_ENABLED` | `false` | 启用知网搜索 |
| `CNKI_CDP_URL` | — | Chrome 远程调试 URL |

---

## 表格与图

表格和图作为**标题锚点记录**存储——不解析为结构化单元格。可靠的表格结构化是视觉问题。我们的方案：

- **表格：** 标题 + 标准引用号 + 原始内容块（数值可检索）
- **图：** 仅标题（大致展示内容，不解码图像）
- **交叉引用：** 正文"如表3/如图2"自动链接到对应记录

如需真正的结构化表格，可用 [MinerU](https://github.com/opendatalab/MinerU)、[Docling](https://github.com/docling-project/docling)、[Marker](https://github.com/datalab-to/marker) 或 [PyMuPDF4LLM](https://github.com/pymupdf/RAG) 预处理 PDF。

---

## 其他功能

### 在线文献发现
- 多源并行检索（OpenAlex + CrossRef + S2）
- 语料优先引用网络扩展
- 三索引交叉验证——无法验证的论文自动过滤
- 反幻觉：搜索零结果时生成 `[MATERIAL GAP]` 标签

### CNKI（中文文献）
- 可选浏览器自动化（Chrome DevTools Protocol）
- 期刊等级标签（CSSCI、北大核心、CSCD、SCI、EI）
- 直接导入 Zotero，无需 DOI 查找

### 阅读与写作
- 阅读状态检测（深度阅读/浏览过/未读）
- 基于阅读行为的个性化推荐
- 文献综述生成器（附页码引用）
- 论据发现器：按立场分类（支持/反对/中立）
- 智能标签建议（方法论/领域/数据类型，建议制）

### 文库管理
- 通过 DOI、arXiv、ISBN、BibTeX 或出版商 URL 添加论文
- OA PDF 瀑布流：arXiv → Unpaywall → OpenAlex → S2 → CORE → PMC
- 重复检测与合并（dry-run 预览）
- 所有写操作需明确确认

---

## 升级方式

```bash
pip install --upgrade zra-mcp
```

> 若新版本更新了分块策略，`sync_index` 自动检测版本变更并重建索引。

---

## 常见问题

| 问题 | 解决 |
|------|------|
| **连接拒绝/无结果** | 确保 Zotero 桌面端运行且本地 API 已启用 |
| **新论文搜不到** | 说"同步我的索引"或重启 MCP（启动时自动同步） |
| **写操作失败** | 在 `.env` 中设置 `ZOTERO_API_KEY` + `ZOTERO_LIBRARY_ID` |
| **首次启动慢** | 首次建索引会下载 ONNX INT8 模型（~347MB）。国内用 `HF_ENDPOINT=https://hf-mirror.com` |
| **搜索结果差** | 说"检查系统健康"→ `check_health`；"显示最近的检索记录"→ `recent_retrievals` |
| **"为什么没搜到这篇论文？"** | "显示最近的检索"→ 获取 trace ID → "回放检索 trace [id]" → `retrieval_trace` |
| **索引似乎过时** | "检查我的索引"→ `inspect_index` |
| **Windows 脚本被阻止** | PowerShell 执行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| **MCP 工具未被调用** | 使用支持 function calling 的模型；客户端开启 MCP/工具 |

---

## 架构说明

```
research_core/
  parsers/     — PDF 提取、文本清洗器 (52 规则)、中文感知分块器、
                IMRaD 章节检测、chunk 标记
  rag/         — ChromaDB 存储+检索、ONNX INT8 + FP32 嵌入、
                SQLite 元数据库、Cross-Encoder + MMR 重排序、
                索引期双语富化、评估、检索日志、嵌入诊断
  tools/       — 40 个 MCP 工具适配层（36 常驻 + 4 个 CNKI 条件注册）
  zotero/      — Zotero 本地 + Web API 客户端
project_a_mcp/ — MCP 服务端入口 (stdio 传输)
scripts/       — CLI 工具（索引、审计、评估、基准测试、发布）
tests/         — pytest 套件 + 60 条黄金评估查询
docs/          — 配置指南（Cherry Studio 中英文）、开发日志
```

---

## 致谢

灵感来自 [zotero-mcp](https://github.com/54yyyu/zotero-mcp)、[cnki-skills](https://github.com/cookjohn/cnki-skills)、[academic-research-skills](https://github.com/Imbad0202/academic-research-skills)、[nature-skills](https://github.com/Yuan1z0825/nature-skills)。

---

## 许可证

MIT
