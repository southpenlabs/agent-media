// sasonica.com/feedback — feedback from every Sasonica install, to South Pen Labs.
//
// An install's What's new thread relays what its owner answers there (opt-in);
// David's server reads it all and answers, and each install picks up the
// answers to its own (agent-media docs/proposals/2026-10-31-community-feedback.md).
// An install signs with the Ed25519 key it made at install, the one /r/ (the
// lookup) knows it by: the install id is base32(sha256(pub)[:16]), so no
// enrolment, and trust on first use as there.
//
//   POST /feedback/v1/post     {pub, ts, text, entry?, app?, server?}  signed
//                              → {ok, id}
//   POST /feedback/v1/answers  {pub, ts, after?}                       signed
//                              → {ok, answers: [{id, post_id, at, text}]}
//   GET  /feedback/v1/admin/posts?after=<id>         Bearer ADMIN_TOKEN
//                              → {ok, posts: [{id, install, at, entry, text, app, server}]}
//   POST /feedback/v1/admin/answer {install, text, post_id?}  Bearer ADMIN_TOKEN
//                              → {ok, id}
//   POST /feedback/v1/admin/block  {install, blocked}         Bearer ADMIN_TOKEN
//
// "Signed": header X-Sasonica-Signature: base64url(Ed25519(body)), the body
// JSON, ts within ten minutes of now and newer than the last one this install
// sent, so a captured request cannot be sent again.

export interface Env {
  DB: D1Database;
  WRITES: RateLimit;
  /** Set with `wrangler secret put ADMIN_TOKEN`: what David's server reads with. */
  ADMIN_TOKEN: string;
}

const MAX_BODY = 16 * 1024;
const MAX_TEXT = 8000;
const SKEW_S = 600;
const MAX_POSTS_PER_HOUR = 30;   // per install
const PAGE = 200;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Sasonica-Signature",
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

export async function installId(pub: Uint8Array): Promise<string> {
  const h = new Uint8Array(await crypto.subtle.digest("SHA-256", pub));
  return base32(h.slice(0, 16));
}

type Signed = { install: string; body: Record<string, unknown> } | Response;

/** Check a signed request: the key, the signature, the clock, the install. */
async function signed(req: Request, env: Env): Promise<Signed> {
  const ip = req.headers.get("CF-Connecting-IP") || "?";
  const { success } = await env.WRITES.limit({ key: ip });
  if (!success) return json(429, { error: "too many requests; slow down" });
  if (Number(req.headers.get("Content-Length") || "0") > MAX_BODY) return json(413, { error: "body too large" });
  const raw = new Uint8Array(await req.arrayBuffer());
  if (raw.length > MAX_BODY) return json(413, { error: "body too large" });
  const sig = b64urlDecode(req.headers.get("X-Sasonica-Signature") || "");
  if (!sig || sig.length !== 64) return json(401, { error: "no signature" });
  let body: Record<string, unknown>;
  try {
    body = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    return json(400, { error: "not JSON" });
  }
  if (!body || typeof body !== "object") return json(400, { error: "not a JSON object" });
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
    if (row.blocked) return json(403, { error: "this install may not send feedback" });
    if (ts <= row.last_ts) return json(409, { error: "an older request than one already seen" });
    await env.DB.prepare("UPDATE installs SET last_ts = ? WHERE install = ?").bind(ts, install).run();
  } else {
    await env.DB.prepare("INSERT INTO installs (install, pub, first_at, last_ts) VALUES (?, ?, ?, ?)")
      .bind(install, body.pub, Math.round(now), ts).run();
  }
  return { install, body };
}

const str = (v: unknown, max: number) => (typeof v === "string" ? v.trim().slice(0, max) : "");

