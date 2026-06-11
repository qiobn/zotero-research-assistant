"""ML-based table extraction using Microsoft Table Transformer (TATR).

Opt-in engine (enabled via ZRA_TABLE_MODE=ml). Unlike text-alignment
heuristics — which shred borderless/three-line academic tables and misread
multi-column reference lists — TATR's trained models reliably locate tables
and recover their row/column structure, then we fill cells from the PDF's
own text layer (no OCR) and render Markdown.

Requires the optional `[tables]` extra: torchvision, timm, pillow (torch and
transformers come in via the core embedding stack). Models (~230MB total) are
downloaded once from HuggingFace and cached.

Notes / deployment gotchas handled here:
- The structure model's config ships `dilation: null`, which newer
  transformers reject with a strict-dataclass error; we patch the config dict
  before instantiating.
- The detection repo has no preprocessor config, so we use a plain
  DetrImageProcessor for both models.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass

import pymupdf
from loguru import logger

from research_core.parsers.pdf import TableData

_DETECTION_MODEL = "microsoft/table-transformer-detection"
_STRUCTURE_MODEL = "microsoft/table-transformer-structure-recognition-v1.1-all"

_RENDER_DPI = 200
_SCALE = _RENDER_DPI / 72.0
_DETECT_THRESHOLD = 0.85
_STRUCTURE_THRESHOLD = 0.5
_CROP_PAD_PX = 12

# False-positive guard thresholds: prose blocks misdetected as tables have few
# columns of long paragraph cells; real tables have many short cells.
_MAX_MEDIAN_CELL_CHARS = 60
_MAX_ANY_CELL_CHARS = 600
_MIN_ROWS = 3
_MIN_COLS = 2

_models = None
_models_lock = threading.Lock()


@dataclass
class MLTable:
    """A table located by TATR, with its page-space bbox for prose stripping."""

    page_num: int
    bbox: tuple[float, float, float, float]  # PDF coordinates
    data: TableData


def is_available() -> bool:
    """True if the optional ML table dependencies are importable."""
    try:
        import timm  # noqa: F401
        import torch  # noqa: F401
        import torchvision  # noqa: F401
        from PIL import Image  # noqa: F401
        from transformers import (  # noqa: F401
            DetrImageProcessor,
            TableTransformerForObjectDetection,
        )
    except Exception:
        return False
    return True


def _resolve_device() -> str:
    override = os.getenv("EMBEDDING_DEVICE", "").strip()
    if override:
        return override
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _load_models():
    """Lazy-load and cache (detector, det_proc, structurer, str_proc, device)."""
    global _models
    if _models is not None:
        return _models
    with _models_lock:
        if _models is not None:
            return _models
        from transformers import (
            DetrImageProcessor,
            TableTransformerConfig,
            TableTransformerForObjectDetection,
        )

        device = _resolve_device()
        logger.info(f"Loading Table Transformer models (device={device})")
        proc = DetrImageProcessor()
        detector = TableTransformerForObjectDetection.from_pretrained(
            _DETECTION_MODEL
        ).to(device).eval()
        structurer = _from_pretrained_patched(
            TableTransformerForObjectDetection, TableTransformerConfig, _STRUCTURE_MODEL
        ).to(device).eval()
        _models = (detector, proc, structurer, proc, device)
        logger.info("Table Transformer models loaded")
    return _models


def _from_pretrained_patched(model_cls, config_cls, name):
    """Load a model whose published config has `dilation: null` (rejected by
    strict config validation in newer transformers)."""
    from huggingface_hub import hf_hub_download

    cfg_path = hf_hub_download(name, "config.json")
    with open(cfg_path) as f:
        cfg_dict = json.load(f)
    if cfg_dict.get("dilation") is None:
        cfg_dict["dilation"] = False
    backbone = cfg_dict.get("backbone_config")
    if isinstance(backbone, dict) and backbone.get("dilation") is None:
        backbone["dilation"] = False
    cfg = config_cls(**cfg_dict)
    return model_cls.from_pretrained(name, config=cfg)


def extract_tables(path: str) -> list[MLTable]:
    """Detect and structure all tables in a PDF. Returns [] if unavailable."""
    if not is_available():
        logger.warning(
            "ZRA_TABLE_MODE=ml but optional deps are missing; "
            "install with: pip install 'zotero-research-assistant[tables]'"
        )
        return []
    try:
        import torch
        from PIL import Image
    except Exception:
        return []

    detector, det_proc, structurer, str_proc, device = _load_models()
    tables: list[MLTable] = []

    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc):
            page_num = i + 1
            words = page.get_text("words")
            if not words:
                continue
            image = _render(page, Image)
            boxes = _detect(detector, det_proc, image, _DETECT_THRESHOLD, device, torch)
            for label, _score, box_px in boxes:
                if label != "table":
                    continue
                built = _structure_table(
                    page, words, box_px, structurer, str_proc, device, torch, Image
                )
                if built is None:
                    continue
                columns, rows, pdf_bbox = built
                if not _looks_like_table(columns, rows):
                    continue
                caption = _find_caption_near(page, pdf_bbox)
                tables.append(MLTable(
                    page_num=page_num,
                    bbox=pdf_bbox,
                    data=TableData(
                        page_num=page_num,
                        caption=caption,
                        columns=columns,
                        rows=rows,
                    ),
                ))
    return _dedup(tables)


def _render(page, Image):
    pix = page.get_pixmap(matrix=pymupdf.Matrix(_SCALE, _SCALE))
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _detect(model, proc, image, threshold, device, torch):
    with torch.no_grad():
        inputs = proc(images=image, return_tensors="pt").to(device)
        outputs = model(**inputs)
    sizes = torch.tensor([image.size[::-1]]).to(device)
    res = proc.post_process_object_detection(
        outputs, threshold=threshold, target_sizes=sizes
    )[0]
    id2label = model.config.id2label
    out = []
    for s, l, b in zip(res["scores"], res["labels"], res["boxes"], strict=True):
        out.append((id2label[l.item()], s.item(), [float(x) for x in b.tolist()]))
    return out


def _structure_table(page, words, box_px, model, proc, device, torch, Image):
    pad = _CROP_PAD_PX
    crop = (max(box_px[0] - pad, 0), max(box_px[1] - pad, 0),
            box_px[2] + pad, box_px[3] + pad)
    image = _render(page, Image).crop(crop)
    objs = _detect(model, proc, image, _STRUCTURE_THRESHOLD, device, torch)
    rows = sorted((b for lbl, _s, b in objs if lbl == "table row"), key=lambda b: b[1])
    cols = sorted((b for lbl, _s, b in objs if lbl == "table column"), key=lambda b: b[0])
    headers = [b for lbl, _s, b in objs if lbl == "table column header"]
    if len(rows) < _MIN_ROWS or len(cols) < _MIN_COLS:
        return None

    ox, oy = crop[0], crop[1]

    def to_pdf(b):
        return ((b[0] + ox) / _SCALE, (b[1] + oy) / _SCALE,
                (b[2] + ox) / _SCALE, (b[3] + oy) / _SCALE)

    header_bottom = None
    if headers:
        header_bottom = to_pdf(max(headers, key=lambda b: b[3] - b[1]))[3]

    grid: list[tuple[float, list[str]]] = []
    for r in rows:
        rp = to_pdf(r)
        cells = [_words_in(words, (to_pdf(c)[0], rp[1], to_pdf(c)[2], rp[3])) for c in cols]
        grid.append((rp[1], cells))

    grid.sort(key=lambda g: g[0])
    header_idx = 0
    if header_bottom is not None:
        for idx, (ytop, _) in enumerate(grid):
            if ytop < header_bottom:
                header_idx = idx
    columns = [c or f"col_{j+1}" for j, c in enumerate(grid[header_idx][1])]
    data_rows = [cells for _y, cells in grid[header_idx + 1:] if any(cells)]

    table_rect = to_pdf(box_px)
    return columns, data_rows, table_rect


def _words_in(words, rect) -> str:
    x_lo, y_lo, x_hi, y_hi = rect
    got = []
    for (x0, y0, x1, y1, w, *_rest) in words:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if x_lo <= cx <= x_hi and y_lo <= cy <= y_hi:
            got.append((round(y0, 1), x0, w))
    got.sort()
    return " ".join(w for _y, _x, w in got)


def _looks_like_table(columns: list[str], rows: list[list[str]]) -> bool:
    """Reject prose blocks misdetected as tables (few columns, long cells)."""
    if len(columns) < _MIN_COLS or len(rows) < _MIN_ROWS:
        return False
    cell_lens = [len(c) for r in rows for c in r if c]
    if not cell_lens:
        return False
    cell_lens.sort()
    median = cell_lens[len(cell_lens) // 2]
    if median > _MAX_MEDIAN_CELL_CHARS:
        return False
    if max(cell_lens) > _MAX_ANY_CELL_CHARS and len(columns) < 3:
        return False
    return True


def _find_caption_near(page, table_rect) -> str:
    from research_core.parsers.pdf import _find_caption

    return _find_caption(page, pymupdf.Rect(table_rect))


def _dedup(tables: list[MLTable]) -> list[MLTable]:
    """Drop near-duplicate detections (overlapping bboxes on the same page)."""
    kept: list[MLTable] = []
    for t in tables:
        rect = pymupdf.Rect(t.bbox)
        dup = False
        for k in kept:
            if k.page_num != t.page_num:
                continue
            inter = rect & pymupdf.Rect(k.bbox)
            if inter and inter.get_area() > 0.6 * min(rect.get_area(), pymupdf.Rect(k.bbox).get_area()):
                dup = True
                break
        if not dup:
            kept.append(t)
    return kept
