"""Sentence embeddings for the semantic search: multilingual-e5-small, int8 ONNX, on CPU.

The model (~120 MB) is downloaded once by the post-live processing, never by a web request: when it
is missing, `get()` answers None and the search keeps its word and verse matches. Vectors are
L2-normalised float32, so a dot product is the cosine.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import numpy as np

from .. import paths
from ..i18n import _
from .models import EMBEDDING_DIM

REPO = "Xenova/multilingual-e5-small"
FILES = ("onnx/model_quantized.onnx", "tokenizer.json")
MODEL_ENV = "SADA_EMBED_MODEL_DIR"
DISABLE_ENV = "SADA_SEMANTIC_SEARCH"  # "0": semantic search off (no model loaded, nothing computed)
MAX_TOKENS = 256  # a transcript segment is a sentence or two: longer input only costs time
BATCH = 8  # small batches: the activations of a batch of 32 x 256 tokens cost ~250 MB more
DIM = EMBEDDING_DIM

_lock = threading.Lock()
_model: Embedder | None = None


def enabled() -> bool:
    return os.environ.get(DISABLE_ENV, "1").strip() not in ("0", "false", "no", "off")


def model_dir() -> Path:
    return Path(os.environ.get(MODEL_ENV) or paths.DATA / "models" / "multilingual-e5-small")


def installed() -> bool:
    d = model_dir()
    return all((d / f).is_file() for f in FILES)


def download(log=print) -> bool:
    """Fetch the model files into `model_dir()`. False when the network or the hub is unavailable."""
    if installed():
        return True
    try:
        from huggingface_hub import hf_hub_download

        for f in FILES:
            hf_hub_download(REPO, f, local_dir=model_dir())
    except Exception as e:  # noqa: BLE001 — no model: the search keeps words and verses
        log(_("embedding model unavailable: {error}", error=e))
        return False
    return installed()


class Embedder:
    def __init__(self, directory: Path, threads: int = 1):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads  # one core: the live and the web requests keep the rest
        opts.inter_op_num_threads = 1
        opts.enable_cpu_mem_arena = False  # the arena keeps the peak of the largest batch for good
        opts.enable_mem_pattern = False
        self.session = ort.InferenceSession(
            str(directory / FILES[0]), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(str(directory / FILES[1]))
        self.tokenizer.enable_truncation(MAX_TOKENS)
        self.tokenizer.enable_padding()

    def encode(self, texts: list[str], kind: str = "passage") -> np.ndarray:
        """`kind`: "passage" for indexed text, "query" for what the user typed (e5 prefixes)."""
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        order = sorted(range(len(texts)), key=lambda j: len(texts[j]))  # similar lengths: less padding
        for i in range(0, len(texts), BATCH):
            idx = order[i : i + BATCH]
            chunk = [f"{kind}: {texts[j]}" for j in idx]
            enc = self.tokenizer.encode_batch(chunk)
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run(None, feed)[0]  # (batch, tokens, dim)
            m = mask[..., None].astype(np.float32)
            pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
            pooled /= np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
            out[idx] = pooled
        return out


def get() -> Embedder | None:
    """The loaded model, or None (disabled, not downloaded, or failing to load)."""
    global _model
    if not enabled():
        return None
    with _lock:
        if _model is None and installed():
            try:
                _model = Embedder(model_dir())
            except Exception as e:  # noqa: BLE001
                print(_("embedding model failed to load: {error}", error=e), flush=True)
                return None
        return _model


def to_bytes(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def from_bytes(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)
