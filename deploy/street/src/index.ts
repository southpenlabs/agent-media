// sasonica.com/street/v1/near — Mapillary street photos near a point, asked
// with one client token held here (the Worker secret MAPILLARY_TOKEN) for the
// installs listed in ALLOWED, so a host needs no Mapillary token of its own
// (David, 24 Nov 2026: "I'd prefer it to work from sasonica.com rather than
// each host server individually"). The server's side is agent-media
// map_street_photos.py; contract §6.50; outside-services row 87.
//
//   GET /street/v1/near?lat=&lon=&radius_m=&limit=&ts=&pub=
//       header X-Sasonica-Signature: base64url(Ed25519(path + query))
//     → 200 {data: [{id, captured_at, compass_angle, computed_compass_angle,
//            geometry, thumb_256_url, thumb_1024_url, creator: {username},
//            is_pano}]}   (Mapillary's own fields, trimmed)
//     → 403 {error: unsigned | bad_signature | not_allowed}
//     → 400 {error: bad_ask | stale}, 429 {error: slow_down}
//     → 503 {error: not_configured}   (no token set yet)
//     → 502 {error: upstream}         (Mapillary did not answer)
//
// Signed as the lookup and /facts are: an install's Ed25519 key, the install
// id being base32(sha256(pub)[:16]). A GET carries `pub` and `ts` in its
// query, and the signature covers the path and the whole query exactly as
// sent, so nothing in the ask can be changed. ts is good for ±10 minutes; a
// replay within that only gets the same photos again. Only install ids in
// ALLOWED are served: Mapillary's terms have each app register its own
// client, so until Mapillary agrees in writing to a shared token, the list is
// David's own installs.
//
// Mapillary's thumbnail links are signed and expire, so an answer is kept in
// Cloudflare's cache for CACHE_S only (keyed by the rounded ask, never by who
// asked). Never used along a route: Mapillary's terms (§5) forbid use "in
// connection with real-time navigation or route guidance"; the server asks
// for a place card only.

export interface Env {
  /** Mapillary client token (MLY|…); a secret, unset until registered. */
  MAPILLARY_TOKEN?: string;
  /** Install ids served, comma-separated. */
  ALLOWED: string;
  PER_INSTALL: RateLimit;
  PER_ADDRESS: RateLimit;
}

const GRAPH = "https://graph.mapillary.com/images";
const FIELDS = "id,captured_at,compass_angle,computed_compass_angle,geometry,thumb_256_url,thumb_1024_url,creator,is_pano";
const SKEW_S = 600;
const CACHE_S = 3600;
const RADIUS_MIN = 5;
const RADIUS_MAX = 500;
const LIMIT_MAX = 100;
const TIMEOUT_MS = 8000;

function json(status: number, body: unknown, cache = "no-store"): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": cache },
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

function num(s: string | null, lo: number, hi: number): number | null {
  if (s === null || s === "") return null;
  const n = Number(s);
  return Number.isFinite(n) && n >= lo && n <= hi ? n : null;
}

