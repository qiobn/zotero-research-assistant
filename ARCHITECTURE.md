# Architecture Decision Records

> Why we chose X instead of Y — for every major technical decision in the RAG pipeline.
>
> Last updated: 2026-07-13

---

## 1. PDF Extraction: PyMuPDF

| | Our choice | Main alternative |
|---|---|---|
| **Library** | **PyMuPDF (fitz)** | Unstructured / Grobid / pdfplumber |
| **Method** | `page.get_text("text")` | Layout-aware parsing |

**Why PyMuPDF:**
- Fastest pure-text extraction among all Python PDF libraries (C++ backend)
- Zero configuration — no model downloads, no API keys, no Docker containers
- Excellent CJK text extraction (critical for Chinese academic papers)
- `get_toc()` provides outline/heading structure for free
- MIT license, actively maintained

**Why NOT the alternatives:**
- **Grobid**: Requires Docker container running a Java service. Excellent for structured academic paper parsing (authors, affiliations, references), but adds ~2GB dependency and network latency. Overkill for chunk-level text extraction where we only need prose, not structural annotation.
- **Unstructured**: Powerful layout analysis but heavy dependency chain. Its ML models add hundreds of MB. The "basic" mode without ML is comparable to PyMuPDF but slower.
- **pdfplumber**: Good for table extraction from born-digital PDFs, but slower than PyMuPDF for pure text extraction. Table extraction is explicitly NOT our goal (see Table/Figure decision below).

**Trade-off:** We lose automatic author/affiliation/reference extraction that Grobid provides. These would be useful for metadata but are not required for our core retrieval use case.

**When to revisit:** If the project adds citation network features requiring parsed reference lists.

---

## 2. Text Cleaning: Blacklist Regex (52 rules)

| | Our choice | Main alternative |
|---|---|---|
| **Method** | **Line-level blacklist regex** | ML-based noise classification |

**Why blacklist regex:**
- Academic journal boilerplate is highly formulaic: "中图分类号", "A R T I C L E  I N F O", "CLC number:", "DOI:", volume/issue/number lines. These patterns are KNOWN and STABLE.
- Blacklist regex has **near-zero false positive risk**: no paper body text ever contains "中图分类号" or "〔基金项目〕".
- Zero inference cost — runs at line-scanning speed during ingestion.
- Easily auditable and extensible. Each rule is a regex pattern with a comment explaining what it removes.

**Why NOT ML classification:**
- A classifier would need training data (labeled noise/clean lines from hundreds of papers) and would still have false positives.
- The false-positive cost is HIGH: removing a sentence from a paper's body is worse than leaving a noisy line in.
- ML adds latency, model dependencies, and maintenance burden — for a problem solvable with 52 regex patterns.

**Trade-off:** New journal formats with novel boilerplate patterns will require adding new regex rules. This is manual maintenance but happens infrequently (the 52 rules cover 85%+ of papers already).

**Decision date:** 2026-06-30 (v0.3.0)

---

## 3. Chunking: Sentence-Boundary + Paragraph-Aware

| | Our choice | Main alternatives |
|---|---|---|
| **Method** | **Sentence-boundary paragraph merge/split** | RecursiveCharacterTextSplitter / Semantic chunking / Agentic chunking |
| **Default size** | target=600, max=1200 chars | 512-1024 tokens |

**Why sentence-boundary paragraph-aware:**
- Academic papers HAVE paragraph structure. Unlike web scrapes or legal documents, PDFs of papers naturally break at paragraph boundaries. Exploiting this structure is strictly better than treating the text as a flat stream.
- CJK-aware sentence splitting: Chinese periods (。) and English periods (.) are both respected. PDF soft-wrap repair handles the common case where PDF extraction inserts line breaks mid-sentence.
- The 200-char minimum chunk floor prevents the ~43-token fragments that FloTorch 2026 benchmark found kill end-to-end accuracy.
- Fixed-size recursion (the LangChain default) cuts anywhere — mid-sentence, mid-paragraph, mid-thought. For research papers where arguments flow across paragraphs, this damages retrieval quality.

