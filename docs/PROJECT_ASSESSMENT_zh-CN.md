# Zotero Research Assistant 仓库评估

> 评估时间：2026-09-03
> 本地仓库：`/Users/qiobn/Desktop/zotero-research-assistant`
> 审计快照：初始检出 `main` / `8bebcc2b072ea243d43c7decd49fcf08ba4f7d15`
> 后续开发基线：`feat/lightweight-graphrag`；本文第七至第十节的风险结论应以审计快照理解。
> 证据范围：仓库文档、源码、测试、包清单、Git 分支/标签/历史，以及远端开发分支中已提交的第一方材料。未用第三方评测或二手介绍。

## 一、结论先行

这是一个面向个人 Zotero 文库的独立 MCP 服务，核心不是“自动写论文”，而是把本地 PDF 建成中英文学术检索库，再向 MCP 客户端提供发现、阅读、引用、文库管理和证据整理工具。项目已经有完整的纵向链路：Zotero 读取、PDF 提取与清洗、分块、Dense/BM25 检索、重排序、上下文渲染、写操作预览与确认；包元数据仍明确标为 **Beta**。（证据：`README_zh.md:11-14,34-90`；`pyproject.toml:1-18`）

当前最重要的判断不是“还能加什么功能”，而是“从哪条线继续开发”：检出的 `main` 是 0.4.8，最后提交为 2026-07-17；`origin/feat/lightweight-graphrag` 无分叉地线性领先 12 个提交，最后提交为 2026-08-12，已经推进到 0.4.9，并加入评估强化、策略外置、列感知 PDF 提取和提取质量门禁。其余三个远端功能分支都已被 `main` 完全包含。（证据：`git rev-list --left-right --count main...origin/feat/lightweight-graphrag` 输出 `0 12`；`git branch -r --contains main` 同时列出 `origin/main` 与该功能分支；`git log -1 --format='%H %aI %s' main` 输出 `8bebcc2... 2026-07-17...`；同命令对开发分支输出 `3ebc8f9... 2026-08-12...`；对另外三分支的左右计数分别为 `40 0`、`55 0`、`59 0`）

不建议立刻把该活跃分支合入 `main`：它把个人文库查询、Zotero item key、研究主题、评估结果以及本机绝对路径提交到了远端 Git 历史；而且其“把策略 skills 作为 MCP resources 随包提供”的实现看起来没有把仓库根目录的 `.claude/skills` 纳入 wheel。应先完成隐私清理、打包验证和测试契约修复，再把它作为后续开发基线。（证据：`origin/feat/lightweight-graphrag:tests/eval_queries_user.json:1-147`；`origin/feat/lightweight-graphrag:tests/eval_results/strategy_strategy_variants_7call_gpt-5-4-mini_pool50.json:464`；`origin/feat/lightweight-graphrag:project_a_mcp/server.py:301-326`；`origin/feat/lightweight-graphrag:pyproject.toml:55-60`）

