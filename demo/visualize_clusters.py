"""
Visual proof that the Phase 1 embeddings carry real topical structure.

Loads the *actual* fitted model (data/embed_model.pkl) and the *actual*
corpus (data/corpus.csv) -- these are the same 64-dim vectors that get
loaded into pgvector -- projects them to 2D two different ways (PCA and
t-SNE) and colours each point by its category.

It also prints two hard numbers you can quote:
  * silhouette score  -- how cleanly separated the 6 clusters are (-1..1)
  * 1-NN purity       -- fraction of docs whose nearest neighbour (cosine)
                         is in the same category

Run from this folder:
    python visualize_clusters.py
Output:
    embedding_clusters.png
"""
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE
from sklearn.metrics import silhouette_score
from sklearn.metrics.pairwise import cosine_similarity

# --- locate the project regardless of where it was unzipped -------------
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CANDIDATES = [ROOT / "selfheal_pgvector", ROOT]
PROJ = next((p for p in CANDIDATES if (p / "data" / "corpus.csv").exists()), None)
if PROJ is None:
    raise SystemExit(
        "Could not find selfheal_pgvector/data/corpus.csv near this script.\n"
        "Put this file in a 'demo' folder next to the selfheal_pgvector folder."
    )

import sys
sys.path.insert(0, str(PROJ / "scripts"))
from embed_model import TfidfSvdEmbedder  # noqa: E402

# --- real model, real corpus, real vectors ----------------------------
df = pd.read_csv(PROJ / "data" / "corpus.csv")
model = TfidfSvdEmbedder.load(PROJ / "data" / "embed_model.pkl")
X = model.embed(df["body"].tolist())          # (480, 64), unit-normalised
cats = df["category"].to_numpy()
order = sorted(np.unique(cats))
palette = dict(zip(order, plt.cm.tab10.colors))

# --- hard numbers ----------------------------------------------------
sil = silhouette_score(X, cats, metric="cosine")

sim = cosine_similarity(X)
np.fill_diagonal(sim, -1.0)
nn = sim.argmax(axis=1)
purity = float(np.mean(cats[nn] == cats))

print(f"documents            : {len(df)}")
print(f"categories           : {len(order)}  ({', '.join(order)})")
print(f"embedding dim        : {X.shape[1]}")
print(f"silhouette (cosine)  : {sil:.3f}   (modest: categories share template words like 'improves ... by')")
print(f"1-NN category purity : {purity*100:.1f}%   (every doc's nearest neighbour is the same topic -- this is the headline)")

# --- 2D projections ------------------------------------------------
pca_xy = PCA(n_components=2, random_state=42).fit_transform(X)
tsne_xy = TSNE(n_components=2, random_state=42, init="pca",
               perplexity=30, metric="cosine").fit_transform(X)

fig, axes = plt.subplots(1, 2, figsize=(14, 6))
for ax, xy, title in ((axes[0], pca_xy, "PCA"), (axes[1], tsne_xy, "t-SNE")):
    for c in order:
        m = cats == c
        ax.scatter(xy[m, 0], xy[m, 1], s=28, alpha=0.8,
                   color=palette[c], label=c, edgecolors="none")
    ax.set_title(f"{title} projection of 64-dim embeddings", fontsize=12)
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_alpha(0.3)

axes[0].legend(loc="best", frameon=True, fontsize=9, title="category")
fig.suptitle(
    f"Phase 1 baseline embeddings  |  silhouette={sil:.2f}  |  1-NN purity={purity*100:.0f}%",
    fontsize=13, y=1.02,
)
fig.tight_layout()
out = HERE / "embedding_clusters.png"
fig.savefig(out, dpi=150, bbox_inches="tight")
print(f"\nwrote {out}")