**Why NOT semantic chunking:**
NAACL 2025 (*"Is Semantic Chunking Worth the Computational Cost?"*) conclusively showed semantic chunking's extra compute cost is not justified by performance gains over fixed-size chunking. Chroma Research independently confirmed that chunk SIZE is the dominant variable, not the splitter strategy. PaperQA2 uses fixed-character chunks (~9,000 chars) with downstream LLM reranking to correct imperfections.
The 2025-2026 consensus is: tune chunk size, not chunk strategy.

**Why NOT agentic/adaptive chunking:**
The 2026 adaptive chunking paper (*de Moura Junior et al.*) shows promising results (87% vs 50% baseline) but on clinical data, not academic papers. It requires running 6 chunkers per document — 6x indexing time. Not production-ready for CPU-only desktop use.

**Trade-off:** Paragraph-aware splitting requires the text to HAVE paragraph structure. For poorly OCR'd or unstructured PDFs (old scans, some theses), we fall back to a sliding-window approach. The fallback is functional but lower quality.

**When to revisit:** When 2026-2027 literature provides clearer evidence that semantic/adapative chunking beats fixed-size on academic paper corpora specifically.

**Decision date:** 2026-06-30 (v2.8.0), refined 2026-07-10 (v3.1.0)

---

## 4. Embedding Model: BGE-M3 (1024-dim)

| | Our choice | Main alternatives |
|---|---|---|
| **Model** | **BAAI/bge-m3** | all-MiniLM-L6-v2 / text-embedding-3-small / E5-mistral / Jina-embeddings-v3 |
| **Dimension** | 1024 | 384 (MiniLM) / 1536 (OpenAI) / 1024 (Jina) |

**Why BGE-M3:**
- **Multi-lingual**: Explicitly trained on Chinese + English data. This is the decisive factor — the user's Zotero library contains both CN and EN papers. Single-language models (all-MiniLM, E5-en) fail on Chinese queries.
- **1024 dimensions** provide sufficient capacity for academic text without the storage cost of 1536-dim (OpenAI) or 4096-dim models.
- **Open-weight, self-hosted**: No API key, no rate limit, no per-query cost, no data leaving the user's machine. Zero-config for the default use case.
- Mature ecosystem: the model has been extensively benchmarked and has ONNX quantization support from the community (skatzR/USER-BGE-M3-ONNX-INT8).

**Why NOT the alternatives:**
- **all-MiniLM-L6-v2**: English-only. 384 dimensions is too small for academic text with nuanced methodology terminology. ChromaDB's default and what zotero-mcp uses — but that project targets English-only users.
- **text-embedding-3-small (OpenAI)**: Requires API key + internet. ~$0.02 per 1M tokens. Excellent quality but violates the "works offline on your desktop" design goal. Same for Cohere, Voyage.
- **E5-mistral (7B)**: Requires GPU for reasonable inference speed. 4096-dim output is storage-heavy. Excellent quality but not for CPU-only desktop use.
- **Jina-embeddings-v3**: Strong multilingual performance with 1024-dim output. Viable alternative. Chose BGE-M3 over it because BGE-M3 had better Chinese benchmark scores and an existing ONNX INT8 quantized version in the community.

**Trade-off:** BGE-M3 is large compared to all-MiniLM (2.3GB FP32 vs ~120MB). This is mitigated by ONNX INT8 quantization (347MB).

**Decision date:** 2026-06-15 (v0.2.0). ONNX INT8 backend added 2026-07-03.

---

## 5. Embedding Backend: ONNX INT8 (default)

| | Our choice | Main alternatives |
|---|---|---|
| **Runtime** | **ONNX Runtime + INT8 quantized** | Sentence-Transformers FP32 / llama.cpp / CTranslate2 |
| **Size** | 347 MB | 2,300 MB (FP32) / 1,200 MB (FP16) |
| **Speed** | ~3.7x faster than FP32 on CPU | |

