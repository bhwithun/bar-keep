/**
 * Away-from-home bar stock mirror.
 *
 * GET  /        → HTML board (requires READ_SECRET via ?k= or Bearer)
 * GET  /stock   → JSON snapshot (same read auth)
 * PUT  /stock   → replace snapshot (WRITE_SECRET Bearer only)
 * GET  /health  → { ok, updated_at } (no auth; no ingredient data)
 */

import { renderPage } from "./page.js";

const SNAPSHOT_KEY = "snapshot";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    try {
      if (request.method === "OPTIONS") {
        return withCors(new Response(null, { status: 204 }));
      }

      if (url.pathname === "/health" && request.method === "GET") {
        const snapshot = await loadSnapshot(env);
        return json({
          ok: true,
          updated_at: snapshot?.updated_at ?? null,
          has_snapshot: Boolean(snapshot),
        });
      }

      if (url.pathname === "/" && request.method === "GET") {
        if (!authorizeRead(request, url, env)) {
          return json({ error: "Unauthorized" }, 401);
        }
        const snapshot = await loadSnapshot(env);
        if (!snapshot) {
          return html(emptyPage());
        }
        return html(renderPage(snapshot));
      }

      if (url.pathname === "/stock" && request.method === "GET") {
        if (!authorizeRead(request, url, env)) {
          return json({ error: "Unauthorized" }, 401);
        }
        const snapshot = await loadSnapshot(env);
        if (!snapshot) {
          return json({ error: "No snapshot yet" }, 404);
        }
        return json(snapshot);
      }

      if (url.pathname === "/stock" && request.method === "PUT") {
        if (!authorizeWrite(request, env)) {
          return json({ error: "Unauthorized" }, 401);
        }
        let body;
        try {
          body = await request.json();
        } catch {
          return json({ error: "Invalid JSON" }, 400);
        }
        if (!body || typeof body !== "object" || !Array.isArray(body.ingredients)) {
          return json({ error: "Body must include ingredients[]" }, 400);
        }
        const snapshot = {
          updated_at: body.updated_at || new Date().toISOString(),
          bar_name: body.bar_name || "Bar",
          on_hand_count: Number(body.on_hand_count ?? 0),
          out_count: Number(body.out_count ?? 0),
          ingredients: body.ingredients,
        };
        await env.STOCK.put(SNAPSHOT_KEY, JSON.stringify(snapshot));
        return json({ ok: true, updated_at: snapshot.updated_at });
      }

      return json({ error: "Not found" }, 404);
    } catch (error) {
      return json({ error: error.message || String(error) }, 500);
    }
  },
};

async function loadSnapshot(env) {
  const raw = await env.STOCK.get(SNAPSHOT_KEY);
  if (!raw) return null;
  return JSON.parse(raw);
}

function authorizeRead(request, url, env) {
  const expected = env.READ_SECRET;
  if (!expected) return false;
  const bearer = bearerToken(request);
  const query = url.searchParams.get("k") || "";
  return timingSafeEqual(bearer, expected) || timingSafeEqual(query, expected);
}

function authorizeWrite(request, env) {
  const expected = env.WRITE_SECRET;
  if (!expected) return false;
  return timingSafeEqual(bearerToken(request), expected);
}

function bearerToken(request) {
  const header = request.headers.get("Authorization") || "";
  const match = header.match(/^Bearer\s+(.+)$/i);
  return match ? match[1].trim() : "";
}

function timingSafeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  if (a.length !== b.length) return false;
  let out = 0;
  for (let i = 0; i < a.length; i++) {
    out |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return out === 0;
}

function emptyPage() {
  return `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stock · waiting</title>
<style>body{font-family:system-ui,sans-serif;background:#1a1410;color:#f3e6d4;padding:2rem;max-width:28rem;margin:0 auto}
.muted{color:#b39a82}</style></head>
<body><h1>Bar stock</h1>
<p class="muted">No snapshot yet. Push stock from the home bar Admin → Inventory.</p>
</body></html>`;
}

function json(data, status = 200) {
  return withCors(
    new Response(JSON.stringify(data, null, 2), {
      status,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
      },
    })
  );
}

function html(body) {
  return withCors(
    new Response(body, {
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
      },
    })
  );
}

function withCors(response) {
  const headers = new Headers(response.headers);
  headers.set("Access-Control-Allow-Origin", "*");
  headers.set("Access-Control-Allow-Headers", "Content-Type, Authorization");
  headers.set("Access-Control-Allow-Methods", "GET, PUT, OPTIONS");
  return new Response(response.body, { status: response.status, headers });
}