/** Which install signed this request, or the refusal. */
async function signer(req: Request, url: URL, env: Env): Promise<string | Response> {
  const sig = b64urlDecode(req.headers.get("X-Sasonica-Signature") || "");
  const pub = b64urlDecode(url.searchParams.get("pub") || "");
  if (!sig || sig.length !== 64 || !pub || pub.length !== 32) return json(403, { error: "unsigned" });
  const ts = num(url.searchParams.get("ts"), 0, 1e11);
  if (ts === null || Math.abs(Date.now() / 1000 - ts) > SKEW_S) return json(400, { error: "stale" });
  let ok = false;
  try {
    const key = await crypto.subtle.importKey("raw", pub, { name: "Ed25519" }, false, ["verify"]);
    ok = await crypto.subtle.verify({ name: "Ed25519" }, key, sig, new TextEncoder().encode(url.pathname + url.search));
  } catch {
    ok = false;
  }
  if (!ok) return json(403, { error: "bad_signature" });
  const id = await installId(pub);
  const allowed = (env.ALLOWED || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (!allowed.includes(id)) return json(403, { error: "not_allowed" });
  return id;
}

interface Image {
  id?: unknown;
  captured_at?: unknown;
  compass_angle?: unknown;
  computed_compass_angle?: unknown;
  geometry?: unknown;
  thumb_256_url?: unknown;
  thumb_1024_url?: unknown;
  creator?: { username?: unknown };
  is_pano?: unknown;
}

function trim(f: Image): Record<string, unknown> {
  return {
    id: f.id,
    captured_at: f.captured_at,
    compass_angle: f.compass_angle,
    computed_compass_angle: f.computed_compass_angle,
    geometry: f.geometry,
    thumb_256_url: f.thumb_256_url,
    thumb_1024_url: f.thumb_1024_url,
    creator: f.creator && typeof f.creator.username === "string" ? { username: f.creator.username } : undefined,
    is_pano: f.is_pano,
  };
}

async function near(req: Request, url: URL, env: Env, ctx: ExecutionContext): Promise<Response> {
  const ip = req.headers.get("CF-Connecting-IP") || "?";
  if (!(await env.PER_ADDRESS.limit({ key: ip })).success) return json(429, { error: "slow_down" });
  const who = await signer(req, url, env);
  if (who instanceof Response) return who;
  if (!(await env.PER_INSTALL.limit({ key: who })).success) return json(429, { error: "slow_down" });
  const lat = num(url.searchParams.get("lat"), -90, 90);
  const lon = num(url.searchParams.get("lon"), -180, 180);
  const radius = num(url.searchParams.get("radius_m") || "80", RADIUS_MIN, RADIUS_MAX);
  const limit = num(url.searchParams.get("limit") || "100", 1, LIMIT_MAX);
  if (lat === null || lon === null || radius === null || limit === null) {
    return json(400, { error: "bad_ask", detail: `lat, lon, radius_m ${RADIUS_MIN}-${RADIUS_MAX}, limit 1-${LIMIT_MAX}` });
  }
  const token = (env.MAPILLARY_TOKEN || "").trim();
  if (!token) return json(503, { error: "not_configured" });

  // One cached answer per rounded ask (about a metre), whoever asked.
  const la = Math.round(lat * 1e5) / 1e5, lo = Math.round(lon * 1e5) / 1e5;
  const r = Math.round(radius), n = Math.round(limit);
  const cacheKey = new Request(`https://sasonica.com/street/v1/_cache?lat=${la}&lon=${lo}&radius_m=${r}&limit=${n}`);
  const cache = caches.default;
  const hit = await cache.match(cacheKey);
  if (hit) return new Response(hit.body, { status: 200, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });

  const dlat = r / 111320;
  const dlon = r / (111320 * Math.max(0.01, Math.cos((la * Math.PI) / 180)));
  const bbox = [lo - dlon, la - dlat, lo + dlon, la + dlat].map((x) => x.toFixed(6)).join(",");
  const q = new URLSearchParams({ bbox, limit: String(n), fields: FIELDS });
  let got: Response;
  try {
    got = await fetch(`${GRAPH}?${q}`, {
      headers: { Authorization: `OAuth ${token}`, Accept: "application/json" },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    return json(502, { error: "upstream" });
  }
  if (got.status !== 200) return json(502, { error: "upstream", status: got.status });
  let body: { data?: Image[] };
  try {
    body = await got.json();
  } catch {
    return json(502, { error: "upstream" });
  }
  const out = JSON.stringify({ data: (body.data || []).map(trim) });
  ctx.waitUntil(cache.put(cacheKey, new Response(out, {
    headers: { "Content-Type": "application/json", "Cache-Control": `public, max-age=${CACHE_S}` },
  })));
  return new Response(out, { status: 200, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
}

export default {
  async fetch(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(req.url);
    if (url.pathname === "/street/v1/near" || url.pathname === "/street/v1/near/") {
      if (req.method !== "GET") return json(405, { error: "GET" });
      return near(req, url, env, ctx);
    }
    return json(404, { error: "no such route" });
  },
} satisfies ExportedHandler<Env>;