**Why ONNX INT8:**
- **CPU-only users are the target audience.** Researchers using Zotero on laptops without GPUs. ONNX Runtime with INT8 quantization is 2-3x faster than PyTorch FP32 on CPU. This is the difference between "index my library while I get coffee" and "index my library overnight."
- 4x smaller on disk than FP32 (347MB vs 2.3GB). Matters for laptops with limited SSD space.
- <1% Recall@5 loss vs FP32 in our benchmarks (74% chunk@10 overlap). The downstream Cross-Encoder reranker and MMR diversity layer absorb the minor differences.
- Zero user effort: `EMBEDDING_BACKEND=auto` (default) tries ONNX INT8 first, transparently falls back to sentence-transformers FP32 if onnxruntime is unavailable.

**Why NOT stay FP32:**
- FP32 is the reference quality but 2-3x slower on CPU. Acceptable for small libraries (<50 papers), painful for large ones.
- PyTorch inference on CPU has significant overhead from the Python interpreter and autograd engine, even in `no_grad()` mode.

**Why NOT FP16:**
- FP16 on CPU requires the CPU to support AVX-512 VNNI or similar instructions. Most consumer laptops don't. Falls back to FP32 anyway.
- Half the size of FP32 but only marginally faster on CPU. The real CPU speedup comes from INT8 quantization, not half-precision.

**Why NOT llama.cpp / CTranslate2:**
- Both are excellent for LLM inference but less mature for embedding models specifically. ONNX Runtime has first-class embedding model support via the `transformers` export pipeline.

**Trade-off:** ONNX INT8 loses the ability to fine-tune the embedding model (model weights are frozen). This is acceptable because we don't fine-tune (and fine-tuning on a single user's library of <1000 papers would overfit).

**Decision date:** 2026-07-03

---

## 6. Vector Store: ChromaDB

| | Our choice | Main alternatives |
|---|---|---|
| **Database** | **ChromaDB (embedded)** | Qdrant / Milvus / Weaviate / FAISS / LanceDB |
| **Deployment** | In-process (PersistentClient) | Docker / Cloud / Embedded |

**Why ChromaDB:**
- **Zero setup**: `PersistentClient(path=".chroma_db")` — that's it. No Docker, no daemon, no port configuration. The database is a directory on disk. This is the single most important factor for a desktop MCP server: the user should not need to install and configure a separate database service.
- Python-native API with `EmbeddingFunction` abstraction. Integrates cleanly with our ONNX INT8 and sentence-transformers backends.
- HNSW indexing with cosine distance — the standard for semantic search.
- Metadata filtering with `where` clauses — used for reference section exclusion and item_key filtering.
- Active maintenance (2024-2026) with breaking changes handled via version pins.

**Why NOT the alternatives:**
- **Qdrant**: Excellent performance and features (quantization, payload indexing). But requires running as a separate service. Local mode exists but is less mature than ChromaDB's embedded mode. Overkill for single-user desktop use.
- **Milvus**: Industrial scale, designed for billion-vector collections. Requires Docker + etcd + MinIO for the full deployment. "Killing a fly with a sledgehammer" for a personal Zotero library of <100K chunks.
- **Weaviate**: Similar to Qdrant — excellent but requires a service. The embedded mode is Java-based, not Python-native.
- **FAISS**: Meta's library. Raw performance unmatched, but it's a C++ library with Python bindings, not a database. No persistence, no metadata filtering, no CRUD operations. We'd need to build our own database layer on top.
- **LanceDB**: Newer contender (2024+), embedded-first like ChromaDB. Built on Lance columnar format. Interesting but less mature ecosystem. Worth re-evaluating in 2027.

**Trade-off:** ChromaDB's performance at scale (100K+ chunks) is acceptable but not stellar. The SQLite-backed metadata store partially offloads filtering work. If the library grows to 1M+ chunks (unlikely for a personal Zotero library), Qdrant embedded would be the migration path.

