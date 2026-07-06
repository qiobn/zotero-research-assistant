# Development Log

> 开发者日志 — 记录每次重要更新的内容、解决的问题、技术决策和后续优化方向。
> 面向项目维护者和贡献者，比 CHANGELOG 更详细、更技术向。
>
> 格式：按版本分组，每项标注日期、类别、关联 commit。

---

## v0.3.0 — RAG 质量管线全面升级 (2026-07-06)

### 背景

v0.2.0 的 RAG 管线是"能跑就行"的状态：PDF 提取后直接分块、embedding、入库搜索。通过对 20 篇论文的审计发现多个问题：

- 嵌入分离度只有 1.13x（阈值 1.3x），论文间区分度不足
- "Keywords:"、"A R T I C L E I N F O" 等期刊 boilerplate 被当作语义内容入库（85%+ 论文受影响）
- 零检索观测性——搜不到论文时完全不知道原因
- 无系统化评估——改参数后无法判断是改进还是退化
- 所有 chunk 被平等对待，无法区分高质量段落和碎片

### 新增

#### Phase 0: 基线审计 (2026-06-30, `f9a5947`)
- `scripts/index_sample.py` — 从库中随机采样 N 篇论文构建测试索引
- `scripts/audit_index.py` — 7 阶段全库质量审计（分页扫描、逐论文评分、覆盖率、噪声检测、嵌入分离度、健康评分、建议）
- 20 篇论文/2102 chunk 的审计基线：嵌入分离度 1.13x，噪声覆盖 85%+ 论文，健康分 65/100
- **解决的问题**：之前完全不知道索引质量如何，现在有量化基线
- **技术决策**：审计先于代码——先看清问题，不盲目写代码

#### Phase 1.1: 文本清洗引擎 (2026-07-01, `f9a5947`)
- `research_core/parsers/text_cleaner.py` (~350 行)
- 52 条黑名单正则规则，分三类：
  - EN 期刊 (9 条)：文章信息栏、摘要/关键词标题、页眉
  - CN 期刊 (24 条)：卷期号、中图分类号、文献标识码、日期行、基金信息、作者简介、页码括号
  - 通用 (19 条)：独立数字页码、DOI/ISSN/ISBN 行、URL 片段、重复标点、空白标准化
- 返回 `(cleaned_text, CleaningReport)` 元组
- 环境变量 `ZRA_CLEAN_ENABLED=true` 控制（默认开启）
- 集成到 `admin.py _parse_and_chunk()`，在分块前执行
- **解决的问题**：期刊 boilerplate 被当作语义内容索引，降低检索精度
- **技术决策**：黑名单 > 启发式。期刊格式高度公式化，正则精确匹配零误杀风险——没有论文正文会包含"〔中图分类号〕TU984.2"
- **后续优化方向**：增加 PDF 质量评分器（区分原生 PDF/扫描件/加密），针对低质量 PDF 跳过清洗

#### Phase 1.2: 系统性评估框架 (2026-07-01, `f9a5947`)
- `research_core/rag/evaluation.py` (~250 行)
- 指标：Recall@5/10/20、MRR、NDCG@10（使用 DCG 公式 `(2^s-1)/log2(i+2)`）
- `tests/eval_queries.json` — 60 条黄金查询，覆盖三类：
  - 直接命中（~50%）：答案在单个 chunk 内
  - 跨文档综合（~25%）：需跨论文综合
  - 无答案拒绝（~15%）：验证不返回虚假结果
- `scripts/run_evaluation.py` — CLI 工具，支持 `--save-baseline` 保存基线、`--compare` A/B 对比
- `scripts/generate_eval_queries.py` — 从索引论文元数据自动生成查询
- **解决的问题**：之前只有单论文 `test_recall`，无法量化评估检索变更
- **技术决策**：LLM 生成候选 + 人工审校。纯人工写太慢，纯 LLM 太不准
- **后续优化方向**：扩展到 100+ 条查询，加入"矛盾检测"类别，加入人工评估维度的打分指南

