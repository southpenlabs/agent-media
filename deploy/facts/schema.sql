-- sasonica.com/facts: public facts installs share, the same for everyone.
-- Apply: npx wrangler d1 execute sasonica-facts --remote --file schema.sql
CREATE TABLE IF NOT EXISTS installs (
  install TEXT PRIMARY KEY,       -- base32(sha256(pub)[:16]), as /r/ and /feedback use
  pub TEXT NOT NULL,              -- the key its first report showed (trust on first use)
  first_at INTEGER NOT NULL,
  last_ts REAL NOT NULL DEFAULT 0, -- the newest signed ts seen: a replay is refused
  blocked INTEGER NOT NULL DEFAULT 0
);
-- What each install measured. A length becomes a fact once two installs
-- agree on it, or one trusted install (TRUSTED) reports it.
CREATE TABLE IF NOT EXISTS reports (
  key TEXT NOT NULL,              -- yt:<video id> | audio:<sha256(url)[:32]>
  install TEXT NOT NULL,
  seconds INTEGER NOT NULL,
  at INTEGER NOT NULL,
  PRIMARY KEY (key, install)
);
CREATE INDEX IF NOT EXISTS reports_install ON reports (install, at);
CREATE TABLE IF NOT EXISTS durations (
  key TEXT PRIMARY KEY,
  seconds INTEGER NOT NULL,
  at INTEGER NOT NULL
);