**Decision date:** 2026-06-10 (v0.1.0)

---

## 7. Sparse Retrieval: BM25 (rank_bm25)

| | Our choice | Main alternatives |
|---|---|---|
| **Library** | **rank_bm25 (BM25Okapi)** | SPLADE / ColBERT / Elasticsearch |

**Why BM25:**
- **Proven baseline**: 2025 survey across 9 biomedical RAG systems found BM25 in >50% of production systems. Microsoft found hybrid (BM25+dense) scored 48.4 vs 43.8 (dense-only). It's the "boring but effective" choice.
- Zero external dependency: `rank_bm25` is pure Python, installable via pip. No Elasticsearch cluster, no Java, no service to manage.
- CJK tokenizer is self-contained (character unigrams + bigrams). No jieba segmentation dependency needed.
- Persists as pickle alongside ChromaDB — same directory, same lifecycle.
- The 2025 NAACL paper on embedding "rank ceilings" showed that dense embeddings CANNOT represent complex combinatorial queries — sparse retrieval is mathematically necessary, not just beneficial.

**Why NOT SPLADE / ColBERT:**
- **SPLADE**: Learned sparse retrieval using BERT-based term weighting. Better than BM25 on some benchmarks, but requires a neural model for both indexing and querying. Adds significant latency to both paths. Overkill for a personal library where BM25 recall is already sufficient.
- **ColBERT (late interaction)**: Per-token embeddings, max-similarity scoring. Excellent for code and multilingual domains (+14 recall@5 on mixed-language corpora). But 4-10x storage cost vs single-vector, and PLAID engine adds complexity. Worth reconsidering if the project expands to code or systematic review use cases.

**Trade-off:** BM25 requires all documents in memory during search. For 100K+ chunks, this could be ~200MB RAM. Acceptable for desktop use. The pickle file is rebuilt on every sync (full scan of ChromaDB) — could be optimized to incremental updates.

**Decision date:** 2026-07-13

---

## 8. Query Expansion: Dictionary-Based (No LLM)

| | Our choice | Main alternatives |
|---|---|---|
| **Method** | **3-layer dictionary lookup** | LLM-based (HyDE, Query2Doc, Step-Back) / Embedding-based |

**Why dictionary-based:**
- **Zero latency**: LRU-cached dictionary scan adds <1ms to query time. LLM-based expansion adds 200-2000ms + API cost.
- **Academic domain fit**: Methodology terms are finite and well-defined. "Difference-in-differences" ↔ "双重差分", "gravity model" ↔ "引力模型", "instrumental variables" ↔ "工具变量". These are not creative paraphrases — they are standard translations.
- Three layers provide progressive coverage: built-in dictionary (310 pairs from query_dict.json, cross-disciplinary), auto-extracted from user's Zotero tags (personalized, grows with library), user-defined synonyms (curated, persisted).
- Language detection (CJK/ASCII ratio) auto-determines expansion direction.

**Why NOT LLM-based:**
- **HyDE** (Hypothetical Document Embeddings): Generates a fake answer, embeds that, searches with it. Effective (+5-10% recall in benchmarks) but adds one LLM call per query. Violates the "works offline on your desktop" principle unless we bundle a local LLM — which brings its own complexity.
- **Query2Doc / Step-Back**: Same issue — require LLM calls.
- The project's design goal is local-first, zero external API dependency for core retrieval. LLM-based expansion would be a configurable enhancement, not the default.

**Trade-off:** Dictionary coverage is domain-limited. A user in molecular biology won't benefit from the built-in social science terminology pairs. Layer 2 (Zotero tags) partially addresses this — the user's own tags become expansion terms.

**Decision date:** 2026-07-05

---

## 9. Reranker: ms-marco-MiniLM-L-6-v2 Cross-Encoder

| | Our choice | Main alternatives |
|---|---|---|
| **Model** | **cross-encoder/ms-marco-MiniLM-L-6-v2** | Cohere Rerank / BGE-Reranker-v2 / ColBERT |
| **Size** | ~80 MB | Varies (Cohere: API, BGE: ~1GB) |

