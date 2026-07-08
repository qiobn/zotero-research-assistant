"""End-to-end retrieval comparison: FP32 vs INT8 ONNX embeddings.

Builds two ChromaDB collections with the same chunks but different embeddings,
then runs the full search pipeline (hybrid search + Cross-Encoder + MMR) and
compares top-10 PaperHit overlap.
"""
import os, time, numpy as np, random, shutil

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["ZOTERO_LOCAL"] = "true"

from research_core.rag.retriever import Retriever
from research_core.rag.store import get_collection
from research_core.rag.embedding import get_embedding_function
from research_core.zotero.client import ZoteroClient
from research_core.tools.search import search_papers
import chromadb

# ── 1. Get chunks from 8 papers ──
retriever = Retriever()
indexed = list(retriever.list_indexed_items())
sample_keys = random.sample(indexed, min(8, len(indexed)))
print(f"Papers: {len(sample_keys)}")

all_chunks = []
all_ids = []
all_metas = []
all_titles = {}
for k in sample_keys:
    for r in retriever.get_item_chunks(k):
        if r.text.strip():
            cid = f"{k}:{r.chunk_idx}"
            all_chunks.append(r.text)
            all_ids.append(cid)
            all_metas.append({
                "item_key": k, "title": r.title or k, "year": r.metadata.get("year", 0),
                "page_start": r.page_start, "page_end": r.page_end,
                "chunk_idx": r.chunk_idx, "section": r.metadata.get("section", "content"),
            })
            all_titles[k] = r.title or k
print(f"Chunks: {len(all_chunks)}")

# ── 2. FP32 embeddings ──
print("\n--- FP32 ---")
ef = get_embedding_function()
ef._load()
t0 = time.time()
embeddings_fp32 = ef(all_chunks)
t_fp32 = time.time() - t0

# ── 3. INT8 embeddings ──
print("--- INT8 ONNX ---")
from transformers import AutoTokenizer
import onnxruntime as ort

model_path = os.path.expanduser(
    "~/.cache/huggingface/hub/models--skatzR--USER-BGE-M3-ONNX-INT8/"
    "snapshots/4c89cf5351b21965791ef223c6d2f8d045474be0"
)
tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
session = ort.InferenceSession(
    f"{model_path}/model_quantized.onnx", providers=["CPUExecutionProvider"]
)

t0 = time.time()
embeddings_int8 = []
for i in range(0, len(all_chunks), 64):
    batch = all_chunks[i : i + 64]
    enc = tokenizer(batch, padding=True, truncation=True, max_length=1024, return_tensors="np")
    out = session.run(
        None,
        {
            "input_ids": enc["input_ids"].astype(np.int64),
            "attention_mask": enc["attention_mask"].astype(np.int64),
        },
    )
    cls = out[0][:, 0, :]
    cls = cls / np.linalg.norm(cls, axis=1, keepdims=True)
    embeddings_int8.extend(cls.tolist())
t_int8 = time.time() - t0
print(f"Speedup: {t_fp32 / t_int8:.1f}x")

# ── 4. Build two ChromaDB collections with manual embeddings ──
persist = ".chroma_db"
client = chromadb.PersistentClient(path=persist)

coll_fp32_name = "bench_fp32"
coll_int8_name = "bench_int8"
for name in [coll_fp32_name, coll_int8_name]:
    try:
        client.delete_collection(name)
    except Exception:
        pass

coll_fp32 = client.create_collection(
    name=coll_fp32_name,
    metadata={"hnsw:space": "cosine"},
    embedding_function=ef,  # reuse cached model, won't be called (we pass embeddings=)
)
coll_int8 = client.create_collection(
    name=coll_int8_name,
    metadata={"hnsw:space": "cosine"},
    embedding_function=ef,  # same — won't be called, just prevents default model download
)

# Upsert FP32
coll_fp32.upsert(ids=all_ids, documents=all_chunks, metadatas=all_metas, embeddings=embeddings_fp32)
# Upsert INT8
coll_int8.upsert(ids=all_ids, documents=all_chunks, metadatas=all_metas, embeddings=embeddings_int8)

# ── 5. Run search_papers through the full pipeline ──
zot = ZoteroClient()

queries = [
    "urban accessibility public services",
    "walkability health elderly community",
    "transportation behavior spatial analysis",
    "machine learning urban planning",
    "satisfaction service quality evaluation",
    "carbon emissions environmental impact",
    "housing elderly community facilities",
    "public transit travel behavior",
    "spatial equity accessibility",
    "urban green space walkability",
]

results_fp32 = {}
results_int8 = {}

for q in queries:
    # FP32
    r_fp = Retriever(collection=coll_fp32)
    hits_fp = r_fp.search(q, n_results=15, include_references=True)
    results_fp32[q] = [(h.item_key, round(h.score, 4)) for h in hits_fp[:10]]

    # INT8
    r_int8 = Retriever(collection=coll_int8)
    hits_int8 = r_int8.search(q, n_results=15, include_references=True)
    results_int8[q] = [(h.item_key, round(h.score, 4)) for h in hits_int8[:10]]

# ── 6. Compare PaperHit overlap ──
print(f"\n{'='*60}")
print(f"END-TO-END RETRIEVAL COMPARISON (top-10 papers)")
print(f"{'='*60}")

overlaps = []
for q in queries:
    fp_papers = [k for k, _ in results_fp32[q]]
    int8_papers = [k for k, _ in results_int8[q]]
    fp_set = set(fp_papers)
    int8_set = set(int8_papers)
    overlap = len(fp_set & int8_set) / 10
    overlaps.append(overlap)

    # Show differences
    fp_only = fp_set - int8_set
    int8_only = int8_set - fp_set
    if fp_only or int8_only:
        print(f"\n  Q: {q[:60]}")
        print(f"  Overlap: {len(fp_set & int8_set)}/10 ({overlap:.0%})")
        if fp_only:
            fp_titles = [f"{k[-6:]}" for k in fp_only]
            print(f"  FP32 only: {fp_titles}")
        if int8_only:
            i8_titles = [f"{k[-6:]}" for k in int8_only]
            print(f"  INT8 only: {i8_titles}")
    else:
        print(f"\n  Q: {q[:60]}")
        print(f"  Overlap: 10/10 (100%) — identical top-10")

print(f"\n{'='*60}")
print(f"Summary:")
print(f"  Paper overlap@10:  mean={np.mean(overlaps):.1%}  min={np.min(overlaps):.1%}")
print(f"  FP32 rank correlation (Spearman per query):")

# Spearman on paper rankings (not chunks)
from scipy.stats import spearmanr
for q in queries:
    fp_papers = [k for k, _ in results_fp32[q]]
    int8_papers = [k for k, _ in results_int8[q]]
    # Build rank maps from common paper set
    all_papers = list(dict.fromkeys(fp_papers + int8_papers))
    fp_ranks = [all_papers.index(k) + 1 if k in all_papers else 99 for k in fp_papers[:10]]
    int8_ranks = [all_papers.index(k) + 1 if k in all_papers else 99 for k in int8_papers[:10]]
    # Pad to same length
    max_n = max(len(fp_ranks), len(int8_ranks))
    fp_ranks.extend([99] * (max_n - len(fp_ranks)))
    int8_ranks.extend([99] * (max_n - len(int8_ranks)))
    rho, _ = spearmanr(fp_ranks, int8_ranks)
    print(f"    {q[:50]:50s}  rho={rho:.4f}")

# Cleanup
try:
    client.delete_collection(coll_fp32_name)
    client.delete_collection(coll_int8_name)
except Exception:
    pass
print(f"{'='*60}")
