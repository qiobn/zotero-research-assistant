# Development Log

> 开发者日志 — 记录每次重要更新的内容、解决的问题、技术决策和后续优化方向。
> 面向项目维护者和贡献者，比 CHANGELOG 更详细、更技术向。
>
> 格式：按版本分组，每项标注日期、关联 commit。

---

## Skills 通过 FastMCP Provider 暴露为 MCP Resources (2026-08-10)

### 问题

策略抽离为 `.claude/skills/` 后,只有 Claude Code 等能读取本地 skill 文件的客户端才能按需加载。Claude Desktop / Cherry Studio 等纯 MCP 客户端仍只能依赖 docstring 里的紧凑摘要,拿不到完整策略。

### 方案

利用 FastMCP 3.4.2 原生 skills provider,把 `.claude/skills/` 扫描为 MCP resources:

- `project_a_mcp/server.py` 新增 `_add_skills_provider()`,在 `mcp = FastMCP(...)` 后注册 `SkillsDirectoryProvider(roots=<skills dir>, reload=True)`
- 目录默认 `<项目根>/.claude/skills`,可用 `ZRA_SKILLS_DIR` 覆盖;目录不存在或 FastMCP 版本过旧时静默跳过(不阻塞 server 启动)
- skill 通过 `skill://<name>/SKILL.md` 和 `skill://<name>/_manifest` 两个 resource 暴露

### 技术决策

- **SKILL.md frontmatter 改为单行引号描述**:FastMCP 的 `parse_frontmatter` 是极简解析器,不支持 `description: >` 折叠块(会解析成 `">"`)。改为单行 `description: "..."` 后,Claude Code 与 FastMCP 解析器都兼容,`SkillInfo.description` 取到真实文本
- **`reload=True`**:每次请求重扫目录,改动即时生效,代价可忽略(两个文件)

### 验证

独立脚本实测:两个 `SkillProvider` 被发现;`skill://bilingual-search/SKILL.md`、`skill://graph-expansion/SKILL.md` 及其 `_manifest` 正确列出;description 解析为真实文本;正文 74/48 行完整读取。

### 后续优化方向

- 确认 Cherry Studio 等客户端是否消费 skill resources;若支持,可把更多策略(如 CORPUS-FIRST)下沉到 skill
- 评估 `reload=False` 的性能收益(当前无感知)

---

## 策略外置为 Skills: docstring → .claude/skills (2026-08-10)

### 问题

7-call 双语检索策略和 GraphRAG 图扩展策略以 100+ 行散文形式内嵌在 `search_papers` 的 docstring 中,带来三个问题:

1. **Token 成本** — 每次调用 `search_papers`(哪怕纯标题匹配)都要把 ~5600 字符的策略完整注入 LLM 上下文,稀释注意力
2. **不可复用** — 策略与 search_papers 强耦合,无法被其他工具/场景复用,也无法按需加载、版本化、单独测试
3. **双份维护** — `tests/strategy_variants_7call.json` 必须手工"重建"docstring 里的策略才能做确定性评估,策略改动要同步两处

### 方案

把策略从 docstring 抽离为独立 skill 文件(Claude Code 标准格式),能力保留在 MCP 层:

- `.claude/skills/bilingual-search/SKILL.md` — 7-call 加权双语检索 + RRF 合并(槽位权重表、合并公式、完整示例、评估对齐说明)
- `.claude/skills/graph-expansion/SKILL.md` — 种子发现 → 图扩展(`find_similar_papers` / 标签搜索 / 引用网络)→ 按出现频率合并

### 技术决策

- **docstring 不全删,压到一半** — 非 skill 客户端(Cherry Studio / Claude Desktop)无法按需加载 skill,完全移除会退化它们的检索质量。因此 docstring 保留紧凑可执行版(槽位/权重/合并规则/精简示例),115 → 59 行,约省 50% 常驻上下文
- **JSON 是机器可执行形态,skill 是权威散文形态** — `tests/strategy_variants_7call.json` 的 `_meta` 现在指向 skill 文件,策略只有一份散文权威来源,消除双份维护
- **FastMCP 3.4.2 原生支持 skill**(SKILL.md 可作为 MCP resource 暴露),后续可让同一份 skill 文件同时服务 skill 感知客户端

### 后续优化方向

- 用 FastMCP skills provider 把 `.claude/skills/` 暴露为 MCP resource,让 Claude Desktop / Cherry Studio 也能读取
- 从 strategy JSON 自动生成 docstring 策略段,彻底消除双份维护
- 把 CORPUS-FIRST 文献发现策略也从 `instructions=` 下沉到 skill

---

## v0.4.0-dev — 双语检索增强 (2026-07-14)

### NMT 查询翻译 (Layer 4): OPUS-MT CN→EN (2026-07-14)

**问题：** 原来的查询扩展只有 298 个方法论术语的词典映射，内容类查询（如"城市绿地健康对老年人影响"）词典全无命中 → BM25 零跨语言匹配 → 中文查询只能在中文字段中搜，大部分论文是英文导致漏检。

**方案：** 引入 Helsinki-NLP OPUS-MT zh-en 作为第 4 层扩展。lazy load（首次中文查询 ~3-5s，随后 ~400ms/查）+ LRU 缓存（与现有缓存合一）。

**技术决策：**
- 权重 0.8（介于原文 1.0 和词典扩展 0.4 之间）
- 仅 CN→EN 单方向（非双向）——面向中国科研者，默认行为是中文查询扩英文
- 模型权重 ~300MB，存于 `.chroma_db/hf_cache/`，可配置 `ZRA_NMT_CACHE_DIR`
- 失败安静降级（返回空字符串），不影响其他层

**已知限制：**
- 部分领域术语翻译不准确（如 `多主体模型` → `multi-subject` 而非 `multi-agent`）
- 极长查询（>15 字）延迟偏高（~860ms）
- 不解决重排序器的跨语言问题（当前 `ms-marco-MiniLM-L-6-v2` 纯英文）

### 索引时双语富化: Title_EN + Keywords_EN (2026-07-14)