**Why ms-marco-MiniLM-L-6-v2:**
- **Smallest viable cross-encoder**: 80MB, runs on CPU in ~50ms per batch. Larger models (BGE-Reranker-v2-m3, ~1GB) are more accurate but 10x slower on CPU.
- Query-dependent pairwise scoring catches relevance signals that embedding similarity misses. Specifically: a Methods section chunk about "GMM estimation" is more relevant to the query "dynamic panel GMM" than an Introduction chunk that happens to mention "GMM" in passing.
- The overfetch+rerank pattern (fetch 3-5x more candidates, rerank to top-K) is the single biggest retrieval quality improvement per unit of complexity. Every serious RAG system in 2025-2026 uses this pattern.

**Why NOT Cohere Rerank:**
- Requires API key + internet. Excellent quality but violates offline-first principle. Could be added as an optional `RERANKER_MODEL` configuration for users who want cloud quality.

**Why NOT BGE-Reranker-v2:**
- Better quality on multilingual benchmarks, but ~1GB and significantly slower on CPU. Worth considering if ONNX INT8 quantization becomes available for reranker models.

**Trade-off:** MiniLM is English-optimized. Chinese queries benefit less from the reranker than English queries. A multilingual reranker (BGE-Reranker-v2-m3) would be more balanced but heavier. Users can set `RERANKER_MODEL` env var to switch models.

**Decision date:** 2026-07-01 (v0.3.0)

---

## 10. Diversity: MMR (Maximal Marginal Relevance)

| | Our choice | Main alternatives |
|---|---|---|
| **Method** | **Chunk-level MMR** | DPP (Determinantal Point Process) / Clustering-based / None |