#### Phase 1.3: 检索日志/追踪 (2026-07-01, `f9a5947`)
- `research_core/rag/logger.py` (~210 行)
- JSONL 追加写入 + 字节偏移索引文件（`_retrieval_log.idx`）
- 每条记录捕获：trace_id、时间戳、查询、策略（hybrid/semantic/keyword/fallback）、关键词候选数、语义候选数、重排序前后数量、top-20 结果（含分数/排名/来源）、延迟分解（keyword/semantic/rerank/total ms）、回退触发标志
- 3 个新 MCP 工具：`recent_retrievals`、`retrieval_trace`、`retrieval_stats`
- 集成在 `search_papers()` 中——每次搜索自动记录
- **解决的问题**："为什么这篇论文没搜到？"之前零可见性
- **技术决策**：字节偏移索引 > SQLite。JSONL 更轻量、可 grep、可手动编辑，对嵌入式场景更友好
- **后续优化方向**：增加按日期/延迟/策略的过滤统计，支持对比两个 trace 的 diff 视图

#### Phase 2.1: Chunk 质量元数据 (2026-07-02, `b70e539`)
- `chunker.py` 升级至 v2.9.0
- Chunk dataclass 新增 7 个质量字段：
  - `coherence_score` — 句长变异系数映射到 [0,1]（低值 = 片段化）
  - `information_density` — 停用词占比的倒数（低值 = 信息稀疏）
  - `boilerplate_ratio` — 匹配已知模板片段的字符占比
  - `sentence_count` — 完整句子数
  - `starts_with_conjunction` — 是否以连接词开头（前一个 chunk 被截断的信号）
  - `language` — "zh" / "en" / "mixed"（基于 CJK/ASCII 字符比例）
  - `quality_flag` — "good" / "noisy" / "incomplete" / "boilerplate"
- 轻量启发式评分函数 `score_chunk_quality()`，不依赖额外的模型
- 存入 ChromaDB metadata，支持质量感知过滤
- **技术决策**：启发式 > 模型。chunk 太小，跑第二个模型得不偿失。句长变异和停用词比就够了
- **后续优化方向**：根据 quality_flag 在检索时降权或过滤低质量 chunk

#### Phase 2.2: SQLite 元数据库 + 章节级上下文扩展 (2026-07-02, `9948ac2` / `86c34d4` / `06d8f43`)
- `research_core/rag/database.py` (~370 行)
- 7 张关系表，独立于 ChromaDB 向量库：
  - `papers` — 标题、摘要、作者、年份、DOI、关键词（JSON 字符串）
  - `sections` — 层级化 IMRaD 结构（parent_id 自引用）
  - `chunks_meta` — 位置 + 质量评分
  - `figures` / `table_records` — 标题锚点记录
  - `chunk_figure_refs` / `chunk_table_refs` — 多对多交叉引用
- 摘要**不嵌入向量库**（避免摘要对所有查询都有中等匹配度从而主导搜索结果）
- SQLite 文件在 `.chroma_db/papers.db`，thread-safe 单例初始化，零用户配置
- `Retriever.expand_to_section()` — hit 单 chunk → SQLite 查章节 → 取该章节所有 chunk → 拼接全文
- `Retriever._attach_section_contexts()` — 缓存批量扩展，避免重复查询
- `Retriever.enrich()` — 批量 JOIN 获取论文 + 章节元数据
- **技术决策**：不嵌入摘要。摘要是论文的"浓缩版"，对任何相关查询都有中等匹配度，嵌入后会让不相关的论文也排上来，降低整体区分度
- **后续优化方向**：在论文粒度增加 citation_count、journal_quality、is_retracted 字段；利用关键词做语义搜索的 query expansion

#### Phase 2.4: 嵌入质量诊断 (2026-07-03, `82918ed`)
- `research_core/rag/embedding_diagnostics.py` (~372 行)
- 6 阶段分析管线：
  1. 采样论文，用 numpy 计算嵌入
  2. 每篇论文内部 pairwise + centroid coherence
  3. 最相似论文对（centroid 比较）
  4. Chunk 长度与相似度的 Pearson 相关性
  5. 按章节类型（methods/results/discussion/introduction）分析嵌入分离
  6. 自动问题检测 + 修复建议（分离度 < 1.3、离群率 > 10%、相关性异常等）
- 测试集发现：文本清洗 + overlap 改动后分离度从 1.13x 降到 0.95x（比之前更差）——确认了元数据感知检索的必要性
- **后续优化方向**：增加 PCA/UMAP 投影数据导出，可视化；增加按 topic cluster 的分离分析

