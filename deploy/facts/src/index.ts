// sasonica.com/facts — public facts installs share, the same for everyone
// (agent-media docs/proposals/2026-11-01-three-data-layers.md, the central
// layer). Facts only, never anything about a person: a YouTube video's or an
// audio URL's length, so a second install does not ask YouTube all over again
// and one with no phone (YouTube refuses servers) still gets lengths.
//
//   POST /facts/v1/durations  {keys: [key, …]}                     unsigned
//                             → {ok, durations: {key: seconds}}
//   POST /facts/v1/places     {keys: [key, …]}                     unsigned
//                             → {ok, places: {key: {country, state, city, region, photo}}}
//   POST /facts/v1/report     {pub, ts, durations?: {key: seconds},
//                              places?: {key: place}}               signed
//                             → {ok, install, accepted}
//
// A length's key is `yt:<video id>` or `audio:<sha256(url) hex, first 32>`; a
// place's is `teacher:`, `artist:`, `author:` or `show:` and the person's name
// in lower-case letters and digits only. A place is where someone is from
// (public on their own profile), never where an install is. Asking is
// unsigned, so a lookup is not tied to an install, and nothing logs who asked
// for what. Reporting is signed as /feedback is (header X-Sasonica-Signature:
// base64url(Ed25519(body)), ts within ten minutes and newer than this
// install's last), and a length becomes a fact once two installs agree on it
// (within AGREE_S or 1%; a place, on country, state and city), or one install
// in TRUSTED (red5) reports it, so no one install can put a wrong fact in
// front of everyone.

export interface Env {
  DB: D1Database;
  READS: RateLimit;
  WRITES: RateLimit;
  /** Install ids, comma-separated, whose report is a fact at once (wrangler vars). */
  TRUSTED: string;
}

const MAX_BODY = 32 * 1024;
const MAX_KEYS = 200;
const MAX_SECONDS = 48 * 3600;
const SKEW_S = 600;
const AGREE_S = 2;
const MAX_REPORTS_PER_HOUR = 2000;   // per install
const KEY = /^(yt:[A-Za-z0-9_-]{11}|audio:[0-9a-f]{32})$/;
const PLACE_KEY = /^(teacher|artist|author|show):[a-z0-9]{1,80}$/;
const PLACE_FIELDS = ["country", "state", "city", "region"] as const;
type Place = { country: string | null; state: string | null; city: string | null; region: string | null; photo: string | null };

/** A reported place, cleaned; null when it is not one. */
function place(v: unknown): Place | null {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  const out: Place = { country: null, state: null, city: null, region: null, photo: null };
  for (const f of PLACE_FIELDS) {
    const x = o[f];
    if (x === null || x === undefined || x === "") continue;
    if (typeof x !== "string" || x.length > 120) return null;
    out[f] = x.trim();
  }
  const ph = o.photo;
  if (typeof ph === "string" && ph) {
    if (ph.length > 300 || !/^https:\/\/[^\s"<>]+$/.test(ph)) return null;
    out.photo = ph;
  }
  return out.country || out.region ? out : null;
}

const samePlace = (a: Place, b: Place) =>
  (["country", "state", "city"] as const).every((f) => (a[f] || "").toLowerCase() === (b[f] || "").toLowerCase());

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, X-Sasonica-Signature",
  "Access-Control-Max-Age": "86400",
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...CORS },
  });
}

function b64urlDecode(s: string): Uint8Array | null {
  if (!/^[A-Za-z0-9_-]*$/.test(s)) return null;
  const pad = s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4);
  try {
    return Uint8Array.from(atob(pad), (c) => c.charCodeAt(0));
  } catch {
    return null;
  }
}