**问题：** 即使查询翻译了，BM25 索引中只有中文文本 → 英文翻译后的查询词在纯中文 chunk 中匹配不到。

**方案：** 在 `_enrich_chunk_text()` 中对中文论文自动翻译标题和关键词，追加 `[Title_EN: ...] [Keywords_EN: ...]` 到 BM25/Dense 共用文本。索引时一次翻译，检索时零延迟。

**技术决策：**
- 中文/非中文判断：标题中日韩字符占比 > 30%
- 翻译结果按 (title, keywords) 对缓存，最多 8192 条目，溢出时全清（安全兜底）
- 线程安全（`threading.Lock`），兼容并行索引
- 不翻译正文全文（50 页 PDF 翻译成本不可接受）
- CHUNKING_VERSION 从 v3.1.0 → v3.2.0，自动触发索引重建

**评估验证：**
- 翻译延迟：索引时每次 ~400ms（标题）+ ~150ms（关键词）
- 264 篇论文的翻译开销：假设 40% 中文 → ~100 篇 * ~550ms ≈ 55s 额外索引时间

### 词典管理工具组 (2026-07-14)

新增 3 个 MCP 工具：
- `remove_query_synonym(term)` — 显式移除用户自定义双语同义词
- `list_query_synonyms()` — 列出所有用户定义的 CN→EN 映射
- `import_query_dict(entries)` — 以 JSON 字符串批量导入 CN→[EN...] 映射，json.loads + validate → persist → cache clear

迁移现有 `add_query_synonym([], ...)` 的删除逻辑到独立工具，保持 API 语义清晰。

---

---

## v0.4.9-dev — Retrieval Evaluation Hardening & Ablation Controls (2026-07-28)

### Index-time bilingual enrichment ablation switch (2026-07-28)

**Problem:** 查询侧双语策略已经外置给外部 LLM，但索引侧仍保留中译英 metadata 富化（`[Title_EN]` / `[Keywords_EN]`）。在没有显式开关的情况下，无法做干净的 bilingual ablation，也难以回答“当前跨语种效果到底来自多调用策略还是索引时英译提示”。

**Solution:** 在 `research_core/rag/indexer.py` 增加 `ZRA_INDEX_BILINGUAL_ENRICHMENT` 环境变量，默认值为 `true`。`_enrich_chunk_text()` 只在该开关开启时追加 `[Title_EN]` / `[Keywords_EN]`，从而允许通过“关开关 + 重建索引”进行受控对照实验。

**Technical decisions:**
- 默认保持 `true`，确保当前业务检索行为完全不变
- 仅把它定位为**消融实验开关**，不是日常建议配置
- 不修改查询侧逻辑、不移除 OPUS-MT、不改变已有索引格式；只有显式关闭并重建索引时才会影响行为
- 文档同步到 `.env.example`、`README.md`、`README_zh.md`、`CHANGELOG.md`

**Why this shape:**
- 用户明确要求：所有业务逻辑变动先确认；这次只批准“加开关”这一项
- 先做显式变量，比直接删除逻辑安全，也更适合跑前后对照

**How to use:**
1. 保持默认：不设置该变量，当前行为不变
2. 做消融：设置 `ZRA_INDEX_BILINGUAL_ENRICHMENT=false`
3. 重建索引后再运行同一套 recall/strategy evaluation，比较差异

### 问题复现: ChromaDB "Error loading hnsw index" (2026-07-23)

**现象**: chroma server (127.0.0.1:18000) 已运行，HttpClient heartbeat 正常，但 `collection.count()` 和 `collection.query()` 报错:
```
Error executing plan: Error sending backfill request to compactor:
Error constructing hnsw segment reader: Error loading hnsw index
```

**初步假设（被推翻）**: v0.4.3 记录的"PersistentClient 跨进程 HNSW bug"复现。

**验证过程**:
1. 测试 PersistentClient 写入 → HttpClient 读取: **成功** (ChromaDB 1.5.9 + SQLite 存储)
2. 测试 chroma server 写入 → 重启 → 读取: **成功**
3. 检查 HNSW segment 目录: `.chroma_db/20f5c1ba-fc60-4ff8-9ec9-5097568faa4a/`
   - `index_metadata.pickle` ✓ (832KB, `total_elements_added`=63,918)
   - `data_level0.bin` ✗ **缺失**
   - `link_lists.bin` ✗ **缺失**
   - `header.bin` ✗ **缺失**
   - `length.bin` ✗ **缺失**

**真正根因**: ChromaDB 的 HNSW compaction 从未成功执行过。

`index_metadata.pickle` 中 `dimensionality: None` 是关键线索。HNSW 图结构需要维度才能初始化，维度为 None → hnswlib 无法创建 `data_level0.bin` 和 `link_lists.bin` → 后续所有查询尝试加载 HNSW → 文件不存在 → "Error loading hnsw index"。

`total_elements_added: 63,918` vs embeddings 表记录数 19,790 说明多次 `sync_index` 往 WAL 追加了数据，但 compactor 始终未能压缩成有效的 HNSW 段。

**与 v0.4.3 "跨进程 bug" 的区别**:
- v0.4.3 修复的是架构层面（切换 server 模式消除多进程竞争），是正确预防措施
- 本次发现的是磁盘上已存在的数据缺失（HNSW 核心文件从未被创建），server 模式无法修复已损数据
- 两者不矛盾：server 模式防止未来问题，但不能修复 PersistentClient 时代产生的不完整数据

**修复**: `reset_collection()` + `sync_index(force_rebuild=True)` 通过 chroma server 重建整个索引。

**预防措施（待实现）**:
- `_create_client()` 的 PersistentClient fallback 是静默危险操作——sync_index 若在 server 未启动时运行会 fallback 到可能产生破损数据的模式。应移除 fallback 或至少增加 `dimensionality` 检查
- `_startup_diagnostics` 应增加 segment 目录文件完整性检查

**验证方法（已验证有效）**:
- 新建 collection → add 2000 vectors → 查询 → 确认 `data_level0.bin` 等文件存在 → 重启 server → 查询 → **正常**
- 证明 rebuild 后的索引不会再有此问题

---

## v0.3.0 — RAG 质量管线全面升级 (2026-07-06)

