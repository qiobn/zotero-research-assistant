"""Quick benchmark: FP32 vs INT8 ONNX embedding speed + retrieval quality."""
import os, time, numpy as np, random

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["ZOTERO_LOCAL"] = "true"

from research_core.rag.retriever import Retriever
from research_core.rag.store import get_collection

retriever = Retriever()
indexed = list(retriever.list_indexed_items())

# Get chunks from 8 random papers
sample_keys = random.sample(indexed, min(8, len(indexed)))
all_chunks = []
for k in sample_keys:
    for r in retriever.get_item_chunks(k):
        if r.text.strip():
            all_chunks.append(r.text)
print(f"{len(all_chunks)} chunks from {len(sample_keys)} papers")

# FP32
print("--- FP32 ---")
from sentence_transformers import SentenceTransformer
t0 = time.time()
m = SentenceTransformer("BAAI/bge-m3", device="cpu")
m.max_seq_length = 1024
e_fp32 = m.encode(all_chunks, normalize_embeddings=True, batch_size=64, show_progress_bar=False)
t_fp32 = time.time() - t0
print(f"{t_fp32:.0f}s ({t_fp32/len(all_chunks)*1000:.0f}ms/chunk)")

# INT8
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
embs = []
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
    embs.append(cls)
e_int8 = np.concatenate(embs).astype(np.float32)
t_int8 = time.time() - t0
print(f"{t_int8:.0f}s ({t_int8/len(all_chunks)*1000:.0f}ms/chunk)")

# Per-chunk fidelity
d = min(e_fp32.shape[1], e_int8.shape[1])
chunk_sims = [float(np.dot(e_fp32[i, :d], e_int8[i, :d])) for i in range(len(all_chunks))]

# Rank correlation
from scipy.stats import spearmanr

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

corrs, o10s, o20s = [], [], []
for q in queries:
    e_q_fp32 = m.encode([q], normalize_embeddings=True, show_progress_bar=False)[0]
    enc = tokenizer(q, padding=True, truncation=True, max_length=1024, return_tensors="np")
    out = session.run(
        None,
        {
            "input_ids": enc["input_ids"].astype(np.int64).reshape(1, -1),
            "attention_mask": enc["attention_mask"].astype(np.int64).reshape(1, -1),
        },
    )
    e_q_int8 = out[0][:, 0, :].astype(np.float32)[0]
    e_q_int8 = e_q_int8 / np.linalg.norm(e_q_int8)

    fp32_rank = np.argsort(np.dot(e_fp32, e_q_fp32))[::-1]
    int8_rank = np.argsort(np.dot(e_int8[:, :d], e_q_int8[:d]))[::-1]

    rho, _ = spearmanr(fp32_rank, int8_rank)
    corrs.append(rho)
    o10s.append(len(set(fp32_rank[:10]) & set(int8_rank[:10])) / 10)
    o20s.append(len(set(fp32_rank[:20]) & set(int8_rank[:20])) / 20)

print(f"\n{'='*50}")
print(f"Speedup:       {t_fp32 / t_int8:.1f}x")
print(f"Spearman rho:  mean={np.mean(corrs):.4f}  min={np.min(corrs):.4f}")
print(f"Overlap@10:    mean={np.mean(o10s):.1%}  min={np.min(o10s):.1%}")
print(f"Overlap@20:    mean={np.mean(o20s):.1%}  min={np.min(o20s):.1%}")
print(f"Chunk cos:     mean={np.mean(chunk_sims):.4f}  min={np.min(chunk_sims):.4f}")
print(f"{'='*50}")
