"""
The embedding model for this project.

This environment has no route to a model hub (Hugging Face etc. are not
on the network allowlist), so real transformer weights can't be pulled
down here. Instead this is a small locally-trained TF-IDF + Truncated
SVD model -- a legitimate, fast baseline that still produces meaningful
semantic clusters on the corpus (see generate_corpus.py).

To swap in a real sentence-transformer later on a machine with internet
access, replace embed_texts() below with a call to that model and keep
everything downstream (schema, loader, canary, monitoring) unchanged --
nothing else in the project depends on how the vectors were produced.
"""
import os
import pickle
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import normalize

from config import EMBED_DIM, EMBEDDER

MODEL_PATH = Path(os.environ.get(
    "SELFHEAL_MODEL_PATH",
    Path(__file__).resolve().parent.parent / "data" / "embed_model.pkl",
))


class TfidfSvdEmbedder:
    def __init__(self, dim=EMBED_DIM):
        self.dim = dim
        self.vectorizer = TfidfVectorizer(max_features=5000, stop_words="english")
        self.svd = TruncatedSVD(n_components=dim, random_state=42)

    def fit(self, texts):
        tfidf = self.vectorizer.fit_transform(texts)
        self.svd.fit(tfidf)
        return self

    def embed(self, texts):
        tfidf = self.vectorizer.transform(texts)
        vecs = self.svd.transform(tfidf)
        return normalize(vecs)  # unit-normalize so cosine distance behaves well

    def save(self, path=MODEL_PATH):
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @staticmethod
    def load(path=MODEL_PATH):
        with open(path, "rb") as f:
            return pickle.load(f)


def fit_and_save(texts, dim=EMBED_DIM):
    model = TfidfSvdEmbedder(dim=dim).fit(texts)
    model.save()
    return model


FASTEMBED_MODELS = {
    "bge": "BAAI/bge-small-en-v1.5",
    "minilm": "sentence-transformers/all-MiniLM-L6-v2",
}


class FastEmbedder:
    """A real transformer embedder (ONNX, local) with the same embed() contract."""

    def __init__(self, preset):
        from fastembed import TextEmbedding  # imported lazily: optional dependency

        self.preset = preset
        # 4 threads beat 16 on a shared 16-core box (measured ~53 vs ~26 docs/s)
        self.model = TextEmbedding(FASTEMBED_MODELS[preset],
                                   threads=int(os.environ.get("SELFHEAL_EMBED_THREADS", 4)))

    def embed(self, texts):
        vectors = np.array(list(self.model.embed(list(texts), batch_size=64)), dtype=float)
        return normalize(vectors)


def load_embedder(preset=None):
    """Return the embedder selected by SELFHEAL_EMBEDDER (or the given preset)."""
    preset = preset or EMBEDDER
    if preset == "tfidf":
        return TfidfSvdEmbedder.load()
    return FastEmbedder(preset)