async function post(req: Request, env: Env): Promise<Response> {
  const s = await signed(req, env);
  if (s instanceof Response) return s;
  const text = str(s.body.text, MAX_TEXT);
  if (!text) return json(400, { error: "text is empty" });
  const now = Math.round(Date.now() / 1000);
  const recent = await env.DB.prepare("SELECT COUNT(*) AS n FROM posts WHERE install = ? AND at > ?")
    .bind(s.install, now - 3600).first<{ n: number }>();
  if ((recent?.n || 0) >= MAX_POSTS_PER_HOUR) return json(429, { error: "too much feedback this hour" });
  const r = await env.DB.prepare(
    "INSERT INTO posts (install, at, entry, text, app, server) VALUES (?, ?, ?, ?, ?, ?)",
  ).bind(s.install, now, str(s.body.entry, 200), text, str(s.body.app, 60), str(s.body.server, 60)).run();
  return json(200, { ok: true, id: r.meta.last_row_id, install: s.install });
}

async function answers(req: Request, env: Env): Promise<Response> {
  const s = await signed(req, env);
  if (s instanceof Response) return s;
  const after = Number.isInteger(s.body.after) ? (s.body.after as number) : 0;
  const { results } = await env.DB.prepare(
    "SELECT id, post_id, at, text FROM answers WHERE install = ? AND id > ? ORDER BY id LIMIT ?",
  ).bind(s.install, after, PAGE).all();
  return json(200, { ok: true, answers: results });
}

function admin(req: Request, env: Env): boolean {
  const got = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  if (!env.ADMIN_TOKEN || got.length !== env.ADMIN_TOKEN.length) return false;
  let diff = 0;
  for (let i = 0; i < got.length; i++) diff |= got.charCodeAt(i) ^ env.ADMIN_TOKEN.charCodeAt(i);
  return diff === 0;
}

async function adminPosts(url: URL, env: Env): Promise<Response> {
  const after = Number(url.searchParams.get("after") || "0") || 0;
  const { results } = await env.DB.prepare(
    "SELECT id, install, at, entry, text, app, server FROM posts WHERE id > ? ORDER BY id LIMIT ?",
  ).bind(after, PAGE).all();
  return json(200, { ok: true, posts: results });
}

async function adminAnswer(req: Request, env: Env): Promise<Response> {
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return json(400, { error: "not JSON" });
  }
  const install = str(body.install, 40);
  const text = str(body.text, MAX_TEXT);
  if (!/^[a-z2-7]{26}$/.test(install) || !text) return json(400, { error: "expected {install, text}" });
  const known = await env.DB.prepare("SELECT 1 FROM installs WHERE install = ?").bind(install).first();
  if (!known) return json(404, { error: "no such install" });
  const postId = Number.isInteger(body.post_id) ? (body.post_id as number) : null;
  const r = await env.DB.prepare("INSERT INTO answers (install, post_id, at, text) VALUES (?, ?, ?, ?)")
    .bind(install, postId, Math.round(Date.now() / 1000), text).run();
  return json(200, { ok: true, id: r.meta.last_row_id });
}

async function adminBlock(req: Request, env: Env): Promise<Response> {
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return json(400, { error: "not JSON" });
  }
  const install = str(body.install, 40);
  if (!/^[a-z2-7]{26}$/.test(install) || typeof body.blocked !== "boolean") {
    return json(400, { error: "expected {install, blocked}" });
  }
  await env.DB.prepare("UPDATE installs SET blocked = ? WHERE install = ?").bind(body.blocked ? 1 : 0, install).run();
  return json(200, { ok: true });
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
    const url = new URL(req.url);
    const path = url.pathname.replace(/\/+$/, "");
    if (req.method === "POST" && path === "/feedback/v1/post") return post(req, env);
    if (req.method === "POST" && path === "/feedback/v1/answers") return answers(req, env);
    if (path.startsWith("/feedback/v1/admin/")) {
      if (!admin(req, env)) return json(401, { error: "not an admin" });
      if (req.method === "GET" && path === "/feedback/v1/admin/posts") return adminPosts(url, env);
      if (req.method === "POST" && path === "/feedback/v1/admin/answer") return adminAnswer(req, env);
      if (req.method === "POST" && path === "/feedback/v1/admin/block") return adminBlock(req, env);
    }
    return json(404, { error: "no such route" });
  },
} satisfies ExportedHandler<Env>;