#### Phase 2.8-2.9: Overlap 重写 (2026-07-01, `f9a5947`)
- `chunker.py` v2.7.1 → v2.8.0：句子数 overlap (1 句) → 字符数 overlap (100 chars)
- `_tail()` 算法完全重写：正向搜索（重叠区域内） → 反向搜索（扩展到句首） → 从句标点 → 安全空 fallback
- **解决的问题**：原算法正向搜索只找到文本末尾的句号，EN overlap 仅 1%
- **修复后**：EN overlap 恢复正常（找到区域内最后一个句边界并取其后文本）
- **后续优化方向**：根据 chunk 语言自适应 overlap 大小（中文句短用 50，英文句长用 100）

#### v0.2.0 Overlap 修复 (2026-06-11, 多个 commit)
- `59637d8` — CJK 感知断句：`。！？` 不依赖空格直接断句，PDF 软换行修复 `满\n意度`→`满意度`
- `70b4317` — 硬 cap chunk 大小，防止异常长文本导致 embedding OOM
- `52b8247` — 表格/图重构：删除结构化提取，改为标题锚点记录
- **技术决策**：表格结构化是视觉问题。几何/线框检测在无框三线表上产出垃圾，甚至把多栏正文和参考文献误判为几十栏的假表。把这个功能从默认流程移除，改为可选的外部视觉解析器预处理方案
- **后续优化方向**：提供 MinerU/Docling 预处理集成文档

#### 文档与工程 (2026-07-06, `d570812`)
- README (中英文) 同步至 v0.3.0：Highlights 新增 5 行，RAG Pipeline 重写为 16 项
- 客户端配置增强：Claude Desktop 补充 Windows 路径 + Pro 提示，Cherry Studio 补充 3 步流程 + MCP 开关说明，Codex CLI 补充 `codex mcp list` 验证命令
- `DEVELOPMENT_PLAN.md` 进度更新至 Phase 0/1/2 全部完成
- `DEVELOPMENT_LOG.md` (本文件) — 从 81 条 commit 中提取，建立开发者视角的详细变更记录
- `CHANGELOG.md` — 完善 v0.3.0 发布说明

### A/B 评估结果

文本清洗 + overlap 改动后的评估对比：
- Recall@5: -0.009（微小下降，在噪声范围内）
- MRR: +0.011（微小提升）
- 结论：改动向后兼容，不损害检索精度。这些改动是"基础设施"——它们不直接提升 raw precision，但为后续的元数据感知检索、质量过滤、query rewrite 提供了必要条件

### 当前已知问题

1. **嵌入分离度 0.95x**（低于 1.0，论文间相似度 > 论文内相似度）。清洗和 overlap 改动后比原始 1.13x 更差。等元数据感知检索和 query rewrite 后才能解决
2. **长度-相似度正相关 r=0.44**：长 chunk 更容易匹配，可能导致论文靠 chunk 长度而非内容排名
3. **CNKI 模块**不稳定，依赖浏览器自动化，默认关闭
4. **无 Contextual Summarization**（PaperQA2 的核心创新，Phase 2.5 已推迟）：需要 MCP server 有自己的 LLM 访问权限，是新的架构依赖
5. **无 Query Rewrite**（Phase 3.1）：中英双语扩展、同义词扩展

### 下一阶段方向 (Phase 3)

| 优先级 | 任务 | 预期收益 |
|--------|------|----------|
| P0 | Query Rewrite（中英双语扩展 + 同义词） | 直接提升 Recall，解决中英文术语不匹配 |
| P1 | 自适应 Chunk 粒度（methods=400, discussion=700） | 提升长段落（讨论/引言）的检索精度 |
| P1 | MMR 多样性重排序 | 防止单篇论文主导 top-K 结果 |
| P2 | 元数据增强重排序（引用数、期刊质量、撤稿状态） | 提升学术文献排序质量 |
| P2 | 综合诊断 MCP 工具 `diagnose_rag` | 一键全链路诊断 |
| P3 | Contextual Summarization（PaperQA2 风格） | 查询相关摘要再排序，需额外 LLM |

---

## v0.2.0 — 独立 MCP Server 发布 (2026-06-11)

### 概述
项目从 agent scaffold 重构为纯 MCP server，暴露 32 个单意图工具。每个工具映射一个用户意图，通过 `item_key` 串联。

