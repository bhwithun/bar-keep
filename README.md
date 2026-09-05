# Drinks — home bar menus for phones and the TV

A party-night **home bar** app: guests browse drinks on their phones, the living-room TV mirrors the night’s menu, and you keep recipes and stock in sync from one place.

Only show drinks you can actually pour — based on what’s on hand — with themed menus, bottle-first browsing, guest ratings, and a “show this on the TV” button when someone’s ready to order.

Default bar name: **The Raven**. Rename it under Admin → Settings.

---

## Who it’s for

Hosts who keep a real bar cart (or cabinet) and want guests to self-serve the menu without asking “what can you make?” every five minutes. Designed for a home LAN: phones in hand, TV on the wall, no accounts required.

---

## Three surfaces

| Surface | Who | What |
|---------|-----|------|
| **Guest** (`/guest`) | Guests on phones | Browse, rate, check off ingredients while pouring, pin a drink to the TV |
| **TV** (`/tv`) | Living-room display | Big makeable menu, theme boards, stock board, QR codes, pour toasts |
| **Admin** (`/admin`) | Host | Recipes, inventory, substitutes, Wi‑Fi / guest-link settings |

Legacy `/bar/…` URLs redirect to `/guest/…`. Opening `/` sends you to the TV board.

> **Trust model:** Admin has **no login**. Run this on a trusted home network (or put a reverse proxy / auth in front if you expose it further).

---

## Major features

### Makeable from what’s on hand

Mark bottles in or out on **What’s On Hand** (`/guest/on-hand`). Guest menus and TV boards default to drinks whose **necessary** ingredients are all covered. Optional garnishes never block a drink.

Use **Show all** (`?all=1`) to reveal the rest of the published menu, grayed out, with a **Need:** list of missing bottles.

### Themes

Twenty-five curated themes (cuisine, structure, flavor, season/occasion) so guests can answer “what’s the night?” instead of scrolling a flat list. Each theme has its own palette, icon, and — on the TV — stage-set ornaments, quote ticker, and music suggestions.

### Start with a bottle

**Start with…** (`/guest/start`) lets a guest pick one ingredient and see every drink that uses it. Makeable pours come first; **Show all** includes recipes that still need other bottles.

### Featured bottles

Star a spirit on What’s On Hand. A virtual **Featured** menu appears with makeable drinks that use any starred bottle — handy when you want to push the new amaro or finish the mezcal.

### Substitutes

Pair bottles that can stand in for each other (e.g. brandy ↔ cognac) under **Admin → Substitutes**. Makeability and recipe checklists treat an on-hand substitute as covering the listed ingredient.

### Ratings, top ten, and recent pours

Guests rate drinks 1–5 stars (one vote per phone, via cookie). **Top ten** ranks by average score. Finishing a recipe’s ingredient checklist logs a pour; **Most recent** and the TV toast show what just left the shaker.

### Show on TV

From a recipe page, **Show on TV** pins that drink on the living-room board so the bartender can see the build without huddling over a phone. The TV follows whichever guest menu board was last opened (main, theme, featured, stock).

### Stock board and shopping list

- **TV → Stock** — on-hand vs out, at a glance  
- **Admin → Inventory** — out-of-stock first, with which recipes each missing bottle blocks  

### Guest access QR codes

The TV can show:

- a **Wi‑Fi** join QR (SSID / password from settings)  
- a **guest menu** QR pointing at your LAN URL  

Configure both under **Admin → Settings** (or seed them with environment variables — see below).

---

## Quick map of guest routes

| Path | Purpose |
|------|---------|
| `/guest` | Theme picker |
| `/guest/start` | Start with a bottle |
| `/guest/menu` | Full makeable menu |
| `/guest/theme/<slug>` | One theme |
| `/guest/featured` | Starred-bottle menu |
| `/guest/recent` | Last five poured |
| `/guest/top` | Highest rated |
| `/guest/on-hand` | Stock toggles + Featured stars |
| `/guest/ingredient/<id>` | Drinks using one ingredient |
| `/guest/recipe/<id>` | Build, rate, show on TV |

TV highlights: `/tv`, `/tv/themes`, `/tv/theme/<slug>`, `/tv/featured`, `/tv/stock`.

---

## Stack

