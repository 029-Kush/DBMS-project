-- 2026-10-28: entity_ids() ran a sequential scan of entities on every query (one OR over name / aliases / ext_id).
-- Split into a UNION of three index lookups; ext_id uses the existing upper(ext_id) index. Idempotent.
CREATE INDEX IF NOT EXISTS entities_lower_name_idx ON entities (lower(name));
CREATE INDEX IF NOT EXISTS entities_aliases_gin ON entities USING gin (aliases);