### 新增
- 独立 MCP server (`project_a_mcp/server.py`)，stdio 传输
- 引用网络扩展（OpenAlex 正/反向引用，多 DOI 种子）
- 多策略相关论文发现（语料优先 + 关键词 + 引用网络 + S2 推荐 + OpenAlex 并行）
- 表格/图交叉引用（标题锚点记录 + 正文引用自动链接）
- CJK 感知分块（中文断句 + 软换行修复）
- 共享 HTTP 客户端（全局并发上限、per-host 限流、重试/退避、短 TTL 缓存）
- 中英双语 README + Cherry Studio 配置指南

### 重大变更
- 表格/图从结构化提取改为标题锚点记录
- 写操作 dry-run 默认，需明确确认
- 响应 size cap 保护 LLM 上下文窗口
- 错误返回结构化双语诊断

### 删除
- 内置结构化表格提取（`table_ml.py`、`[tables]` extra）
- Legacy agent scaffold

### 技术决策
- **为什么去掉结构化表格**：实测 PyMuPDF 几何检测在无框三线表上产出垃圾，甚至把多栏正文/参考文献误判为几十栏假表。表格结构化本质上是个视觉问题。提供 MinerU/Docling 作为外部预处理方案
- **为什么独立 MCP Server**：去掉 agent scaffold 使架构更清晰——本项目是"工具"，LLM 由客户端提供

---

## v0.1.0 — 初始开发 (2026-05 ~ 2026-06)

### 时间线

**2026-06-09** — 阅读状态与推荐 (`6ee526f`)
- 启发式阅读状态检测（深度阅读/浏览过/未读），基于标注数、笔记、PDF 打开记录
- 个性化推荐：识别最投入论文 → OpenAlex + S2 并行 → 交叉命中排序

**2026-06-07** — 文献综述生成 (`f4c4dff`)
- 多论文证据提取 + 带引用的主题综述生成
- 智能标签建议（方法论/领域/数据类型），建议制不自动应用

**2026-06-06** — 论据发现器 (`da9ec43`)
- 给定论点，从库中按立场分类（支持/反对/中立）检索证据

**2026-06-03** — CNKI 集成 (`22f1372`)
- Playwright 浏览器自动化，通过 CDP 连接已登录 Chrome
- 期刊等级标签（CSSCI/北大核心/CSCD/SCI/EI）
- 直接导入 Zotero，无需 DOI

**2026-05-30** — 在线文献发现 (`a0d91ce`)
- OpenAlex + CrossRef + Semantic Scholar 并行检索
- OA PDF 瀑布流：arXiv → Unpaywall → OpenAlex → S2 → CORE → PMC

**2026-05-28** — 反幻觉护栏 (`2a3482a`)
- 学科过滤（`fields_of_study`）
- 相关性后过滤
- `[MATERIAL GAP]` 结构化标签

**2026-05-25** — 引用网络 (`e1d2b4d`)
- OpenAlex 正/反向引用
- 语料优先策略（从已知引用扩展）

**2026-05-20** — 三索引交叉验证 (`f9affee`)
- 每个有 DOI 的结果在 CrossRef + OpenAlex + S2 交叉核实
- 无法验证的自动过滤

**2026-05-15** — 项目启动 (`077d7a1`)
- 13 个 MCP 工具 + 共享核心
- Zotero 本地 API + Web API 混合模式

### 早期技术决策
- **bge-m3 作为默认嵌入模型**：1024 维，100+ 语言，中英文都强。all-MiniLM-L6-v2 太小（384 维），对中文支持不够
- **ChromaDB 而非 Qdrant/Milvus**：轻量、Python 原生、零运维，适合个人研究者场景
- **混合搜索**：Zotero 关键词 + ChromaDB 语义，RRF 融合——关键词保证精确匹配不会丢，语义提供模糊发现
- **Cross-Encoder 重排序**：ms-marco-MiniLM-L-6-v2 作为可选增强，体积小（~80MB），精度提升显著

---

## 格式约定

- 每条重要更新记录：日期、版本号、关联 commit、类别
- 类别：新增 / 修复 / 变更 / 优化 / 移除 / 技术决策
- "解决的问题"和"后续优化方向"对于了解技术债务非常重要
- 每次 commit 时评估是否需要更新此文件 + CHANGELOG.md
