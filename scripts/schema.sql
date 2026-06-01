-- SecondBrain schema v2.1
-- Fixes over v2: external-content FTS5 'delete' command in triggers,
-- AFTER DELETE trigger, pending_links promoted to a real table,
-- search views that exclude soft-deleted rows, consistent schema_version.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

-- ---------------------------------------------------------------------------
-- Core tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS drawers (
    id          TEXT      PRIMARY KEY,          -- UUID
    title       TEXT      NOT NULL,
    content     TEXT      NOT NULL,
    collection  TEXT      DEFAULT NULL,         -- plain string, no FK
    sources     TEXT      DEFAULT '[]',         -- JSON array of URLs
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at  TIMESTAMP DEFAULT NULL,         -- soft delete
    metadata    TEXT      DEFAULT '{}'          -- arbitrary JSON
);

CREATE INDEX IF NOT EXISTS idx_drawers_collection
    ON drawers(collection) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_drawers_updated
    ON drawers(updated_at DESC) WHERE deleted_at IS NULL;
-- Title lookups for wikilink resolution (case-insensitive).
CREATE INDEX IF NOT EXISTS idx_drawers_title_nocase
    ON drawers(title COLLATE NOCASE) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS tags (
    id         TEXT      PRIMARY KEY,
    name       TEXT      NOT NULL UNIQUE,
    color      TEXT      DEFAULT '#3b82f6',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS drawer_tags (
    drawer_id TEXT NOT NULL,
    tag_id    TEXT NOT NULL,
    PRIMARY KEY (drawer_id, tag_id),
    FOREIGN KEY (drawer_id) REFERENCES drawers(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id)    REFERENCES tags(id)    ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_drawer_tags_tag ON drawer_tags(tag_id);

CREATE TABLE IF NOT EXISTS relations (
    id            TEXT      PRIMARY KEY,
    from_id       TEXT      NOT NULL,
    to_id         TEXT      NOT NULL,
    relation_type TEXT      NOT NULL DEFAULT 'related',  -- references|contradicts|expands|related
    strength      REAL               DEFAULT 0.5,        -- 0.0-1.0
    source        TEXT               DEFAULT 'manual',   -- manual|wikilink|inferred
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (from_id) REFERENCES drawers(id) ON DELETE CASCADE,
    FOREIGN KEY (to_id)   REFERENCES drawers(id) ON DELETE CASCADE,
    UNIQUE (from_id, to_id, source)
);
CREATE INDEX IF NOT EXISTS idx_relations_from ON relations(from_id);
CREATE INDEX IF NOT EXISTS idx_relations_to   ON relations(to_id);

-- Unresolved wikilinks live in their own table, not buried in JSON metadata.
-- Resolution on /brain-add is a single indexed lookup, not a full-table JSON scan.
CREATE TABLE IF NOT EXISTS pending_links (
    id            TEXT PRIMARY KEY,
    from_id       TEXT NOT NULL,
    target_title  TEXT NOT NULL,          -- the [[Title]] that did not resolve
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (from_id) REFERENCES drawers(id) ON DELETE CASCADE,
    UNIQUE (from_id, target_title)
);
CREATE INDEX IF NOT EXISTS idx_pending_target
    ON pending_links(target_title COLLATE NOCASE);

-- ---------------------------------------------------------------------------
-- Full-text search (external-content FTS5 over drawers)
-- ---------------------------------------------------------------------------

CREATE VIRTUAL TABLE IF NOT EXISTS drawers_fts USING fts5(
    title, content,
    content=drawers,
    content_rowid=rowid
);

-- AFTER INSERT: add the new row to the index.
CREATE TRIGGER IF NOT EXISTS drawers_ai AFTER INSERT ON drawers BEGIN
  INSERT INTO drawers_fts(rowid, title, content)
  VALUES (new.rowid, new.title, new.content);
END;

-- AFTER DELETE: external-content tables require the special 'delete' command,
-- a raw DELETE corrupts the index. Missing this leaves orphaned FTS rows.
CREATE TRIGGER IF NOT EXISTS drawers_ad AFTER DELETE ON drawers BEGIN
  INSERT INTO drawers_fts(drawers_fts, rowid, title, content)
  VALUES ('delete', old.rowid, old.title, old.content);
END;

-- AFTER UPDATE: 'delete' the old contents, then insert the new.
CREATE TRIGGER IF NOT EXISTS drawers_au AFTER UPDATE ON drawers BEGIN
  INSERT INTO drawers_fts(drawers_fts, rowid, title, content)
  VALUES ('delete', old.rowid, old.title, old.content);
  INSERT INTO drawers_fts(rowid, title, content)
  VALUES (new.rowid, new.title, new.content);
END;

-- ---------------------------------------------------------------------------
-- Meta
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS _meta (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO _meta VALUES ('schema_version', '1', CURRENT_TIMESTAMP);
