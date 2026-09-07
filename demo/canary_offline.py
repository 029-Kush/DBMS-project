"""
DB-free reproduction of build_canary.py + the README sanity check.

pgvector isn't required to show that the canary logic is sound: this runs
the same 12 probe queries with the same fitted model, does the cosine
top-k in numpy instead of via `ORDER BY embedding <=> ...`, and prints
the top-10 hits per query plus the category-match rate the README quotes
("119/120 canary results share their query's category").

Run:
    python canary_offline.py
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
PROJ = next(p for p in (HERE.parent / "selfheal_pgvector", HERE.parent)
            if (p / "data" / "corpus.csv").exists())
sys.path.insert(0, str(PROJ / "scripts"))
from embed_model import TfidfSvdEmbedder  # noqa: E402

# same probe list as scripts/build_canary.py
PROBES = [
    ("technology", "a new AI model that reduces computing costs"),
    ("technology", "a smartphone update that improves battery performance"),
    ("sports",     "a team winning a championship match"),
    ("sports",     "an athlete setting a new record"),
    ("finance",    "the central bank changing interest rates"),
    ("finance",    "a stock price reacting to earnings"),
    ("health",     "a clinical trial for a new treatment"),
    ("health",     "a study on reducing hospital readmissions"),
    ("food",       "a restaurant launching a new seasonal dish"),
    ("food",       "a bakery using locally sourced ingredients"),
    ("travel",     "a new flight route between two cities"),
    ("travel",     "a resort becoming popular with tourists"),
]
TOP_K = 10

df = pd.read_csv(PROJ / "data" / "corpus.csv")
model = TfidfSvdEmbedder.load(PROJ / "data" / "embed_model.pkl")
X = model.embed(df["body"].tolist())          # (480, 64), unit-normalised
ids = df["id"].to_numpy()
cats = df["category"].to_numpy()

total_hits = 0
total = 0
print(f"{'query category':12s} | {'top-10 doc ids':40s} | same-cat")
print("-" * 78)
for want_cat, q in PROBES:
    qv = model.embed([q])[0]
    sims = X @ qv                              # cosine (all unit vectors)
    top = np.argsort(-sims)[:TOP_K]
    hit = int(np.sum(cats[top] == want_cat))
    total_hits += hit
    total += TOP_K
    id_str = ",".join(map(str, ids[top].tolist()))
    print(f"{want_cat:12s} | {id_str:40s} | {hit}/{TOP_K}")

print("-" * 78)
print(f"category match: {total_hits}/{total} "
      f"({100*total_hits/total:.1f}%)   -- README quotes 119/120")
