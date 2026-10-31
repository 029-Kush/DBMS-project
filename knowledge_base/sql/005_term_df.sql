-- Word document frequencies over bodies, read by search.py (keyword-lane skip rule and OR-keyword term choice).
-- Created empty here so a fresh database can serve searches; scripts/refresh_stats.sh rebuilds it after an ingest.
CREATE TABLE IF NOT EXISTS term_df (word text, ndoc integer);
CREATE UNIQUE INDEX IF NOT EXISTS term_df_word ON term_df(word);