**Why MMR:**
- **Simple, tunable, effective**: One parameter (λ) controls the relevance-diversity trade-off. Grid search on 10 queries tuned λ from the standard 0.6 to 0.4 for academic papers — higher diversity than generic text.
- Prevents single-paper dominance: without MMR, a paper with many semantically similar chunks can occupy 7/10 top slots. With MMR (cap=3 chunks/paper, penalty=0.1 per extra chunk), unique paper count in top-10 increased from 4 to 6.
- Computes diversity from existing bge-m3 embeddings (reuses what's already computed). ~15ms overhead.

**Why NOT alternatives:**
- **DPP**: Mathematically elegant diversity model. But requires tuning a kernel matrix, is less intuitive to debug, and adds implementation complexity without clear performance benefit over tuned MMR in the literature.
- **Clustering-based**: Cluster results, pick representative from each cluster. Conceptually similar to MMR but adds clustering algorithm choice as another hyperparameter.

**Trade-off:** MMR can suppress a paper that genuinely deserves multiple top slots (e.g., a survey paper with rich Results section). The hard cap of 3 per paper is a compromise — high enough to allow a strong paper to dominate somewhat, low enough to ensure diversity.

**Decision date:** 2026-07-04, tuned 2026-07-05 (λ 0.6→0.4)

---

## 11. Metadata Store: SQLite

| | Our choice | Main alternatives |
|---|---|---|
| **Database** | **SQLite (papers.db)** | PostgreSQL / ChromaDB metadata only / JSON files |

**Why SQLite:**
- **Zero setup**: The database is created automatically on first sync. No server, no port, no credentials. Matches the ChromaDB "directory on disk" philosophy.
- WAL mode for concurrent reads during writes. Foreign keys ON for referential integrity.
- 7 normalized tables separate concerns: papers, sections, chunks_meta, figures, table_records, chunk_figure_refs, chunk_table_refs. This is a proper relational schema, not a flat metadata dump.
- The `enrich()` method batch-joins papers+sections via a single SQL query — returns paper_abstract, section_heading, section_type for all retrieved chunks in one round trip.
- Python 3.11+ ships SQLite 3.35+ with `ALTER TABLE DROP COLUMN` support, so schema migrations are straightforward.

**Why NOT alternatives:**
- **PostgreSQL**: Industrial-grade but requires installation and management. Violates zero-setup design goal.
- **ChromaDB metadata only**: ChromaDB supports metadata filtering, but metadata values must be scalars (strings, ints, floats) — no nested structures, no JOINs. Storing paper abstracts, section hierarchies, and cross-reference data in ChromaDB metadata would be impossible.
- **JSON files**: Simple but no query capability beyond full-scan. The `enrich()` JOIN would require loading the entire JSON structure into memory.

**Trade-off:** SQLite is single-writer. Our indexing is serial (under sync_lock), so this is not a bottleneck. If we ever move to fully parallel indexing, we'd need WAL queue management.

**Decision date:** 2026-07-01 (v0.3.0)

---

## 12. Output Format: Dual-Format (JSON + Markdown context_block)

| | Our choice | Main alternatives |
|---|---|---|
| **Format** | **JSON items + context_block string** | Pure JSON / Pure Markdown / OpenAI-style annotations |
| **Primary consumer** | LLM reads context_block, uses items for lookups | |

**Why dual-format:**
- **Anthropic MCP Best Practice**: Tools returning data should default to Markdown for LLM consumption, with JSON as a secondary programmatic channel.
- Blockquote (`>`) for evidence text — LLM attention weights are highest for blockquote in training data.
- Star ratings (★★★) instead of float scores (0.0321) — LLMs have no intuition for small floats but immediately understand "this is the most relevant result."
- Sentence-boundary truncation ensures the LLM never sees half a sentence.
- ~50% token savings for the context_block alone vs equivalent JSON (1,186 vs 2,351 tokens for 8 results in our benchmark).

**Why NOT pure Markdown:**
- Loses structured metadata for programmatic consumers (logging, stats, future tool chaining).
- The `items` array allows the LLM to do `item.key` lookups without parsing Markdown headings.

**Why NOT OpenAI-style annotations:**  
OpenAI's file_search returns `annotations` with byte offsets into source documents. This requires the LLM to have the full document in context. Our context_block uses inline blockquotes instead — self-contained, no cross-referencing needed.

**Trade-off:** The dual format has ~4% token overhead vs pure JSON (items + context_block are partially redundant). Accepted for now; a future `response_format` parameter could let users choose "markdown" / "json" / "both".

**Decision date:** 2026-07-10

---

## 13. MCP Framework: FastMCP

| | Our choice | Main alternatives |
|---|---|---|
| **Framework** | **FastMCP** | Raw MCP SDK / mcp-use / custom stdio server |

**Why FastMCP:**
- Decorator-based tool registration: `@mcp.tool()` is cleaner than manual JSON schema construction.
- Built-in lifespan management for startup/sync hooks.
- Active development (2025-2026), good documentation.
- Handles stdio transport correctly (the standard MCP transport for desktop clients).

**Alternative would work, but:** Raw MCP SDK requires manual schema construction for each tool. FastMCP's decorator pattern is a strict improvement for a project with 36 tools.

**Decision date:** 2026-06-10 (v0.2.0)

---

## 14. BM25 Library: rank_bm25

| | Our choice | Main alternatives |
|---|---|---|
| **Library** | **rank_bm25 (BM25Okapi)** | Elasticsearch / Whoosh / custom BM25 |

**Why rank_bm25:**
- Pure Python, zero system dependencies. Installable via pip alongside all other deps.
- BM25Okapi is the standard implementation — well-tested, well-understood.
- Accepts tokenized documents, giving us full control over the CJK+EN tokenizer.

**Why NOT Elasticsearch:** Requires Java + service management. Exists in a different universe of operational complexity.
**Why NOT Whoosh:** Pure Python but unmaintained since 2016. BM25 implementation is older and less accurate.

**Decision date:** 2026-07-13

---

## 15. Tables & Figures: Caption-Anchored (Not Structured)

| | Our choice | Main alternatives |
|---|---|---|
| **Method** | **Caption extraction + raw block text** | docling / open-parse / Table Transformer |

**Why caption-anchored:**
This was the hardest decision and is documented explicitly because we chose NOT to do structured table extraction — a counterintuitive choice for a research paper tool.

- **Academic tables are borderless**: Unlike web tables or spreadsheet exports, academic tables rarely have cell borders. PyMuPDF's table detection relies on geometric line detection, which produces garbage on borderless tables.
- **Multi-column prose mis-segmentation**: Two-column PDF layouts are frequently mis-detected as tables by geometric parsers, producing completely wrong output.
- **Table Transformer (Microsoft)**: ML-based detection improves on geometric methods but requires GPU inference and still produces structured output that needs extensive post-processing.
- **The LLM doesn't need structured cells**: When the LLM answers "What does Table 3 show?", a raw text block of the table's content + its caption is sufficient. The LLM can interpret the values without needing a pandas DataFrame.

**Trade-off:** Users who need true structured table extraction (e.g., exporting data to CSV, running statistical analysis on table values) are not served. These users are pointed to docling / open-parse / unstructured as separate tools.

**When to revisit:** If docling or unstructured mature to the point of reliable, lightweight, borderless table extraction without Docker/GPU requirements.

**Decision date:** 2026-06-11 (v0.2.0)

---

## 16. Chunk Contextual Enrichment: Metadata Prefix (Not LLM Summarization)

| | Our choice | Main alternatives |
|---|---|---|
| **Method** | **[Keywords:] [Title:] [Section:] prefix** | LLM-generated chunk context (Anthropic full method) / No enrichment |

**Why metadata prefix:**
- Anthropic's 2024 "Contextual Retrieval" technique prepends LLM-generated context to each chunk before embedding. They measured 49% failure reduction with LLM context, and noted that even simple metadata prefixing captures a significant portion of the benefit.
- Our implementation uses EXISTING metadata (title, year, section heading, keywords) — zero additional cost, as opposed to Anthropic's full method which requires one LLM call per chunk (~$1/million doc tokens).
- The keyword layer adds academic-paper-specific value: author-assigned keywords are expert-curated topic signals that dramatically improve retrieval precision. This is unique to our domain.
- Validated in our pipeline: the enriched text flows through to both dense (bge-m3 embedding) and sparse (BM25 tokenization) retrieval simultaneously.

**Why NOT full Anthropic method (LLM chunk context):**
- Requires an LLM call per chunk at index time. For 500 chunks across 20 papers: 500 LLM calls. This adds cost, latency, and an API dependency.
- The marginal benefit over metadata prefixing (keyword+title+section) is likely small for academic papers, where the metadata IS the context.
- Could be added as an optional enhancement (`ZRA_LLM_CHUNK_CONTEXT=true`) for users who want maximum quality.

**Decision date:** 2026-07-10 (contextual enrichment), 2026-07-13 (keyword layer)

---

## Summary: Design Philosophy

Every decision above follows a consistent philosophy:

1. **Zero-config first**: The default path works without any .env file, API keys, Docker containers, or external services. The user installs via pip and starts searching.

2. **CPU-only desktop**: All computation runs on consumer laptop CPUs. No GPU required or assumed. Model choices (ONNX INT8, MiniLM reranker, bge-m3) are all CPU-viable.

3. **Offline-capable**: Core retrieval (search, read) never calls external APIs. Online features (search_online_literature, find_related_literature) are separate tools that the LLM explicitly chooses.

4. **Boring technology**: SQLite, BM25, MMR, RRF, pickle — proven, well-understood components. "Novel" only where the domain (CN/EN academic papers) demands it (CJK chunking, bilingual query expansion, keyword-enriched embedding).

5. **The LLM is the consumer, not the engine**: Our job is to find the right chunks and present them clearly. The LLM (Claude/GPT/etc.) does the thinking. We focus on retrieval quality, not agentic reasoning.