工程成熟度的主要短板是自动化验证：`main` 有 91 个 `test_*` 方法，但 CI 排除了 67 个 MCP 集成/场景测试，又整文件忽略唯一的 21 项 unit 测试，因此每个 Python 版本实际上只运行 `tests/core/test_chunker.py` 的 3 项测试；现有 MCP 测试还断言旧的列表返回格式，而当前服务器返回 `items + context_block` 字典。功能分支新增 6 个 PDF 测试，使 core + unit 的源码测试数达到 30，但按现有忽略规则实际可进入 CI 的仍只有 9 个，而且该分支没有任何 Actions run。（证据：`.github/workflows/ci.yml:13-40`；`tests/core/test_chunker.py:7-30`；`tests/mcp/test_tools.py:31-41`；`project_a_mcp/server.py:629-675`；主分支测试数量按目录统计为 core 3、unit 21、mcp 67；功能分支为 core 9、unit 21、mcp 67；[GitHub Actions API](https://api.github.com/repos/qiobn/zotero-research-assistant/actions/runs?branch=feat%2Flightweight-graphrag&per_page=5) 返回 `total_count: 0`）

## 二、产品意图与适用对象

项目定位是“把 Zotero 文库变成 AI 可检索的知识库”，重点在消费级硬件上的中英文学术 PDF 检索；LLM 由 Cursor、Claude Desktop、Cherry Studio、Codex 等外部 MCP 客户端提供，服务自身不需要 OpenAI/Anthropic key。（证据：`README_zh.md:11-14,34-36,162-211`；`.env.example:1-13`）

它适合以下工作：

- 在个人 Zotero 文库中按主题、正文术语、年份、标签或集合检索论文，并返回可编程 JSON 与供 LLM 阅读的 Markdown 上下文。（证据：`project_a_mcp/server.py:567-675`）
- 从单篇论文读取元数据、PDF 片段、页码、全文、目录和批注，并解析正文对表/图标题锚点的引用。（证据：`research_core/tools/read.py:16-32,35-64,67-137,128-229`）
- 搜索 OpenAlex、Crossref、Semantic Scholar，扩展正反向引用和 related works；可选通过浏览器自动化接入 CNKI。（证据：`research_core/tools/discover_online.py:182-223,242-270`；`research_core/tools/find_related.py:564-588,609-681`；`research_core/tools/citation_network.py:20-44,74-142`；`research_core/sources/cnki/browser.py:31-75`）
- 为草稿找候选引用、导出 BibTeX/朴素引用、生成阅读笔记或综述所需的证据包、按启发式给论据标注立场。（证据：`research_core/tools/cite.py:33-113`；`research_core/tools/review.py:76-172`；`research_core/tools/reading_note.py:24-119`；`research_core/tools/arguments.py:55-109`）
- 在用户确认后写入 Zotero：加论文/附件、笔记、标签、集合、批注，以及合并重复条目。（证据：`project_a_mcp/server.py:273-278,314-316`；`research_core/tools/manage.py:37-68,74-124,694-819`；`research_core/zotero/client.py:517-613,679-714,726-813`）

它不应被理解为自治研究代理或事实生成器。服务端的综述/阅读笔记工具主要返回检索证据、模板和写作指令，真正的综合与成文由 MCP 客户端中的 LLM 完成；立场分类和标签推荐也主要是关键词启发式。（证据：`research_core/tools/review.py:85-103,145-172`；`research_core/tools/reading_note.py:32-48,96-119`；`research_core/tools/arguments.py:17-83`；`research_core/tools/suggest_tags.py:15-77,101-172`）

## 三、架构与运行链路

### 3.1 模块边界

| 层 | 职责 | 主要入口 |
|---|---|---|
| MCP 传输层 | FastMCP stdio 服务、工具注册、错误封装、响应裁剪、生命周期 | `project_a_mcp/server.py` |
| 业务工具层 | 搜索、阅读、引用、管理、洞察、诊断 | `research_core/tools/` |
| RAG 层 | ChromaDB、BM25、SQLite、嵌入、重排、评估、日志 | `research_core/rag/` |
| 解析层 | PyMuPDF 提取、清洗、分块、章节/表图锚点 | `research_core/parsers/` |
| 数据源层 | OpenAlex、Crossref、S2、CNKI 与共享 HTTP 策略 | `research_core/sources/` |
| Zotero 适配层 | 本地只读、Web API 写入、附件路径、批注与集合 | `research_core/zotero/` |

以上目录分层也是仓库公开架构说明；CLI 入口为 `zra-mcp = project_a_mcp.server:main`，wheel 只声明两个 Python package：`research_core` 和 `project_a_mcp`。（证据：`README_zh.md:367-381`；`pyproject.toml:52-60`）

### 3.2 索引链路

1. 默认通过 Zotero 本地 API `127.0.0.1:23119` 读取；当同时提供 library id 与 API key 时切到“本地读 + Web API 写”的混合模式。（证据：`research_core/zotero/client.py:111-174`）
2. 增量同步用 Zotero item version 区分新增、修改、删除；分块版本或嵌入模型变化会触发全量重建。（证据：`research_core/tools/admin.py:299-353`；`research_core/rag/sync_state.py`）
3. 只索引带本地 PDF 路径的条目；无 PDF 的条目被标记为 skipped，无可提取文本的 PDF 进入 failed。（证据：`research_core/tools/admin.py:361-375,409-428`）
4. `main` 用 PyMuPDF 的 `page.get_text("text")` 逐页提取；之后应用清洗、中文感知断句、软换行修复、章节检测、固定尺度分块以及表图标题锚点。（证据：`research_core/parsers/pdf.py:33-51`；`research_core/parsers/chunker.py:1-23,33-51`；`research_core/tools/admin.py:34-76`）
5. chunk 会加上标题、年份、关键词、章节，以及中文元数据的英文翻译，再写入 ChromaDB；论文/章节/chunk/图/表及交叉引用另写入 SQLite。（证据：`research_core/rag/indexer.py:108-159,179-207`；`research_core/rag/database.py:1-14,188-266`；`research_core/tools/admin.py:79-279`）
6. 同步后从 ChromaDB 文本重建一个内存 BM25Okapi 索引并用 pickle 持久化；中文分词是 CJK 单字 + bigram，英文是至少两个字母的单词。（证据：`research_core/rag/bm25_index.py:1-15,37-59,107-171,225-266`）

### 3.3 查询链路

`main` 的 `search_papers` 同时做 Zotero 元数据关键词检索、ChromaDB Dense 检索和 BM25 正文检索；中文/混合查询还会走内置词典、Zotero tags、用户词表和 OPUS-MT CN→EN 扩展。Dense 候选经 Cross-Encoder 与 MMR 后，Dense 与 BM25 在论文级做双路 RRF；如果没有命中，回退到 Zotero 关键词搜索。（证据：`research_core/tools/search.py:36-65,88-177,239-261,323-360`；`research_core/rag/query_rewriter.py:1-18,281-357`）

每次本地检索默认写 JSONL trace，包括原始/扩展查询、参数、候选数、重排模型、top-20 的 item key/标题/分数和延迟；启动时会删除 90 天前的日志。（证据：`research_core/rag/logger.py:1-18,35-83,86-129,259-320`；`project_a_mcp/server.py:262-270`）

服务启动时会先起本地 ChromaDB 子进程，同步预载 Cross-Encoder 与 NMT 模型，然后后台增量同步索引；自动同步可由 `ZRA_AUTO_SYNC=false` 关闭。（证据：`project_a_mcp/server.py:140-213`；`.env.example:46-69`）

## 四、已实现能力清单

### 4.1 工具数量与注册状态

`main` 源码实际注册 **39 个工具：35 个常驻，4 个仅在 `CNKI_ENABLED=true` 时注册**。功能分支和已发布的 PyPI 0.4.9 则因新增 `expand_query`，实际为 **40 个：36 个常驻 + 4 个 CNKI**。这些数字由 Python AST 对 `@mcp.tool()` / `@_cnki_tool()` 统计得到；`main` 服务器文件头写 39，但两条代码线的 README/CLAUDE 和 0.4.9 包描述仍写 36，功能分支的 CNKI helper docstring 甚至残留 “32 降到 28”，都属于文档漂移。（证据：`project_a_mcp/server.py:1-22,321-336`；`origin/feat/lightweight-graphrag:project_a_mcp/server.py:1-22,328-335,1996-2017`；`pyproject.toml:1-5`；`README_zh.md:215-224`；`CLAUDE.md:3-23`；PyPI 0.4.9 wheel 内 `project_a_mcp/server.py`）

| 类别 | 真实能力 | 边界 |
|---|---|---|
| 本地发现 | 混合搜索、过滤模式、相似论文、文库浏览、重复检测 | 依赖已运行的 Zotero；正文能力依赖本地 PDF 和已建索引。（证据：`project_a_mcp/server.py:567-675,1007-1128`） |
| 在线发现 | OpenAlex + Crossref + S2 并发搜索；引用图和 related works | 查询与 DOI 会发送到第三方 API；结果验证是 fail-open。（证据：`research_core/tools/discover_online.py:182-223,242-270`；`research_core/sources/verify.py:1-14,78-121`） |
| CNKI | 检索、详情、翻页、导入 Zotero | 默认关闭；需 Playwright、登录态/Chrome CDP，验证码需要人工处理。（证据：`research_core/sources/cnki/browser.py:15-75`；`research_core/sources/cnki/search.py:52-77,140-190`；`research_core/tools/cnki_zotero.py:30-67`） |
| 阅读 | 元数据、片段、页码、全文、目录、批注、表图交叉引用 | 全文最多 50 页；图仅标题，表不结构化。（证据：`research_core/tools/read.py:35-125,128-229`） |
| 引用/写作 | 草稿匹配引用、BibTeX、综述证据包、阅读笔记、论据发现 | 不负责最终写作或引用正确性终审；非 BibTeX 格式会退化成简单文本。（证据：`research_core/tools/cite.py:46-130`；`research_core/tools/review.py:76-172`） |
| 管理 | 增、改、集合、批注、重复合并 | 通用写操作需要 Zotero Web API 权限和显式确认；重复项被移入回收站而非永久删除。（证据：`research_core/zotero/client.py:158-174,726-813`；`research_core/tools/search.py:575-630`） |
| 诊断 | health、索引审计、单篇 recall、自定义词表、检索 trace/stats | 单篇 `test_recall` 是“用论文标题找回自身 chunk”的启发式，不等于端到端检索质量。（证据：`research_core/tools/inspect_index.py:220-292`；`project_a_mcp/server.py:1861-2146`） |

### 4.2 防误写与反幻觉设计

通用写操作默认 `confirm=false`，服务器提示明确要求预览后停止并取得用户下一轮显式同意；管理层代码也实际检查 `confirm` 和 `zot.can_write`，不是纯 prompt 约束。（证据：`project_a_mcp/server.py:273-278,314-316`；`research_core/tools/manage.py:37-68,74-124,130-207,694-772`）

在线发现要求结果带 `source_url` / `item_key` 等锚点，并在无材料时输出 `[MATERIAL GAP]`；但“三索引验证”允许无 DOI 的结果直接通过，网络错误也 fail-open，所以它只能减少明显错误，不能证明结果已被三方确认。（证据：`project_a_mcp/server.py:307-313`；`research_core/sources/verify.py:78-121,155-163`）

## 五、明确与隐含的能力边界

### 5.1 PDF 与文档格式

- `main` **没有 OCR**；扫描件会得到空页。加密、字体映射异常、多栏阅读顺序错乱也可能造成无文本或乱码。（证据：`research_core/parsers/pdf.py:38-50`；`research_core/tools/admin.py:421-427`）
- `main` 尚未做列感知读取，直接使用 PyMuPDF 的纯文本顺序；活跃分支才加入列聚类和 scanned/garbled/fragmented 质量门禁，而且仍明确不做 OCR。（证据：`research_core/parsers/pdf.py:38-50`；`origin/feat/lightweight-graphrag:research_core/parsers/pdf.py:11-27,64-99,161-180`）
- 表格只保存标题和粗略原始文本块，不提取单元格；图只保存标题，不看图像像素。（证据：`README_zh.md:301-309`；`research_core/tools/read.py:67-125`）
- `get_paper_content(mode="fulltext")` 硬上限为 50 页，服务器还会在 80,000 字符以上裁剪长字段，必要时把列表降到 15 项。（证据：`research_core/tools/read.py:49-64,140-166`；`project_a_mcp/server.py:339-432`）
- 默认 Dense 搜索排除参考文献章节，只有显式 `include_references=True` 才会检索参考文献。（证据：`research_core/rag/retriever.py:100-140,713-723`）

### 5.2 规模与覆盖范围

- 批注跨库搜索默认最多扫描 300 篇论文，因此大库结果可能不完整；`.env.example` 却写默认 500，文档与实现不一致。（证据：`research_core/zotero/client.py:359-429`；`.env.example:109-110`）
- 重复检测默认最多取 500 个条目；阅读状态无指定 item keys 时最多取 200；推荐算法只从最近修改的 100 篇中选种子，并只用前 200 个库内条目排除“已经收藏”的推荐。（证据：`research_core/zotero/client.py:634-668`；`research_core/tools/reading_status.py:79-93`；`research_core/tools/recommend.py:23-90,185-199`）
- 当前公开评估只对应一个约 250 篇、19,788 chunk 的个人文库和 20 条用户查询，且开发日志承认 LLM judge 对 no-answer 过于宽松，所以绝对 Recall 数值不能外推到其他学科、大型文库或法学/公式密集材料。（证据：`origin/feat/lightweight-graphrag:tests/eval_results/RECALL_EVALUATION_LOG.md:6-12,195-219,235-256`）

### 5.3 “智能”能力的边界

- `reading_status` 把至少 3 个批注或至少 1 个 note 视为 deep_read，把有批注或最近修改过 PDF 视为 browsed；它不是实际阅读时长追踪。（证据：`research_core/tools/reading_status.py:40-51,104-150`）
- `recommend_papers` 的个性化来自批注数、笔记数、近期修改和 tags，候选由 OpenAlex/S2 返回，不是从全文训练出的用户画像。（证据：`research_core/tools/recommend.py:23-120,123-145,235-250`）
- `generate_review_note`、`generate_reading_note` 提供证据与 prompt，不直接完成可靠的学术综合；`find_arguments` 依据支持/反对词表先验分类，明确把最终判断交给 AI 客户端。（证据：`research_core/tools/review.py:85-103,145-172`；`research_core/tools/reading_note.py:32-48,96-119`；`research_core/tools/arguments.py:17-83`）
- `export_bibliography` 真正专门支持的是 BibTeX；其他 `fmt` 统一退化成 `authors (year). title. doi:`，不是 CSL 风格引擎。（证据：`research_core/tools/cite.py:104-130`）

## 六、安装、依赖与运行条件

最低运行版本是 Python 3.11；包用 Hatchling 构建，控制台命令是 `zra-mcp`。基础依赖包括 PyZotero、ChromaDB、sentence-transformers、PyMuPDF、FastMCP、httpx、dotenv、loguru、Pydantic、onnxruntime 和 rank-bm25；CNKI 是带 Playwright 的可选 extra。（证据：`pyproject.toml:1-45,52-60`）

“默认 ONNX”不等于轻量安装：`sentence-transformers>=3.0` 是不可选的基础依赖，而其当前 PyPI 元数据要求 `torch>=2.2`。因此常规 `pip install zra-mcp` 仍会解析并安装 PyTorch，即使实际嵌入路径使用 ONNX；ONNX 主要减少模型运行时和模型文件体积，没有消除安装包/环境中的 PyTorch 占用。（证据：`pyproject.toml:22-33`；[sentence-transformers 官方 PyPI JSON](https://pypi.org/pypi/sentence-transformers/json) 的当前 `requires_dist` 包含 `torch>=2.2`）

最低只读模式只需 Zotero Desktop 正在运行且已开启本地 API；写入模式还要配置 `ZOTERO_LIBRARY_ID` 与 `ZOTERO_API_KEY`。本地读不携带 key，写操作通过 Web API。（证据：`README_zh.md:124-158`；`.env.example:1-13`；`research_core/zotero/client.py:126-174`）

首次运行需要下载约 347MB 的社区量化 ONNX 模型；失败时回退到 FP32 sentence-transformers。默认还下载 Cross-Encoder 和约 300MB 的 OPUS-MT；服务启动会同步预载模型，因此冷启动和首次索引明显依赖 Hugging Face 可用性、磁盘与内存。（证据：`research_core/rag/embedding.py:1-12,23-24,104-152,197-233`；`project_a_mcp/server.py:152-213`；`.env.example:15-32,40-44,61-65`）

默认 ChromaDB 以子进程方式绑定 `127.0.0.1:18000`，避免 Windows 跨进程 HNSW 问题；也可用 PersistentClient。若端口上已有健康 Chroma 服务，代码会直接复用它。（证据：`research_core/rag/chroma_server.py:38-70,73-152`；`research_core/rag/store.py:45-74`）

## 七、开发与发布状态

### 7.1 当前分支态势

`main` 共 144 个提交，工作树干净并与 `origin/main` 对齐；全分支历史由同一作者的两个邮箱身份贡献（97 + 59），因此维护 bus factor 实质为 1。（证据：`git rev-list --count HEAD` 输出 144；`git status --short --branch` 输出 `## main...origin/main`；`git shortlog -sne --all` 输出 `97 qiobn <...>` 与 `59 qiobn <...>`）

活跃分支相对 `main` 有 41 个文件变化，约 `+47,951/-844` 行；大部分新增行来自提交的评估结果 JSON，而不全是源代码。该分支 12 个提交涵盖：移除服务端内置 NMT/词典、让外部 LLM 执行 5-7 次双语查询、增加 GraphRAG 策略 skill、暴露 skill resources、完善 recall/消融评估、修复发布脚本、增加列感知 PDF 提取。（证据：`git diff --stat main...origin/feat/lightweight-graphrag`；`git log --oneline main..origin/feat/lightweight-graphrag`）

这个方向有实验证据但尚未定型：同一 20 查询集合中，7-call 策略相对单次基线把 Recall@10 从 40.8% 提到 56.3%，cross-document 从 15.9% 提到 55.2%；与此同时 direct 和 method 类别分别下降 4.4 和 10.2 个百分点，no-answer judge 仍不可信。（证据：`origin/feat/lightweight-graphrag:tests/eval_results/RECALL_EVALUATION_LOG.md:195-256`）

### 7.2 版本和发布一致性

- PyPI 最新版是 **0.4.9**，上传于 2026-08-10；但 `main` 的 `pyproject.toml` 仍是 0.4.8，GitHub 最新 release/tag 只有 `v0.3.1`，而 `research_core.__version__` 在 `main`、功能分支和 0.4.9 wheel 中都仍是 0.3.1。外部用户、源码开发者和运行时版本检查会看到三套不同版本。（证据：[zra-mcp 官方 PyPI JSON](https://pypi.org/pypi/zra-mcp/json) 返回 latest `0.4.9`、wheel 上传时间 `2026-08-10T16:21:35Z`；`pyproject.toml:1-4`；`git tag --sort=-creatordate` 输出 `v0.3.1, v0.3.0, v0.2.0`；`research_core/__init__.py:1-3`；`origin/feat/lightweight-graphrag:research_core/__init__.py:1-3`）
- `CHANGELOG.md` 最新只到 0.4.5，且有两个 0.3.0 段；`DEVELOPMENT_PLAN.md` 仍称当前版本 `v0.4.0-dev`，进度总表和下方任务状态也已过期。（证据：`CHANGELOG.md:8-20,94-158`；`DEVELOPMENT_PLAN.md:1-15,130-147`）
- README 写 36 工具，`main` 实际是 39，0.4.9 实际是 40；`CLAUDE.md` 的包名仍写 `zotero-research-assistant`，实际 PyPI 包名是 `zra-mcp`。（证据：本报告 4.1；`README_zh.md:215-224`；`project_a_mcp/server.py:1-22`；`pyproject.toml:1-5`；`CLAUDE.md:3-11`）
- 发布脚本依赖 `build` 和 `twine`，但 dev extra 没有声明二者；脚本最后打印的 PyPI 项目 URL 仍是旧名 `zotero-research-assistant`。仓库没有自动发布 workflow。（证据：`scripts/publish.sh:55-62,82-90`；`pyproject.toml:36-42`；`.github/workflows/ci.yml:1-40`）

更严重的是，PyPI 0.4.9 artifact 自相矛盾：wheel 中的 `query_rewriter.py` 明确写着 “No preset dictionaries. No NMT”，且归档内没有 `research_core/rag/query_dict.json`；但同一个 wheel 的 METADATA 内嵌 README 仍宣称“四层双语扩展”，包括 300+ 词典与 OPUS-MT。按 README 配置的用户实际安装后得到的是外部 LLM 主导的轻量策略，而不是文档描述的服务端自动翻译链路。（证据：0.4.9 wheel 的 `research_core/rag/query_rewriter.py:1-15,137-169`；wheel `unzip -l` 的 72 项文件清单无 `query_dict.json`；wheel `zra_mcp-0.4.9.dist-info/METADATA` 内 README 的 `Bilingual Query Expansion` 段；[PyPI wheel 下载地址](https://files.pythonhosted.org/packages/d8/34/8f7549a557e15bdfedff7054886d743bed1889dc841765040ceb1b9ce393/zra_mcp-0.4.9-py3-none-any.whl)）

### 7.3 测试、质量门和可复现性

CI 在 Ubuntu 上只测 Python 3.11/3.12，而 package classifier 声称支持 3.11-3.14；lint 只启用 E/W/F 且只检查两个源码目录，没有执行 `pyproject.toml` 已配置的 I/UP/B，也不检查 scripts/tests。（证据：`.github/workflows/ci.yml:13-40`；`pyproject.toml:10-18,62-76`）

仓库没有类型检查、覆盖率阈值、构建/安装 smoke test、依赖漏洞扫描或 CodeQL workflow；唯一 CI 文件只包含 checkout、安装、ruff 与精简 pytest。workflow 只在 push 到 `main` 或 pull request 时触发，因此直接推送到功能分支不会运行；官方 Actions API 对活跃分支返回 0 次运行。（证据：`.github/workflows/ci.yml:1-40`；`git ls-files .github` 只返回 `.github/workflows/ci.yml`；[GitHub Actions API](https://api.github.com/repos/qiobn/zotero-research-assistant/actions/runs?branch=feat%2Flightweight-graphrag&per_page=5)）

所有运行依赖只有下限没有上限，`uv.lock` 还被明确忽略，因此同一提交在不同时间安装可能得到不同依赖组合。源码直接使用 `requests`、`numpy`、`torch`、`transformers`、`huggingface_hub`，但没有直接声明它们，而是依赖传递依赖。（证据：`pyproject.toml:22-45`；`.gitignore:39-41`；`research_core/zotero/client.py:143-156`；`research_core/rag/embedding.py:42-80,123-150,160-187`；`research_core/rag/query_rewriter.py:202-215`）

本次环境没有安装 `pytest` 或 `ruff`，因此无法执行仓库测试/lint；已用 Python 标准库解析全部 85 个 Python 文件 AST 和 `pyproject.toml`，并运行 `compileall`，均通过，`git fsck --full --no-dangling` 也通过，仓库仍为 clean。以上只能证明语法/TOML/Git 对象有效，不证明运行行为正确。（证据：本次命令 `pytest ...` 与 `ruff ...` 均返回 command not found；`python3 -m compileall -q ...` 返回 0；AST/TOML 脚本输出 `AST/TOML OK`；`git status --short --branch` 输出 `## main...origin/main`）

## 八、代码质量与技术风险

### P0：远端开发分支含个人研究数据

开发分支提交了可识别用户研究范围的查询、论文标题、Zotero item key 和人工备注；策略评估结果还提交了完整检索池及本机绝对路径。这些内容已经进入远端历史，简单再提交一次删除并不能从历史中移除。（证据：`origin/feat/lightweight-graphrag:tests/eval_queries_user.json:1-147`；`origin/feat/lightweight-graphrag:tests/eval_results/strategy_strategy_variants_7call_gpt-5-4-mini_pool50.json:1-45,464`；该分支 `.gitignore:38-39` 只忽略 `tests/eval_baseline.json`）

建议先把原始结果移到私有/本地评估数据集，仓库只保留脱敏 fixture 和聚合指标；若远端仓库可能公开或被他人克隆，应评估历史重写与密钥/身份影响，而不是只删 HEAD 文件。

### P0：开发分支的评估会把文库元数据发给外部 LLM

该分支的 judge prompt 包含 query、item key、标题、tags 和摘要前 200 字，然后 POST 到可配置的 OpenAI-compatible `/v1/chat/completions`；`evaluate_recall` 默认 `include_abstracts=True`。这是评估路径，不是常规本地检索，但必须改为显式 opt-in，并提供脱敏/本地 judge 模式。（证据：`origin/feat/lightweight-graphrag:research_core/rag/eval_judge.py:165-175,202-217`；`origin/feat/lightweight-graphrag:research_core/rag/recall_eval.py:204-225`）

### P1：测试契约已落后于实现

`tests/mcp/test_tools.py` 仍断言 `search_papers` 返回 list，当前服务器明确返回包含 `count/query/items/context_block` 的 dict；即使准备好 Zotero 与索引，这组测试也需要先更新契约才有验证价值。（证据：`tests/mcp/test_tools.py:31-41`；`project_a_mcp/server.py:629-675`）

### P1：MMR 参数语义与公式相反

文档把 `diversity_weight` 描述为 `0=纯相关，1=纯多样`，但函数先把 `<=0` 视为完全禁用，实际公式是 `lambda * relevance - (1-lambda) * similarity`，所以数值越大越偏相关，越小才越重多样性。这个命名/文档错误会让 API 调参方向相反，也使“默认 0.4 经网格搜索调优”的解释难以审计。（证据：`research_core/rag/retriever.py:595-628,644-705`；`research_core/tools/search.py:46-62`）

### P1：NMT 生命周期存在全局副作用与重复内存

查询翻译加载时用 `socket.setdefaulttimeout(10)` 改进程全局默认值；如果原默认值是常见的 `None`，`finally` 不会恢复，后续所有 socket 都继承 10 秒默认超时。若 `transformers` 导入在 `default_timeout` 赋值前失败，`finally` 还可能引用未赋值局部变量。（证据：`research_core/rag/query_rewriter.py:192-224`）

查询扩展与索引富化分别维护各自的 `_nmt_pipeline`，启动预载的是 query rewriter 的模型，而后台索引会再在 `indexer.py` 中加载一套 Marian tokenizer/model；这可能重复占用约 300MB 级内存并增加冷启动复杂度。（证据：`project_a_mcp/server.py:175-213`；`research_core/rag/query_rewriter.py:172-224`；`research_core/rag/indexer.py:17-55,63-102`）

默认缓存路径判断也存在跨平台错误：代码只在 `persist_dir` 不是字面值 `.chroma_db` 时才从其父目录构造缓存，否则固定使用 Windows 字符串 `D:\\tmp\\zra_nmt_cache`。在 macOS/Linux 上反斜杠不是目录分隔符，该值会被当作当前工作目录下的相对目录名，查询扩展和索引模块都会在错误位置创建缓存；应统一改用 `pathlib`、平台缓存目录或显式配置。（证据：`research_core/rag/query_rewriter.py:192-199`；`research_core/rag/indexer.py:36-44`）

### P1：0.4.9 的 skill 打包链路已确认不完整

开发分支和 PyPI wheel 中的服务器从 `<project_root>/.claude/skills` 提供 MCP resources，但构建配置只包含 `research_core` 与 `project_a_mcp`，两个 skill 文件位于 package 之外。对 PyPI 0.4.9 wheel 的实际文件清单检查已经确认 `.claude/skills` 完全缺失，因此标准 `pip install zra-mcp` 下 provider 会因目录不存在而静默跳过；策略从服务器 docstring 外置后，安装用户反而拿不到其宣称的策略资源。（证据：`origin/feat/lightweight-graphrag:project_a_mcp/server.py:301-326`；`origin/feat/lightweight-graphrag:pyproject.toml:55-60`；`git ls-tree` 显示源码资源位于 `.claude/skills/bilingual-search/SKILL.md` 和 `.claude/skills/graph-expansion/SKILL.md`；[PyPI 0.4.9 wheel](https://files.pythonhosted.org/packages/d8/34/8f7549a557e15bdfedff7054886d743bed1889dc841765040ceb1b9ce393/zra_mcp-0.4.9-py3-none-any.whl) 的 72 项文件清单无 `.claude/skills`）

### P2：章节层级只算不存

section detector 已计算 `parent_idx`，SQLite schema 也支持 `parent_id`，但同步写入时固定为 `None`，所有子章节被扁平化；这是 roadmap 已记录但未完成的问题。（证据：`research_core/parsers/section_detector.py:147,276-305`；`research_core/rag/database.py:58-68,204-217`；`research_core/tools/admin.py:117-153`；`DEVELOPMENT_PLAN.md:80-86`）

### P2：持久化与网络边界需收紧

- BM25 用 `pickle.load` 读取 `.chroma_db/_bm25_index.pkl`；正常情况下它是本机生成文件，但若 persist dir 可被不可信用户写入，加载可执行恶意 pickle。（证据：`research_core/rag/bm25_index.py:225-266`）
- Chroma 默认 loopback 较安全，但允许 `ZRA_CHROMA_HOST` 直接传给 `chroma run --host`，代码没有配置认证；若改成 `0.0.0.0`，论文 chunk 可能暴露到局域网。（证据：`research_core/rag/chroma_server.py:73-108`；`research_core/rag/store.py:58-67`）
- “三索引验证”对无 DOI、超时和网络错误全部 fail-open，命名比真实保证更强；UI/返回字段应区分 `verified`、`inconclusive` 和 `unverifiable`。（证据：`research_core/sources/verify.py:1-14,78-121,155-163`）

## 九、安全与隐私边界

`main` 的基础数据面默认是本机：Zotero 读走 loopback，嵌入与重排在本地模型执行，Chroma 默认绑定 loopback；`.env`、`.pypirc*`、`.chroma_db`、用户词表和检索日志均在 `.gitignore` 中。（证据：`research_core/zotero/client.py:126-137`；`research_core/rag/embedding.py:90-98,160-187`；`research_core/rag/reranker.py:20-63`；`research_core/rag/chroma_server.py:73-108`；`.gitignore:10-31`）

但“全程本地”只适用于本地检索主链路：在线发现会把 query/DOI 发给 OpenAlex、Crossref、S2；推荐会把最近阅读论文 DOI 发给 OpenAlex/S2；CNKI 依赖已登录浏览器会话；添加论文会访问 Crossref/arXiv/OpenLibrary/Unpaywall/OpenAlex/S2/CORE 等服务。（证据：`research_core/tools/discover_online.py:182-223`；`research_core/tools/recommend.py:93-120`；`research_core/sources/cnki/browser.py:31-75`；`research_core/tools/manage.py:673-705`）

默认检索日志会在本机明文保留研究查询、扩展词、top-20 题名、item key 和排序分数 90 天。即使 `.gitignore` 防止普通 Git 提交，它仍可能进入系统备份、同步盘或被同机用户读取；敏感研究环境应默认关闭或提供字段脱敏和文件权限控制。（证据：`research_core/rag/logger.py:35-83,89-127,259-320`；`.env.example:115-116`）

仓库全历史的正则扫描未发现明显 AWS/GitHub/PyPI token 或私钥头，`.env.example` 也都是空值/占位符；这只是基础文本扫描，不等同于专业 secret scanner。（证据：`.env.example:1-118`；命令 `git log --all ... git grep ...` 返回 0 个明显凭证模式）

## 十、建议的后续开发路线

### 0. 先选定并清理开发基线

建议以 `origin/feat/lightweight-graphrag` 的代码方向为候选基线，因为它无分叉领先 12 个提交，并包含 PDF 质量门禁；但不要直接 merge。先从分支创建清理分支，移出个人评估数据并决定是否重写公开历史，再补齐 wheel 资源打包、更新 0.4.9 文档/版本并跑安装后的 MCP resource smoke test。（证据：`git log --oneline main..origin/feat/lightweight-graphrag`；`git rev-list --left-right --count main...origin/feat/lightweight-graphrag` 输出 `0 12`；`origin/feat/lightweight-graphrag:CHANGELOG.md:8-65`；该分支 `project_a_mcp/server.py:301-326` 与 `pyproject.toml:55-60`）

### 1. 把 CI 从“语法信心”提升到“行为信心”

优先为纯逻辑模块加无外部服务测试：query rewriting、BM25 tokenizer/RRF、MMR 参数契约、response truncation、write confirmation、section hierarchy、PDF quality gate。再用 fake Zotero/Chroma 或录制 fixture 把 MCP contract tests 纳入 CI；真实 Zotero/CNKI 测试可保留为手动/nightly。（证据：当前 `.github/workflows/ci.yml:33-40`；现有纯逻辑测试集中在 `tests/core/test_chunker.py:1-30` 与 `tests/unit/test_cnki_helpers.py:1-79`）

具体入口：

- `tests/mcp/test_tools.py`：先改成断言当前 dict envelope 与 `context_block`。（证据：`tests/mcp/test_tools.py:19-25,31-41`；`project_a_mcp/server.py:649-675`）
- `research_core/rag/retriever.py`：把 `diversity_weight` 改为 `relevance_weight`，或修正公式并加边界测试。（证据：`research_core/rag/retriever.py:595-705`）
- `.github/workflows/ci.yml`：执行完整 ruff 配置、coverage、build + wheel install smoke，并覆盖声明的 Python 版本或收窄 classifier。（证据：`.github/workflows/ci.yml:13-40`；`pyproject.toml:10-18,62-76`）

### 2. 先稳定数据摄取，再继续调检索

把活跃分支的列感知提取和 quality gate 作为高价值改进合入，但要补真实 PDF fixture：单栏、双栏、三栏、页眉页脚、扫描件、字体乱码、长表格、法律文书。quality gate 应把原因与受影响条目公开到 `inspect_index/check_health`，并允许用户选择 OCR 插件，而不是静默跳过。（证据：`origin/feat/lightweight-graphrag:research_core/parsers/pdf.py:22-27,77-99,161-230`；该分支 `tests/core/test_pdf_extraction.py:1-82`）

然后实现章节 parent linking，避免“整节扩展”在复杂小节结构中取到错误范围。（证据：`research_core/tools/admin.py:117-177`；`research_core/rag/retriever.py:142-199`）

### 3. 让评估可复现、可脱敏、可解释

保留 20-query LLM-as-judge 作为个人离线实验，但不要作为通用质量证明。仓库应新增脱敏小型 corpus、确定性 expected results、组件消融，以及按语言/学科/PDF 类型拆分的指标；no-answer 需要单独测拒答或低相关阈值，而不是把“无 expected keys”直接当 Recall=1。（证据：`tests/eval_queries.json:1-7`；`research_core/rag/evaluation.py:121-133,136-257,260-389`；`origin/feat/lightweight-graphrag:tests/eval_results/RECALL_EVALUATION_LOG.md:195-256`）

若继续采用开发分支的 7-call 策略，应测延迟、token/tool-call 成本、客户端兼容性，以及 direct/method 类别回退；结果已经显示它提升跨文档召回但会伤害部分简单查询，因此更适合按意图自适应触发，而不是所有查询强制 5-7 次调用。（证据：`origin/feat/lightweight-graphrag:tests/eval_results/RECALL_EVALUATION_LOG.md:235-256,260-289`）

### 4. 收敛运行时和发布面

建议把 NMT 合并成单例或采用开发分支的外部双语策略二选一，不要同时保留两套架构；修复全局 socket timeout 和默认缓存路径。为直接 import 的依赖补直接声明，建立 lock/constraints，增加 `build`/`twine` extra，并从安装后的 wheel 测试 CLI、query_dict/package data 和 strategy skills。（证据：`research_core/rag/query_rewriter.py:172-224`；`research_core/rag/indexer.py:17-55`；`pyproject.toml:22-45,52-60`；`scripts/publish.sh:55-90`）

### 5. 建立单一事实来源

工具清单、版本、配置项和 release notes 应由代码/manifest 生成或至少在 CI 校验，避免 36/39、0.4.5/0.4.8、旧包名与默认值不一致。优先同步 `pyproject.toml`、`CHANGELOG.md`、`DEVELOPMENT_PLAN.md`、两份 README、`.env.example` 和 `CLAUDE.md`。（证据：`pyproject.toml:1-5`；`CHANGELOG.md:8-20`；`DEVELOPMENT_PLAN.md:1-15`；`README_zh.md:215-224`；`.env.example:109-110`；`CLAUDE.md:3-11,120-127`）

## 十一、建议的首个开发里程碑

可以把第一个里程碑定义为“0.5.0 可验证基线”，完成标准如下：

1. 远端历史不再包含个人文库原始数据；公开仓库仅保留脱敏 fixture 和聚合评估。
2. 选定并合并活跃分支中 PDF quality gate、评估修复与发布修复；skill resources 能从实际 wheel 加载。
3. 分别为 `main` 的 39 个和 0.4.9 线的 40 个 MCP 工具建立 schema/返回 envelope 自动 contract test；CI 至少覆盖无网络的核心工具。
4. MMR 参数、NMT socket 副作用、章节 parent linking 修复并有回归测试。
5. 版本、tag、changelog、README 工具数和 PyPI URL 一致；wheel 安装 smoke test 通过。
6. 给日志、外部评估、第三方在线搜索写清晰的隐私开关与数据流说明。

这些完成条件分别对应当前已确认的隐私、测试、检索契约、摄取结构、发布和运行边界问题。（证据：本报告第七至第十节所列一手文件与 Git 命令）

## 十二、核查说明

- 本报告记录的是切换到 `feat/lightweight-graphrag` 前的审计快照；其后续一致性修正与提交不改变这里引用的历史证据。
- 未启动 Zotero、ChromaDB、CNKI 或任何外部文献 API，也未下载模型；因此没有对真实文库端到端能力做新的运行验证。
- 本地缺少 pytest/ruff，未擅自安装依赖；只完成 AST、TOML、compileall 与 Git 对象完整性检查。
- 远端分支证据均通过 `git show origin/feat/lightweight-graphrag:<path>` 读取，没有 checkout 或改写分支。
