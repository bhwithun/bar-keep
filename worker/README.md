# bar-stock Worker

Read-only Cloudflare mirror of What’s On Hand for away-from-home checks.

## Routes

| Method | Path | Auth |
|--------|------|------|
| `GET` | `/` | `?k=` or `Authorization: Bearer` = `READ_SECRET` |
| `GET` | `/stock` | same |
| `PUT` | `/stock` | `Authorization: Bearer` = `WRITE_SECRET` |
| `GET` | `/health` | none (no ingredient data) |

## Deploy

```bash
cd worker
npm install
npx wrangler secret put READ_SECRET
npx wrangler secret put WRITE_SECRET
npx wrangler deploy
```

KV binding `STOCK` is already provisioned (`id` in `wrangler.jsonc`).

Then set the Worker URL + write secret under **Admin → Settings** (or
`DRINKS_STOCK_MIRROR_*` env vars) and push once from **Admin → Inventory**.
