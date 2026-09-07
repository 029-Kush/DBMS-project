"""
Builds a small multi-category text corpus with real topical structure,
entirely offline (no dataset download). Each category has a pool of
subjects/actions/details that get recombined into varied sentences, so
documents in the same category are lexically similar but not identical,
and documents across categories are clearly separable -- exactly what's
needed to build a canary set with unambiguous expected results.

Output: data/corpus.csv with columns [id, category, body]
"""
import csv
import random
from pathlib import Path

random.seed(42)

TEMPLATES = {
    "technology": {
        "subjects": ["the new smartphone", "the startup's app", "the AI model",
                     "the cloud platform", "the open-source library", "the chip design"],
        "actions": ["improves battery life by", "reduces latency by", "cuts inference cost by",
                    "speeds up builds by", "increases throughput by", "lowers memory usage by"],
        "details": ["20 percent", "a wide margin", "using a new caching layer",
                     "after the latest update", "compared to last year's model", "in benchmark tests"],
    },
    "sports": {
        "subjects": ["the striker", "the home team", "the tennis champion", "the marathon runner",
                     "the rookie point guard", "the national squad"],
        "actions": ["scored in the final minutes to win", "broke the league record for",
                    "was named most valuable player after", "suffered a setback during",
                    "qualified for the finals following", "extended the winning streak with"],
        "details": ["the derby match", "consecutive assists", "a tense semifinal",
                     "the regional qualifiers", "a dominant second half", "a photo finish"],
    },
    "finance": {
        "subjects": ["the central bank", "the tech stock", "the startup's valuation",
                     "the housing market", "the currency", "the pension fund"],
        "actions": ["raised interest rates after", "rallied following", "dropped sharply amid",
                    "stabilized despite", "hit a new high driven by", "faced pressure due to"],
        "details": ["inflation concerns", "strong earnings", "regulatory uncertainty",
                     "a weaker jobs report", "investor optimism", "supply chain disruptions"],
    },
    "health": {
        "subjects": ["the clinical trial", "the new vaccine", "the hospital study",
                     "the wearable device", "the nutrition guideline", "the surgery technique"],
        "actions": ["showed promising results for", "reduced recovery time in",
                    "was approved for use in", "improved early detection of",
                    "cut hospital readmissions for", "lowered risk factors linked to"],
        "details": ["elderly patients", "chronic conditions", "post-operative care",
                     "seasonal outbreaks", "cardiovascular disease", "pediatric cases"],
    },
    "food": {
        "subjects": ["the local bakery", "the food critic", "the restaurant chain",
                     "the home cook", "the farmers market vendor", "the pop-up stand"],
        "actions": ["introduced a seasonal menu featuring", "won an award for",
                    "shared a recipe using", "sold out quickly of",
                    "revived a traditional dish with", "experimented with a fusion of"],
        "details": ["locally sourced vegetables", "a family recipe", "fermented ingredients",
                     "artisan bread", "regional spices", "plant-based alternatives"],
    },
    "travel": {
        "subjects": ["the budget airline", "the coastal town", "the mountain trail",
                     "the boutique hotel", "the train route", "the island resort"],
        "actions": ["added a new route connecting", "saw a surge in visitors after",
                    "reopened following renovations near", "offered discounted packages for",
                    "became popular with hikers due to", "extended service to reach"],
        "details": ["two major cities", "a viral social media post", "the historic district",
                     "the off-season", "scenic overlooks", "a remote archipelago"],
    },
}

DOCS_PER_CATEGORY = 80


def make_sentence(cat):
    t = TEMPLATES[cat]
    subj = random.choice(t["subjects"])
    act = random.choice(t["actions"])
    det = random.choice(t["details"])
    return f"{subj.capitalize()} {act} {det}."


def main():
    rows = []
    doc_id = 1
    for cat in TEMPLATES:
        seen = set()
        while len(seen) < DOCS_PER_CATEGORY:
            s = make_sentence(cat)
            if s in seen:
                continue
            seen.add(s)
            rows.append({"id": doc_id, "category": cat, "body": s})
            doc_id += 1

    out_path = Path(__file__).resolve().parent.parent / "data" / "corpus.csv"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "category", "body"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} documents across {len(TEMPLATES)} categories to {out_path}")


if __name__ == "__main__":
    main()
