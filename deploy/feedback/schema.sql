-- sasonica.com/feedback: feedback from installs, and the answers to it.
-- Apply: npx wrangler d1 execute sasonica-feedback --remote --file schema.sql
CREATE TABLE IF NOT EXISTS installs (
  install TEXT PRIMARY KEY,       -- base32(sha256(pub)[:16]), as /r/ uses
  pub TEXT NOT NULL,              -- the key its first post showed (trust on first use)
  first_at INTEGER NOT NULL,
  last_ts REAL NOT NULL DEFAULT 0, -- the newest signed ts seen: a replay is refused
  blocked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  install TEXT NOT NULL,
  at INTEGER NOT NULL,
  entry TEXT NOT NULL DEFAULT '', -- the What's new entry it answers, if any
  text TEXT NOT NULL,
  app TEXT NOT NULL DEFAULT '',
  server TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS posts_install ON posts (install, at);
CREATE TABLE IF NOT EXISTS answers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  install TEXT NOT NULL,
  post_id INTEGER,
  at INTEGER NOT NULL,
  text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS answers_install ON answers (install, id);