### 双格式输出: JSON + Markdown Context Block (2026-07-10, `706afff`)

**问题：** 所有 MCP 工具返回纯 JSON。对 LLM 存在三个问题：
1. Token 浪费——JSON 的引号、方括号、逗号、key 名消耗大量 token
2. 注意力稀释——LLM 难以从扁平 JSON 中区分证据文本和元数据
3. 分数不可理解——`score: 0.0321` 对 LLM 没有直觉意义

**方案：** 参考 [Anthropic MCP 最佳实践](https://github.com/anthropics/skills/blob/main/skills/mcp-builder/reference/mcp_best_practices.md)，给核心检索工具添加 pre-rendered Markdown `context_block` 字段，与 JSON items 双通道输出：
- Markdown → LLM 消费主通道（blockquote 证据、★★★ 分级、句边界截断）
- JSON → 程序化消费通道（结构化元数据、日志、统计）

**技术决策：**
- Blockquote (`>`) 用于引用文本——LLM attention 权重最高
- `###` 三级标题编号——树形心理模型
- ★★★/★★/★ 替代浮点分数——基于 Cross-Encoder 百分位分桶（>75th → high, >25th → medium）
- `_snippet()` 句边界截断——CJK `。！？；` + EN `.!?` 双向感知
- CJK 人名格式检测——Unicode 范围 `"一" <= c <= "鿿"` 判断姓在前/后

**Token 实测（6 篇论文，cl100k_base）：**
- 旧纯 JSON: 1,559 tokens
- 新 JSON+MD: 1,762 tokens (+13%，双格式冗余)
- context_block 单独: 931 tokens（比旧 JSON 少 40%）
- 结论：双格式有 ~13% 冗余开销，但 LLM 从 Markdown 提取信息的准确率更高。后续可选 `response_format` 参数让用户选择。

**最没把握的点：**
1. 双格式冗余——`items` 和 `context_block` 有信息重复，总 token 量反而增加了 13%
2. 没有做 A/B LLM 响应质量对比测试——只能推断 Markdown 格式更好，没有硬数据
3. `relevance_tier` 在少量结果时（<4 条）百分位分桶可能不准确

**后续方向：**
- 添加 `response_format="json"` / `"markdown"` / `"both"` 参数
- A/B 测试对比 JSON vs Markdown 的 LLM 引用准确率
- 将 `generate_reading_note` 和 `find_arguments` 也接入 context_block

---

## v0.3.0 — RAG 质量管线全面升级 (2026-07-06)

### 背景

v0.2.0 的 RAG 管线是"能跑就行"的状态：PDF 提取后直接分块、embedding、入库搜索。通过对 20 篇论文的审计发现：

- 嵌入分离度只有 1.13x（阈值 1.3x），论文间区分度不足
- "Keywords:"、"A R T I C L E I N F O" 等期刊 boilerplate 被当作语义内容入库（85%+ 论文受影响）
- 零检索观测性——搜不到论文时完全不知道原因
- 无系统化评估——改参数后无法判断是改进还是退化
- 所有 chunk 被平等对待，无法区分高质量段落和碎片

---

### Phase 0: 基线审计 (2026-06-30, `f9a5947`)

**新增文件：**
- `scripts/index_sample.py` — 从库中随机采样 N 篇论文构建测试索引，支持 `--count` 和 `--random`
- `scripts/audit_index.py` (~800 行) — 7 阶段全库质量审计：分页扫描、逐论文评分、文库覆盖率、噪声检测、嵌入分离度、健康评分、建议

**审计基线（20 篇论文 / 2102 chunks）：**

| 指标 | 值 | 判定 |
|------|-----|------|
| 乱码 chunk | 0% | 优秀 |
| 长 chunk (>1500) | 0% | Cap 生效 |
| 短 chunk (<50) | 2.8% | 可接受 |
| 图表 chunk | 16.9% | 提取正常 |
| 嵌入分离度 | 1.13x | 弱（阈值 1.3x） |
| 噪声模式 | "Keywords:", "A R T I C L E I N F O", "A B S T R A C T" | 85%+ 论文确认 |
| 平均 chunk/论文 | 105.1 | 过于细粒度 |
| 健康分 | 65/100 (B) | 需改进 |

**解决的问题：** 之前完全不知道索引质量如何，现在有量化基线
**技术决策：** 审计先于代码——先看清问题，不盲目写代码

---

### Phase 1.1: 文本清洗引擎 (2026-07-01, `f9a5947`)

**新增文件：**
- `research_core/parsers/text_cleaner.py` (~350 行)

**52 条黑名单正则规则：**
- EN 期刊 (9 条)：文章信息栏（"A R T I C L E  I N F O"）、摘要标题（"A B S T R A C T"）、关键词标题、页眉
- CN 期刊 (24 条)：卷期号（"第 XX 卷第 XX 期"）、中图分类号（"〔中图分类号〕TU984.2"）、文献标识码、日期行、基金信息、作者简介、页码括号（"〔123〕"）
- 通用 (19 条)：独立数字页码、DOI/ISSN/ISBN 行、URL 片段、重复标点、空白标准化

**API 设计：** 返回 `(cleaned_text, CleaningReport)` 元组，报告包含行级统计和分类统计

**集成方式：** 在 `admin.py _parse_and_chunk()` 中分块前调用 `clean_text()`
**环境变量：** `ZRA_CLEAN_ENABLED=true`（默认开启）
**实测效果：** 平均 10.6% 行移除率（CN 19.3%, EN 7.2%）

**技术决策：黑名单 > 启发式。** 期刊格式高度公式化，正则精确匹配零误杀风险——没有论文正文会包含"〔中图分类号〕TU984.2"。启发式方法（如频率统计）会把"Accessibility"（4 篇论文的关键词）误判为噪声
**后续优化：** 增加 PDF 质量评分器（区分原生 PDF/扫描件/加密），针对低质量 PDF 调整清洗策略

---

### Phase 1.2: 系统性评估框架 (2026-07-01, `f9a5947`)

**新增文件：**
- `research_core/rag/evaluation.py` (~250 行)
- `tests/eval_queries.json` — 60 条黄金查询
- `scripts/run_evaluation.py` — 评估 CLI
- `scripts/generate_eval_queries.py` — LLM 辅助查询生成

**评估查询分布：**
- 直接命中（~50%）：答案在单个 chunk 内
- 跨文档综合（~25%）：需跨论文综合信息
- 无答案拒绝（~15%）：验证检索不会返回虚假结果

**实现的指标：**
- Recall@5/10/20
- MRR (Mean Reciprocal Rank)
- NDCG@10（DCG 公式 `(2^s-1)/log2(i+2)`）

**基线数据：** R@5=0.792, R@10=0.825, R@20=0.867, MRR=0.736

**CLI 使用：**
```bash
python scripts/run_evaluation.py              # 完整评估
python scripts/run_evaluation.py --baseline   # 保存为新基线
python scripts/run_evaluation.py --compare    # vs 基线 A/B 对比
```

**技术决策：LLM 生成候选 + 人工审校。** 纯人工写 60 条太慢，纯 LLM 不准
**后续优化：** 扩展到 100+ 条，加入"矛盾检测"类别，补充人工评估维度

---

### Phase 1.3: 检索日志/全链路追踪 (2026-07-01, `f9a5947`)

**新增文件：**
- `research_core/rag/logger.py` (~210 行)

**每条 trace 捕获的字段（20+）：**
- `trace_id`、时间戳、原始查询
- 搜索策略（hybrid / semantic / keyword / fallback）
- 候选数（关键词 N、语义 N、合并 N）
- 重排序器状态（启用/禁用、模型名、重排前后数量）
- Top-20 结果（item_key、标题、分数、排名、来源）
- 延迟分解（keyword_ms / semantic_ms / rerank_ms / total_ms）
- 回退触发标志和回退结果数

**存储方案：** JSONL 追加写入 + 字节偏移索引文件（`_retrieval_log.idx`），支持快速按 trace_id 随机访问

**3 个新 MCP 工具：**
- `recent_retrievals(n=20, strategy="")` — 查看最近检索记录
- `retrieval_trace(trace_id)` — 按 trace ID 回放完整链路
- `retrieval_stats()` — 聚合统计（总查询数、平均延迟、策略分布、回退率）

**集成位置：** `search_papers()` 末尾自动记录，无需手动调用

**技术决策：字节偏移索引 > SQLite。** JSONL 可 grep、可手动编辑、不引入额外依赖，对嵌入式个人研究者场景更轻量
**后续优化：** 按日期/延迟/策略的多维过滤统计；两个 trace 的 diff 视图

---

### Phase 2.1: Chunk 质量元数据 (2026-07-02, `b70e539`)

**修改文件：**
- `research_core/parsers/chunker.py` — v2.8.0 → v2.9.0

**Chunk dataclass 新增 7 个质量字段：**

| 字段 | 类型 | 含义 |
|------|------|------|
| `coherence_score` | float [0,1] | 句长变异系数映射 → 低值=句子碎片化 |
| `information_density` | float [0,1] | (文本长度 - 停用词长度) / 文本长度 |
| `boilerplate_ratio` | float [0,1] | 已知模板片段匹配的字符占比 |
| `sentence_count` | int | 完整句子数 |
| `starts_with_conjunction` | bool | 以连接词开头（前一个 chunk 被截断的信号） |
| `language` | str | "zh" / "en" / "mixed"（基于 CJK/ASCII 字符比例） |
| `quality_flag` | str | "good" / "noisy" / "incomplete" / "boilerplate" |

**评分函数：** `score_chunk_quality()` — 纯启发式（句长变异系数、停用词比、模板匹配），不调用额外模型。Chunk 太小（~600 字符），跑第二个模型得不偿失

**存储：** ChromaDB metadata，支持检索时按 `quality_flag` 过滤
**audit_index.py：** 新增 quality flag 分布统计

**技术决策：启发式 > 模型。** 句长变异和停用词比已经足够判断 chunk 质量，不引入额外依赖和延迟
**后续优化：** 检索时对 boilerplate/incomplete chunk 降权或直接过滤

---

### Phase 2.2: SQLite 元数据库 + 章节检测 (2026-07-02, `9948ac2`)

**新增文件：**
- `research_core/rag/database.py` (~370 行)
- `research_core/parsers/section_detector.py` (~250 行)

**SQLite 数据库（`.chroma_db/papers.db`），7 张表：**

| 表 | 内容 | 关键字段 |
|----|------|----------|
| `papers` | 论文元数据 | title, abstract, authors(JSON), keywords(JSON), doi, journal, year |
| `sections` | 层级化 IMRaD 结构 | heading, section_type, level, parent_id(自引用), page_start/end |
| `chunks_meta` | Chunk 位置+质量 | id, section_id, chunk_idx, page_start/end, 7 质量字段 |
| `figures` | 图标题锚点 | figure_label, figure_ref, caption, page |
| `table_records` | 表标题锚点 | table_label, table_ref, caption, raw_content, page |
| `chunk_figure_refs` | 多对多交叉引用 | chunk_id ↔ figure_id |
| `chunk_table_refs` | 多对多交叉引用 | chunk_id ↔ table_id |

**关键设计决策：**
- 摘要存入 SQLite 但**不嵌入 ChromaDB**。摘要是论文的"浓缩版"，对任何相关查询都有中等匹配度，嵌入后会降低整体区分度
- Thread-safe 单例初始化（`sqlite3.check_same_thread=False` + `RLock`）
- 零用户配置——首次 `sync_index` 时自动创建
- 与 ChromaDB 元数据职责分离：ChromaDB 只存检索必需字段（item_key, title, page, section），详细元数据在 SQLite

**章节检测器 (`section_detector.py`)：**
- H1 模式：编号章节（"1. Introduction"）、中文编号（"一、引言"）、裸关键字（"Methods\n"、"引言\n"）
- H2/H3 模式："1.1 Study Area"、"（一）研究区域"、带圈数字
- `_classify_section_type()` — 关键词映射到 11 种 IMRaD 类型
- `_is_valid_heading()` — 长度和字母占比过滤，防止误检噪声行
- Quality-aware：跳过 quality_flag 为 boilerplate/incomplete 的 chunk

**同步管线集成：** `_index_metadata()` (~120 行) — upsert 论文、插入 sections、chunks_meta、figures、tables、交叉引用

**后续优化：** 在 papers 表增加 `citation_count`、`journal_quality`、`is_retracted`；利用 keywords 做 query expansion

---

### Phase 2.2 (续): 章节级上下文扩展 (2026-07-02, `86c34d4`)

**修改文件：**
- `research_core/rag/retriever.py`

**三个新方法：**

1. `Retriever.expand_to_section(item_key, chunk_idx)` — 单 chunk 章节查找：
   - SQLite `chunks_meta` → 获取 `section_id`
   - SQLite `sections` → 获取章节 heading、type、page range
   - SQLite → 获取该章节所有 chunk ID
   - ChromaDB `collection.get()` → 批量获取 chunk 文本
   - 拼接为完整章节全文

2. `Retriever._attach_section_contexts(results)` — 批量扩展，使用 `dict` 缓存避免重复 DB + ChromaDB 查询

3. `Retriever.enrich(results)` — 批量 JOIN 获取论文 + 章节元数据：
   - `enrich_results(conn, chunk_ids)` — 单次 JOIN papers + sections
   - 注入 paper_abstract、paper_authors、paper_year、paper_doi、paper_keywords、section_heading、section_type
   - 零感知延迟——SQLite JOIN 成本极低

**`SectionContext` dataclass：** heading, section_type, full_text, chunk_ids, page_start, page_end

**前端效果：** `search_papers(expand_context=True)` → `matched_passage` 从 300 字符 chunk 片段变为 2000 字符完整章节

---

### Phase 2.2 (续): 检索结果富化 (2026-07-03, `06d8f43`)

**修改文件：**
- `research_core/rag/retriever.py` — `RetrievalResult` 新增 6 个字段
- `research_core/tools/search.py` — `PaperHit` 新增 3 个字段

**`RetrievalResult` 新增字段：** paper_abstract, paper_authors, paper_year, paper_doi, paper_keywords, section_heading, section_type

**`PaperHit` 新增字段：** paper_abstract, section_heading, section_type

**效果：** 搜索结果不仅返回匹配段落和分数，还附带论文摘要预览、所在章节标题和类型

---

### Phase 2.4: 嵌入质量诊断 (2026-07-03, `82918ed`)

**新增文件：**
- `research_core/rag/embedding_diagnostics.py` (~372 行)

**6 阶段分析管线：**

| 阶段 | 分析内容 | 方法 |
|------|----------|------|
| 1 | 采样论文 + 计算嵌入 | 随机采样 N 篇论文，numpy float32 |
| 2 | 论文内部分离度 | Pairwise cosine sim + centroid coherence |
| 3 | 论文间最相似对 | Centroid-to-centroid 比较，top-10 |
| 4 | 长度-相似度相关性 | Pearson 相关系数 |
| 5 | 章节类型分离度 | 按 section_type 分组分析 centroid 一致性 |
| 6 | 自动问题检测 + 建议 | 分离度 < 1.3、离群率 > 10%、相似对 > 0.85 等 |

**实测发现（测试集 20 篇论文）：**
- 分离度 0.95x（低于 1.0，论文间 > 论文内）
- 长度-相似度正相关 r=0.44（长 chunk 更容易匹配）
- 最相似论文对达到 0.88（潜在近重复论文）
- **清理 + overlap 改动后分离度从 1.13x 降到了 0.95x** — 确认元数据感知检索不是"可选项"而是"必选项"

**`format_diagnostic_report()`** — 人类可读的输出格式

**后续优化：** PCA/UMAP 投影数据导出；按 topic cluster 分离分析；可视化

---

### Overlap 重写 (v2.8.0) (2026-07-01, `f9a5947`)

**修改文件：** `research_core/parsers/chunker.py` — v2.7.1 → v2.8.0

**变更：** 句子数 overlap (1 句) → 字符数 overlap (100 chars) + 句边界补全

**`_tail()` 算法重写：**
1. 正向搜索（重叠区域内 `[end-overlap_chars*2, end]`）：找最后一个句边界（`.!?。！？`）
2. 反向搜索（扩展到句首）：从句边界往前找句首
3. 从句标点回退：无完整句子时用 `,;，；` 等从句边界
4. 安全空 fallback：什么都没找到时返回空字符串（不返回垃圾）

**Bug 修复过程：**
- 第一版（反向搜索）：EN overlap 仅 1%，因为从 `start` 往回找，根本找不到句边界
- 第二版（正向搜索）：找到边界但取了边界之后的文本（末尾为空）
- 第三版（最终版）：在整个搜索窗口找最后一个句边界，取其后文本 → 正常工作

**实测效果：** CN 67% / EN 46-50% overlap 覆盖

---

### A/B 评估结果

文本清洗 + overlap 改动后的对比：
- Recall@5: -0.009（微小下降，在噪声范围内）
- MRR: +0.011（微小提升）

**结论：** 改动向后兼容，不损害检索精度。这些改动是"基础设施"——不直接提升 raw precision，但为质量过滤、元数据感知检索、query rewrite 提供了必要条件

---

#### ONNX INT8 嵌入后端 (2026-07-08, `embedding.py`)

- 新增 `ONNXInt8Embedding` — 基于 ONNX Runtime 的 INT8 量化嵌入
- 使用社区预量化模型 `skatzR/USER-BGE-M3-ONNX-INT8`（347MB vs FP32 2.3GB）
- `EMBEDDING_BACKEND=auto`（默认）：优先 ONNX INT8，不可用时回退到 sentence-transformers
- 基准测试：3.7x 编码加速，0.95 embedding fidelity，74% chunk@10 重叠
- 零用户配置——`auto` 模式自动选择最佳后端
- **解决的问题**：首次安装体积从 ~4.3GB 降至 ~370MB；索引速度 2-3x

#### MMR 多样性重排序 (2026-07-07, `980ab81` / `ac925fd`)

- Chunk 级 MMR：λ=0.6（60% 相关性 + 40% 多样性）
- 利用 ChromaDB 中已有的 bge-m3 embedding，一次 `collection.get()` 获取（~15ms）
- 硬 cap：每篇论文最多 3 个 chunk；per-document penalty：第 3 个起每 chunk -0.1
- 默认开启（`diversity_weight=0.6`），`diversity_weight=0` 关闭
- 测试结果（6 个查询）：
  - 论文数：平均 +1.2 篇（最高 +2 篇）
  - 单篇最多 chunk：平均从 4.8 降到 2.7（最高从 8 降到 3）
  - 原本已多样化的查询不受影响

#### Query Rewrite 词典扩展 (2026-07-07, `c10aeff` / `89ecae5`)

- 三层架构：内置 ~300 对方法论词典 + Zotero 标签自动提取 + `add_query_synonym` MCP 工具
- CN↔EN 双向，最长贪婪匹配，LRU 缓存 512 条（<2ms）
- 6 大类：研究方法、变量统计、AI/ML、RAG/检索、数据评估、社科经济
- 集成在 `search_papers()` 中，无感知、零延迟、无 LLM 依赖

---

### 当前已知问题

1. **嵌入分离度 0.95x**（低于 1.0）— 等元数据增强重排序解决
2. **长度-相似度正相关 r=0.44** — chunk 可能靠长度而非内容排名
3. **CNKI 模块不稳定** — 依赖浏览器自动化（Playwright + Chrome CDP），默认关闭
4. **无 Contextual Summarization** — 需要 MCP server 有独立 LLM 访问权限

### 下一阶段方向 (Phase 3)

| 优先级 | 任务 | 预期收益 |
|--------|------|----------|
| P0 | 自适应 Chunk 粒度（methods=400, discussion=700） | 提升长段落检索精度 |
| P1 | 元数据增强重排序（引用数、期刊质量、撤稿状态） | 提升学术排序质量 |
| P1 | 自适应 Chunk 粒度（methods=400, discussion=700） | 提升长段落检索精度 |
| P1 | MMR 多样性重排序 | 防止单篇论文主导 top-K |
| P2 | 元数据增强重排序（引用数、期刊质量、撤稿状态） | 提升学术排序质量 |
| P2 | 综合诊断 MCP 工具 `diagnose_rag` | 一键全链路诊断 |
| P3 | Contextual Summarization（PaperQA2 风格） | 查询相关摘要重排序，需额外 LLM |

---

## v0.2.0 — 独立 MCP Server 发布 (2026-06-11)

**关联 commit：** `52b8247` `7b88467` `3bdda3c` `191ee35` `9fe189b` `484e871` 等 (PR #1 ~ #9)

### 概述

从 agent scaffold 重构为纯 MCP server，暴露 32 个单意图工具。每个工具映射一个用户意图，通过 `item_key` 串联。

---

### 架构重构：去除 Agent Scaffold (`7b88467`)

**删除：**
- `project_b_agent/` — FastAPI agent 后端占位
- `research_core/llm/` 和 `research_core/eval/` — agent 专用模块
- `tests/agent/`
- agent 相关依赖（fastapi, uvicorn, sse-starlette, aiosqlite, litellm, openai）

**原因：** 本项目定位为 MCP 工具服务端，LLM 由客户端（Cursor/Cherry Studio/Claude Desktop）提供，不需要内置 agent 能力
**影响：** 版本从 0.1.2 跳到 0.2.0

---

### P0 → P2 稳定性修复 (3 个 PR)

**P0: Bug 修复** (`52a5d54`)
- health check 中 `embeddings` → `embedding` 拼写错误（导致健康检查始终报 ImportError）
- `_diagnose_error` 中 `and`/`or` 运算优先级导致 `"empty" in lower and "index" in lower` 被短路

**P1: 并发安全 + 内存优化 + 响应 Size Cap** (`191ee35`)
- 统一 ChromaDB 客户端单例 + `sync_lock`（RLock 保护所有写操作）
- `inspect_index` 分页读取（每页 1000 chunk），防止大库 OOM
- `_truncate_response()` 函数：MCP 响应上限 50K 字符，大字段截断标记 `[TRUNCATED]`

**P2: 架构清理** (`9fe189b`)
- `expand_citation_network` 从 `server.py` 提取到 `research_core/tools/citation_network.py`
- 全局 HTTP 客户端：并发控制、per-host 限流、429/5xx 自动重试
- 统一工具响应格式：裸列表 → `{data: [...], count: N}`
- 响应截断策略优化：上限 80K → 先裁剪 text 字段 → 最后才删 item

---

### 并发与性能优化 (`3bdda3c`)

**架构层面：**
- Thread-safe 单例初始化（embedding, reranker, store, server globals）
- `verify.py` 三态模型（True/False/None）：网络瞬断不再丢弃合法论文
- 外部 API 调用统一走共享 HTTP 客户端（重试/限流/超时/缓存）
- Zotero 客户端 socket 超时 + 断路器（桌面端不运行时快速失败）

**RAG 性能：**
- HNSW 参数调优（搜索 ef=100，构建 ef/M 可选环境变量覆盖）
- 设备感知批量 embedding（cuda/mps/cpu）
- 并行 PDF 解析+分块，串行索引（sync_lock 保护）

**MCP 工具：**
- CNKI 条件注册（`CNKI_ENABLED=false` 时 32→28 默认工具）
- `search_all_annotations` 扫描上限防止 O(N*M) API 风暴

---

### 表格/图：从结构化提取到标题锚点 (`e7b7f0c` → `52b8247`)

**第一阶段：尝试 ML 结构化提取** (`e7b7f0c`)
- 新增 `ZRA_TABLE_MODE=ml`，使用 Microsoft Table Transformer
- `table_ml.py` — 线程安全的懒加载模型，处理 config 兼容性问题
- 表格识别后从 PDF text layer 填充单元格（无 OCR），输出 Markdown
- 实测效果：在三线表上提取正确，但 prose 误检被 tabular-signature guard 过滤
- 代价：需要 torchvision + timm + pillow 依赖，`[tables]` optional extra

**第二阶段：放弃结构化，改为标题锚点** (`52b8247`)
- **全文删除**结构化表格提取（lite 模式 + Table Transformer ml 模式、`[tables]` extra、`table_ml.py`）
- 回归标题锚点方案：
  - 表格：从标题提取 label + ref，保留原始内容块（数值可检索），不拆成行列
  - 图片：从标题提取 label + ref + caption，不解码图像
  - CJK 感知的 prose boundary 检测；tabular-content guard
  - 正文"如表3/如图2"自动链接到对应记录
- Chunker: v2.7.1-caption-tables

**放弃结构化提取的原因：** 实测 PyMuPDF 线框检测在无框三线表上产出垃圾——单篇论文产出 110 个假多栏"表格"；甚至把多栏正文和参考文献误判为几十栏的假表。可靠的表格结构化本质上是视觉识别问题（需要"看图"），纯文本/几何方案不可行

---

### CJK 感知分块 (`59637d8`)

**问题：** 分块器假设了英文习惯——断句要求空格在标点后（中文 `。！？` 无空格），硬截断只按空格/换行。导致中文 chunk 在句子中间被切断，PDF 软换行使词语断开（`满\n意度`）

**修复：**
- `_split_sentences()` — CJK 终结符后直接断句（不检查空格）；ASCII 终结符保持空格门控（防止拆分 "U.S." / "3.14"）
- `_hard_split()` — 最优边界：句终结符 → 从句标点 `，、` → 空格
- `_join_soft_wraps()` — 页面组装时修复 CJK 软换行和拉丁连字符
- Chunker 版本：v2.4-cjk-aware（自动触发全量重建索引）

---

### 表格/图交叉引用 (`776ba34` / `435a4f5`)

**表格交叉引用** (`776ba34`, v2.5-table-xref)：
- 从标题解析 label + canonical ref（表3 / Table 3 / Tab. 5 / 附表2）
- 表格 chunk 开头添加自然语言列摘要提升语义召回
- 扫描 prose chunk 中的表格提及（如表3所示 / Tables 3 and 4 / 表3、4），与论文实际表格取交集（排除引用别的论文的情况）
- `get_item_tables(item_key, refs)` — 按 ref 获取表格 chunk，多部分表有序
- 新增量词 guard：排除"试图3次"、"代表3个"的假匹配

**图交叉引用** (`435a4f5`, v2.6-figure-xref, 不识别图像)：
- 与表格交叉引用对称设计
- 从原始页面文本提取标题（pre-soft-wrap-join，防止标题混入下一段）
- 量词 guard：排除"5 items"的假匹配
- `get_item_figures(item_key, refs)` — 按 ref 获取图 chunk

**API 效果：** `get_paper_content` 返回 passage 的 `referenced_tables` / `referenced_figures` 和 `cites_tables` / `cites_figures`

---

### Zotero API 过滤修复 (`18b9451`)

**问题：** Zotero 本地 API 静默忽略组合取反过滤器 `"-attachment || note"`，返回所有 item（附件和笔记也被当作论文）。实测文库 732 个 item 中有 468 个是附件/笔记

**修复：** 客户端侧使用正向类型查询逐一排除；同样保护了 `get_all_items_minimal` 和 `search_all_annotations`

**ChromaDB 修复：** `collection.modify()` 传入 `hnsw:space` 时被 ChromaDB 拒绝（当作距离函数变更）。修改前 strip 掉再传

---

### 结构化表格提取 + Hard Chunk Cap (`70b4317`)

**问题：** 一篇 15MB 数据密集型 PDF 在索引时触发 20 GiB MPS 分配失败。根因：一个无框数据表产生 9038 字符（2294 token）的无边界跑块（无句边界），批量 64 时 bge-m3 注意力张量达到 20.07 GiB

**修复：**
- 从页面文本提取线框表（保守的线检测），从 prose 中移除表格区域防止重复索引
- 表格渲染为行分组 Markdown chunk（每部分重复表头），附带 JSON 负载
- `_enforce_max_chars()` — 通用硬 cap，所有 chunk 不超过 max_chunk_size（Chunker v2.3-tables-hardcap）
- `EMBEDDING_MAX_SEQ_LEN=1024` 环境变量安全网，限制模型 max_seq_length
- 验证：之前失败的 QZATEAF3 (141 chunks) 和 D9VKKS8H (85 chunks) 正常索引，最大 chunk ≤ 1200 chars

---

### GitHub Actions CI (`a33e0bf`)

- 推送到 main 和 PR 时触发
- 矩阵：Python 3.11 + 3.12
- 步骤：安装依赖 → ruff lint → tests/core + tests/unit
- tests/mcp 集成套件排除（需运行中的 Zotero 和已构建索引）
- 添加 MIT LICENSE 文件（`c1412e0`）

---

### PyPI 发布脚本 (`fc9b6bb` / `f382e61`)

- `scripts/publish.sh` — 无凭据泄露的发布流程（凭据从 `~/.pypirc` 或 `TWINE_*` 环境变量读取）
- 支持 `--test` (TestPyPI) 和 `--check-only`（仅构建+检查）
- 无凭据时的快速失败 preflight 检查
- `.pypirc` 加入 `.gitignore` 防止 token 泄露

---

### Bilingual Cherry Studio Setup Guide (`9b04df9`)

- 重写 `docs/cherry-studio-setup.md`：pip install 为主线，源码安装移至附录
- 新增 `docs/cherry-studio-setup-en.md`（完整英译版）
- Python 版本要求下限 ≥3.11
- `HF_ENDPOINT` 镜像加速嵌入模型下载
- 自动应用 HF 镜像，加载失败时给出提示

---

## v0.1.0 — 初始开发 (2026-05-15 ~ 2026-06-09)

> 此阶段大部分开发在另一台机器上完成（Co-authored-by: Cursor），以下从 commit 记录还原。

### 时间线

**2026-06-09** — 阅读状态与个性化推荐 (`6ee526f`)
- 启发式阅读状态检测（深度阅读/浏览过/未读），基于标注数、笔记、PDF 打开记录
- 个性化推荐：识别最投入论文 → OpenAlex Related Works + S2 Recommendations 并行 → 按交叉种子频率排序
- 聚焦主题提取：从近期阅读标签提炼活跃研究方向

**2026-06-09** — OpenAlex Related Works (`82086a0`)
- 添加 OpenAlex Related Works 作为并行搜索策略

**2026-06-07** — 文献综述 + 智能标签 (`f4c4dff`)
- 多论文证据提取 + 带页码引用的主题综述生成
- 智能标签建议（方法论/领域/数据类型），建议制（不自动应用）

**2026-06-06** — 论据发现器 (`da9ec43`)
- 给定论点，按立场分类（支持/反对/中立）从库中检索证据

**2026-06-05** — MCP 工具描述重写 (`28649ef`)
- 精简 MCP 指令和工具 docstrings

**2026-06-03** — CNKI 知网集成 (`22f1372` / `e9a7eaf`)
- Playwright 浏览器自动化，通过 Chrome CDP 连接已登录浏览器
- 期刊等级标签（CSSCI/北大核心/CSCD/SCI/EI）
- 直接导入 Zotero，无需 DOI 查找
- 智能翻页

**2026-06-02** — 引用网络扩展 (`f5384ee` / `8762f0d`)
- OpenAlex 正/反向引用 + S2 Recommendations API
- 分层查询生成：Metadata → 配对查询 → 多源检索 → 后过滤 → 去重
- CNKI 超时修复、引用网络并行执行

**2026-06-01** — 反幻觉护栏 (`f9affee` / `2a3482a`)
- P0 语料优先策略（从已知引用优先扩展）
- P1 `[MATERIAL GAP]` 结构化标签
- P2 三索引交叉验证（CrossRef + OpenAlex + S2 交叉核实 DOI）
- 无法验证的结果自动过滤
- 学科过滤（`fields_of_study`）、相关性后过滤

**2026-05-30** — 在线文献发现 (`a0d91ce`)
- OpenAlex + CrossRef + Semantic Scholar 并行检索
- OA PDF 瀑布流：arXiv → Unpaywall → OpenAlex → S2 → CORE → PMC
- `6fbf87f` — 扩大在线搜索覆盖（CrossRef + Elsevier）

**2026-05-28** — 项目重命名 (`34a3fb0`)
- 正式命名为 `zotero-research-assistant`

**2026-05-25** — 功能扩展至 16 工具 (`e51a578`)
- 容器化嵌入
- `b5efc64` — 写操作需明确用户确认
- `5058c01` — 支持空查询的 filter-only 搜索

**2026-05-20** — RAG 管线优化 + 图文标题检测 + 双语 README (`1817edd`)
- 分块器重写为语义感知策略（v2.1-semantic）：段落优先、句边界回退、参考文献段检测
- 图表标题标注（Figure/Fig/Table/图/表 模式）
- 分块版本追踪 + SyncState；策略变更自动全量重建
- 3 个新管控工具：`check_health`、`inspect_index`、`test_recall`
- Retriever：默认排除参考文献，新增 `get_figure_table_chunks()`
- 增强错误处理：`_diagnose_error` + 双语修复建议
- `sync_index`：质量摘要报告、chunk 丢失检测
- 中英双语 README

**2026-05-18** — Cherry Studio 配置指南 (`68b6621`)
- 面向零编程基础用户重写

**2026-05-15** — 项目启动 (`077d7a1`)
- 13 个 MCP 工具 + 共享核心（`research_core/`）
- Zotero 本地 API + Web API 混合模式

---

### v0.1.0 → v0.2.0 关键版本记录

| 版本 | 日期 | 事件 |
|------|------|------|
| v0.1.1 | 05-20 | RAG 管线优化 + 双语 README + 3 个管控工具 |
| v0.1.2 | 05-28 | PyPI 发布、镜像支持、Cherry Studio 指南重写 |
| v0.2.0 | 06-11 | 独立 MCP Server、32 工具、CJK 分块、表格交叉引用、并发安全 |

---

## 早期技术决策（v0.1.0 时期做出的核心选择）

### 嵌入模型选择
**bge-m3 (1024-dim) > all-MiniLM-L6-v2 (384-dim)。** BGE-M3 支持 100+ 语言，CJK 和 English 表现都强。MiniLM 维度太小，对中文支持不够。代价是首次下载 ~2.3GB，但这是一次性成本

### 向量数据库选择
**ChromaDB > Qdrant/Milvus。** ChromaDB 轻量、Python 原生、零运维、嵌入式，适合个人研究者单机使用场景。Qdrant/Milvus 适合服务端部署，过度设计

### 混合搜索架构
**关键词 + 语义 RRF 融合。** 纯语义搜索会漏掉精确匹配（如 DOI、作者名），纯关键词搜索会漏掉语义相关但不同词的论文。RRF (Reciprocal Rank Fusion) 是简单有效的融合方法，论文验证过

### Cross-Encoder 重排序
**ms-marco-MiniLM-L-6-v2**（~80MB）。Bi-encoder 召回 + Cross-encoder 精排是验证过的范式。选 MiniLM 而非更大的模型是因为它在速度和精度之间达到了实用的平衡点

### 反幻觉策略
**三层防线：** (1) 三索引交叉验证——每个有 DOI 的结果在 CrossRef/OpenAlex/S2 交叉核实；(2) `[MATERIAL GAP]` 结构化标签——搜索零结果时明确标记而非让 LLM 编造；(3) 来源可溯——每篇结果附可验证链接

### 表格/图处理策略
**标题锚点 > 结构化提取。** 见 v0.2.0 详细说明。这个决策经历了"尝试 ML 提取 → 发现不可行 → 撤回"的完整过程，最终确认可靠的表格结构化是视觉问题，文本方案不可行

---

## 格式约定

- 每条重要更新记录：日期、版本号、关联 commit、类别
- 类别：新增 / 修复 / 变更 / 优化 / 移除 / 技术决策
- **"解决的问题"和"后续优化方向"是核心** — 对于了解技术债务和规划 roadmap 至关重要
- 每次 commit 时评估是否需要更新此文件 + `CHANGELOG.md`（参考 `CLAUDE.md` 中的规则）
