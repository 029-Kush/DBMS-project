-- Application layer on top of the knowledge-base schema (knowledge_base/sql/schema.sql must be applied first).
-- Idempotent: safe to re-run. Applied by `flask --app kb_web.wsgi init-db`.

-- One users table for every role (RBAC by the role column, checked in the app and constrained here).
CREATE TABLE IF NOT EXISTS users (
    id            serial PRIMARY KEY,
    username      varchar(32)  NOT NULL UNIQUE CHECK (username ~ '^[a-z0-9_]{3,32}$'),
    email         varchar(254) NOT NULL UNIQUE CHECK (position('@' IN email) > 1),
    password_hash varchar(60)  NOT NULL,                -- bcrypt; plain passwords are never stored
    role          varchar(10)  NOT NULL DEFAULT 'viewer' CHECK (role IN ('admin', 'editor', 'viewer')),
    is_active     boolean      NOT NULL DEFAULT true,
    created_at    timestamptz  NOT NULL DEFAULT now(),
    last_login    timestamptz
);

-- A user's saved passages: users M:N chunk_refs, with a note on the association.
CREATE TABLE IF NOT EXISTS bookmarks (
    id         serial PRIMARY KEY,
    user_id    int    NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chunk_id   bigint NOT NULL REFERENCES chunk_refs(id) ON DELETE CASCADE,
    note       varchar(500),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS bookmarks_chunk_idx ON bookmarks (chunk_id);

-- Every search run through the app (dashboard statistics). user_id is kept NULL if the user is deleted.
CREATE TABLE IF NOT EXISTS search_log (
    id         bigserial PRIMARY KEY,
    user_id    int REFERENCES users(id) ON DELETE SET NULL,
    query      varchar(300) NOT NULL,
    n_results  smallint     NOT NULL CHECK (n_results >= 0),
    latency_ms integer      NOT NULL CHECK (latency_ms >= 0),
    created_at timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS search_log_user_idx ON search_log (user_id, created_at DESC);

-- Audit trail written by trigger, so it also records changes made outside the app (psql, scripts).
CREATE TABLE IF NOT EXISTS audit_log (
    id         bigserial PRIMARY KEY,
    table_name text        NOT NULL,
    row_id     text        NOT NULL,
    action     text        NOT NULL CHECK (action IN ('INSERT', 'UPDATE', 'DELETE')),
    actor      text        NOT NULL,     -- app user (SET LOCAL app.user) or the database role
    old_row    jsonb,
    new_row    jsonb,
    changed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_log_time_idx ON audit_log (changed_at DESC);

CREATE OR REPLACE FUNCTION audit_row() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    o jsonb := CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END;
    n jsonb := CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END;
BEGIN
    -- never copy password hashes into the audit trail
    o := o - 'password_hash'; n := n - 'password_hash';
    IF TG_OP = 'UPDATE' AND o = n THEN RETURN NEW; END IF;   -- e.g. a login that only touched the hash column
    INSERT INTO audit_log (table_name, row_id, action, actor, old_row, new_row)
    VALUES (TG_TABLE_NAME, coalesce(n, o) ->> 'id', TG_OP,
            coalesce(nullif(current_setting('app.user', true), ''), current_user), o, n);
    RETURN coalesce(NEW, OLD);
END $$;

DROP TRIGGER IF EXISTS entities_audit ON entities;
CREATE TRIGGER entities_audit AFTER INSERT OR UPDATE OR DELETE ON entities
    FOR EACH ROW EXECUTE FUNCTION audit_row();
DROP TRIGGER IF EXISTS users_audit ON users;
CREATE TRIGGER users_audit AFTER INSERT OR UPDATE OF username, email, role, is_active OR DELETE ON users
    FOR EACH ROW EXECUTE FUNCTION audit_row();

-- Merge a duplicate entity into another in one transaction: its passages, links and aliases move to the target.
-- Called from the admin UI as CALL merge_entities(src, dst).
-- Parameters are p_* because entity_links has columns named src and dst.
DROP PROCEDURE IF EXISTS merge_entities(int, int);
CREATE PROCEDURE merge_entities(p_src int, p_dst int) LANGUAGE plpgsql AS $$
BEGIN
    IF p_src = p_dst THEN RAISE EXCEPTION 'cannot merge an entity into itself'; END IF;
    PERFORM 1 FROM entities WHERE id IN (p_src, p_dst) HAVING count(*) = 2;
    IF NOT FOUND THEN RAISE EXCEPTION 'entity % or % does not exist', p_src, p_dst; END IF;
    UPDATE chunk_refs SET entity_id = p_dst WHERE entity_id = p_src;
    -- links: re-point, dropping any that would duplicate an existing link or become a self-link
    INSERT INTO entity_links (src, dst, rel, note, source)
        SELECT m.s, m.d, m.rel, m.note, m.source
        FROM (SELECT CASE WHEN l.src = p_src THEN p_dst ELSE l.src END AS s,
                     CASE WHEN l.dst = p_src THEN p_dst ELSE l.dst END AS d, l.rel, l.note, l.source
              FROM entity_links l WHERE l.src = p_src OR l.dst = p_src) m
        WHERE m.s <> m.d
        ON CONFLICT DO NOTHING;
    UPDATE entities e SET aliases = ARRAY(SELECT DISTINCT a FROM unnest(
            e.aliases || (SELECT array_append(x.aliases, lower(x.name)) FROM entities x WHERE x.id = p_src)) a ORDER BY 1)
        WHERE e.id = p_dst;
    DELETE FROM entities WHERE id = p_src;   -- cascades the old links
END $$;

-- Per-source corpus statistics (dashboard). A view so the app and psql read the same numbers.
CREATE OR REPLACE VIEW v_source_stats AS
    SELECT r.source,
           count(*)                    AS passages,
           count(DISTINCT r.body_id)   AS distinct_texts,
           count(DISTINCT r.entity_id) AS entities,
           round(avg(length(b.body)))  AS avg_chars
    FROM chunk_refs r JOIN bodies b ON b.id = r.body_id
    GROUP BY r.source;

-- Per-user activity (admin users page): searches, bookmarks, last search.
CREATE OR REPLACE VIEW v_user_activity AS
    SELECT u.id, u.username, u.role, u.is_active, u.created_at, u.last_login,
           (SELECT count(*) FROM search_log s WHERE s.user_id = u.id) AS searches,
           (SELECT count(*) FROM bookmarks  k WHERE k.user_id = u.id) AS bookmarks,
           (SELECT max(created_at) FROM search_log s WHERE s.user_id = u.id) AS last_search
    FROM users u;