function base32(bytes: Uint8Array): string {
  const abc = "abcdefghijklmnopqrstuvwxyz234567";
  let bits = 0, value = 0, out = "";
  for (const b of bytes) {
    value = (value << 8) | b;
    bits += 8;
    while (bits >= 5) {
      out += abc[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) out += abc[(value << (5 - bits)) & 31];
  return out;
}

async function installId(pub: Uint8Array): Promise<string> {
  const h = new Uint8Array(await crypto.subtle.digest("SHA-256", pub));
  return base32(h.slice(0, 16));
}

async function readBody(req: Request): Promise<Uint8Array | Response> {
  if (Number(req.headers.get("Content-Length") || "0") > MAX_BODY) return json(413, { error: "body too large" });
  const raw = new Uint8Array(await req.arrayBuffer());
  if (raw.length > MAX_BODY) return json(413, { error: "body too large" });
  return raw;
}

function parse(raw: Uint8Array): Record<string, unknown> | null {
  try {
    const body = JSON.parse(new TextDecoder().decode(raw));
    return body && typeof body === "object" && !Array.isArray(body) ? body : null;
  } catch {
    return null;
  }
}

async function durations(req: Request, env: Env): Promise<Response> {
  const ip = req.headers.get("CF-Connecting-IP") || "?";
  const { success } = await env.READS.limit({ key: ip });
  if (!success) return json(429, { error: "too many requests; slow down" });
  const raw = await readBody(req);
  if (raw instanceof Response) return raw;
  const body = parse(raw);
  const keys = Array.isArray(body?.keys) ? [...new Set(body!.keys as unknown[])] : null;
  if (!keys || keys.length > MAX_KEYS || !keys.every((k) => typeof k === "string" && KEY.test(k))) {
    return json(400, { error: `expected {keys: [up to ${MAX_KEYS} yt:<id> or audio:<hash>]}` });
  }
  const out: Record<string, number> = {};
  if (keys.length) {
    const marks = keys.map(() => "?").join(",");
    const { results } = await env.DB.prepare(`SELECT key, seconds FROM durations WHERE key IN (${marks})`)
      .bind(...keys).all<{ key: string; seconds: number }>();
    for (const r of results) out[r.key] = r.seconds;
  }
  return json(200, { ok: true, durations: out });
}

async function places(req: Request, env: Env): Promise<Response> {
  const ip = req.headers.get("CF-Connecting-IP") || "?";
  const { success } = await env.READS.limit({ key: ip });
  if (!success) return json(429, { error: "too many requests; slow down" });
  const raw = await readBody(req);
  if (raw instanceof Response) return raw;
  const body = parse(raw);
  const keys = Array.isArray(body?.keys) ? [...new Set(body!.keys as unknown[])] : null;
  if (!keys || keys.length > MAX_KEYS || !keys.every((k) => typeof k === "string" && PLACE_KEY.test(k))) {
    return json(400, { error: `expected {keys: [up to ${MAX_KEYS} teacher|artist|author|show:<name>]}` });
  }
  const out: Record<string, unknown> = {};
  if (keys.length) {
    const marks = keys.map(() => "?").join(",");
    const { results } = await env.DB.prepare(`SELECT key, value FROM places WHERE key IN (${marks})`)
      .bind(...keys).all<{ key: string; value: string }>();
    for (const r of results) out[r.key] = JSON.parse(r.value);
  }
  return json(200, { ok: true, places: out });
}

/** Check a signed request: the key, the signature, the clock, the install. */
async function signed(req: Request, env: Env): Promise<{ install: string; body: Record<string, unknown> } | Response> {
  const ip = req.headers.get("CF-Connecting-IP") || "?";
  const { success } = await env.WRITES.limit({ key: ip });
  if (!success) return json(429, { error: "too many requests; slow down" });
  const raw = await readBody(req);
  if (raw instanceof Response) return raw;
  const sig = b64urlDecode(req.headers.get("X-Sasonica-Signature") || "");
  if (!sig || sig.length !== 64) return json(401, { error: "no signature" });
  const body = parse(raw);
  if (!body) return json(400, { error: "not a JSON object" });
  const pub = typeof body.pub === "string" ? b64urlDecode(body.pub) : null;
  if (!pub || pub.length !== 32) return json(400, { error: "pub must be a 32-byte key" });
  const ts = typeof body.ts === "number" && Number.isFinite(body.ts) ? body.ts : NaN;
  const now = Date.now() / 1000;
  if (!(Math.abs(now - ts) <= SKEW_S)) return json(400, { error: "ts is not now (check the clock)" });
  let ok = false;
  try {
    const key = await crypto.subtle.importKey("raw", pub, { name: "Ed25519" }, false, ["verify"]);
    ok = await crypto.subtle.verify({ name: "Ed25519" }, key, sig, raw);
  } catch {
    ok = false;
  }
  if (!ok) return json(401, { error: "bad signature" });
  const install = await installId(pub);
  const row = await env.DB.prepare("SELECT pub, last_ts, blocked FROM installs WHERE install = ?")
    .bind(install).first<{ pub: string; last_ts: number; blocked: number }>();
  if (row) {
    if (row.pub !== body.pub) return json(403, { error: "this install has another key" });
    if (row.blocked) return json(403, { error: "this install may not report" });
    if (ts <= row.last_ts) return json(409, { error: "an older request than one already seen" });
    await env.DB.prepare("UPDATE installs SET last_ts = ? WHERE install = ?").bind(ts, install).run();
  } else {
    await env.DB.prepare("INSERT INTO installs (install, pub, first_at, last_ts) VALUES (?, ?, ?, ?)")
      .bind(install, body.pub, Math.round(now), ts).run();
  }
  return { install, body };
}

const agree = (a: number, b: number) => Math.abs(a - b) <= Math.max(AGREE_S, 0.01 * Math.max(a, b));

async function report(req: Request, env: Env): Promise<Response> {
  const s = await signed(req, env);
  if (s instanceof Response) return s;
  const obj = (v: unknown) => v && typeof v === "object" && !Array.isArray(v);
  if (s.body.durations !== undefined && !obj(s.body.durations)) return json(400, { error: "durations must be {key: seconds}" });
  if (s.body.places !== undefined && !obj(s.body.places)) return json(400, { error: "places must be {key: place}" });
  const entries = Object.entries((s.body.durations || {}) as Record<string, unknown>);
  const placeEntries = Object.entries((s.body.places || {}) as Record<string, unknown>);
  if (!entries.length && !placeEntries.length) return json(400, { error: "expected {durations} or {places}" });
  if (entries.length + placeEntries.length > MAX_KEYS) return json(400, { error: `at most ${MAX_KEYS} at a time` });
  for (const [k, v] of entries) {
    if (!KEY.test(k) || typeof v !== "number" || !Number.isInteger(v) || v <= 0 || v > MAX_SECONDS) {
      return json(400, { error: `not a key and a whole number of seconds: ${k.slice(0, 60)}` });
    }
  }
  const cleaned: [string, Place][] = [];
  for (const [k, v] of placeEntries) {
    const p = PLACE_KEY.test(k) ? place(v) : null;
    if (!p) return json(400, { error: `not a key and a place: ${k.slice(0, 60)}` });
    cleaned.push([k, p]);
  }
  const now = Math.round(Date.now() / 1000);
  const recent = await env.DB.prepare(
    "SELECT (SELECT COUNT(*) FROM reports WHERE install = ?1 AND at > ?2) + " +
    "(SELECT COUNT(*) FROM place_reports WHERE install = ?1 AND at > ?2) AS n",
  ).bind(s.install, now - 3600).first<{ n: number }>();
  const total = entries.length + cleaned.length;
  if ((recent?.n || 0) + total > MAX_REPORTS_PER_HOUR) return json(429, { error: "too many reports this hour" });
  const trusted = (env.TRUSTED || "").split(",").map((t) => t.trim()).includes(s.install);
  let accepted = 0;
  for (const [key, v] of entries as [string, number][]) {
    await env.DB.prepare(
      "INSERT INTO reports (key, install, seconds, at) VALUES (?, ?, ?, ?) " +
      "ON CONFLICT (key, install) DO UPDATE SET seconds = excluded.seconds, at = excluded.at",
    ).bind(key, s.install, v, now).run();
    let fact = trusted;
    if (!fact) {
      const { results } = await env.DB.prepare("SELECT seconds FROM reports WHERE key = ? AND install != ?")
        .bind(key, s.install).all<{ seconds: number }>();
      fact = results.some((r) => agree(r.seconds, v));
    }
    if (fact) {
      await env.DB.prepare(
        "INSERT INTO durations (key, seconds, at) VALUES (?, ?, ?) " +
        "ON CONFLICT (key) DO UPDATE SET seconds = excluded.seconds, at = excluded.at",
      ).bind(key, v, now).run();
      accepted++;
    }
  }
  for (const [key, p] of cleaned) {
    const value = JSON.stringify(p);
    await env.DB.prepare(
      "INSERT INTO place_reports (key, install, value, at) VALUES (?, ?, ?, ?) " +
      "ON CONFLICT (key, install) DO UPDATE SET value = excluded.value, at = excluded.at",
    ).bind(key, s.install, value, now).run();
    let fact = trusted;
    if (!fact) {
      const { results } = await env.DB.prepare("SELECT value FROM place_reports WHERE key = ? AND install != ?")
        .bind(key, s.install).all<{ value: string }>();
      fact = results.some((r) => samePlace(JSON.parse(r.value), p));
    }
    if (fact) {
      await env.DB.prepare(
        "INSERT INTO places (key, value, at) VALUES (?, ?, ?) " +
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value, at = excluded.at",
      ).bind(key, value, now).run();
      accepted++;
    }
  }
  return json(200, { ok: true, install: s.install, accepted });
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
    const path = new URL(req.url).pathname.replace(/\/+$/, "");
    if (req.method === "POST" && path === "/facts/v1/durations") return durations(req, env);
    if (req.method === "POST" && path === "/facts/v1/places") return places(req, env);
    if (req.method === "POST" && path === "/facts/v1/report") return report(req, env);
    return json(404, { error: "no such route" });
  },
} satisfies ExportedHandler<Env>;
