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
import pickle
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import normalize

from config import EMBED_DIM

MODEL_PATH = Path(__file__).resolve().parent.parent / "data" / "embed_model.pkl"


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