- **Python 3** + **Flask**
- **SQLite** (`drinks.db`) — recipes, ingredients, ratings, pours, settings
- **Jinja** templates + static CSS/JS/SVG (glassware, ingredient icons, TV stage sets)
- **qrcode** + Pillow for join QR images

No separate frontend build step.

---

## Run locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export DRINKS_PORT=5000          # avoid needing root for port 80
export DRINKS_SECRET_KEY='pick-a-long-random-string'
./run.sh
```

Then open:

- Guest: `http://localhost:5000/guest`  
- TV: `http://localhost:5000/tv`  
- Admin: `http://localhost:5000/admin`  

`./run.sh` defaults to `0.0.0.0:80` (needs root or `CAP_NET_BIND_SERVICE` / authbind). Override with env vars as needed.

### Environment variables

| Variable | Default | Role |
|----------|---------|------|
| `DRINKS_HOST` | `0.0.0.0` | Bind address |
| `DRINKS_PORT` | `80` | HTTP port |
| `DRINKS_SECRET_KEY` | `change-me-in-production` | Flask session secret |
| `DRINKS_DEBUG` | `0` | Set `1` for Flask debug |
| `DRINKS_BAR_NAME` | `The Raven` | Seeds display name if unset in DB |
| `DRINKS_PUBLIC_BASE_URL` | _(empty)_ | LAN URL for guest QR, e.g. `http://192.168.1.10` |
| `DRINKS_WIFI_SSID` | _(empty)_ | Seeds Wi‑Fi QR |
| `DRINKS_WIFI_PASSWORD` | _(empty)_ | Seeds Wi‑Fi QR |
| `DRINKS_WIFI_SECURITY` | `WPA` | `WPA`, `WEP`, or `nopass` |
| `DRINKS_WIFI_HIDDEN` | `0` | `1` if the SSID is hidden |
| `DRINKS_STOCK_MIRROR_URL` | _(empty)_ | Cloudflare Worker base URL for away-from-home stock |
| `DRINKS_STOCK_MIRROR_WRITE_SECRET` | _(empty)_ | Bearer token matching the Worker `WRITE_SECRET` |
| `DRINKS_STOCK_MIRROR_READ_SECRET` | _(empty)_ | Token for `?k=` on the Worker page (Admin Inventory link) |

Empty settings are seeded from these env vars once; afterward **Admin → Settings** wins.

### Away-from-home stock mirror

The home app stays on your LAN. A separate Cloudflare Worker (`worker/`) holds a
read-only copy of ingredient on-hand / out status (including which drinks use
each out-of-stock bottle).

1. Deploy the Worker from `worker/` (`npx wrangler deploy`), create the `STOCK` KV binding, and set secrets `READ_SECRET` + `WRITE_SECRET`.
2. In **Admin → Settings**, set the Worker URL and write secret (or use the env vars above).
3. Open **Admin → Inventory → Push stock to cloud** once, then bookmark:

   `https://<worker>.workers.dev/?k=<READ_SECRET>`

Toggling stock on **What’s On Hand** pushes the mirror automatically. If Cloudflare is unreachable, local toggles still succeed.

### Production-ish on a home server

This repo includes `restart_server.sh`, which restarts a host `drinks` systemd unit if you’ve installed one. Example unit pattern: run `run.sh` from the app directory, restart on failure, bind port 80 via authbind or capabilities. Keep Admin off the public internet unless you add your own access control.

---

## Project layout

```
app.py                 Flask app, schema, routes
themes.py              Theme catalog, palettes, TV artists
ingredient_icons.py    Icon slug map for bottles
ticker_quotes.py       Quote pools for the TV ticker
drinks.db              SQLite database (created/migrated on start)
requirements.txt
run.sh                 Launch helper
restart_server.sh      systemctl restart helper
static/                CSS, JS, favicons, glassware/, tv-sets/, sounds
templates/             Guest, TV, and admin pages; SVG icon sets
```

---

## Typical night

1. Update **What’s On Hand** (and star anything you want to feature).  
2. Put `/tv` on the living-room screen.  
3. Point guests at `/guest` (or the QR on the TV).  
4. They pick a theme, a bottle, or the full menu — only pourable drinks show by default.  
5. When someone orders, they hit **Show on TV**; you build from the big screen.  
6. Restock later from **Admin → Inventory**.

---

## License / status

Personal home-bar software. Fork and adapt for your own cabinet. Contributions and issues are welcome if you share the same problem: too many bottles, not enough bartenders.
