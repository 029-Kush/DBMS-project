-- Entities get an external id (T1055.012, CWE-79, CVE-...) and typed facts; add typed links between entities.
ALTER TABLE entities ADD COLUMN IF NOT EXISTS ext_id text NOT NULL DEFAULT '';
ALTER TABLE entities ADD COLUMN IF NOT EXISTS facts  jsonb NOT NULL DEFAULT '{}';
ALTER TABLE entities DROP CONSTRAINT IF EXISTS entities_type_name_key;
CREATE UNIQUE INDEX IF NOT EXISTS entities_type_name_ext_key ON entities (type, name, ext_id);
CREATE INDEX IF NOT EXISTS entities_ext_idx ON entities (upper(ext_id)) WHERE ext_id <> '';

CREATE TABLE IF NOT EXISTS entity_links (
    src  int NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    dst  int NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    rel  text NOT NULL,             -- uses | mitigates | detects | subtechnique_of | in_tactic | has_weakness | same_as ...
    note text,
    source text NOT NULL DEFAULT '',
    PRIMARY KEY (src, dst, rel)
);
CREATE INDEX IF NOT EXISTS entity_links_dst_idx ON entity_links (dst, rel);
