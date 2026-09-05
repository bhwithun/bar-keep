/** Mobile-first HTML for the away-from-home stock board. */

const CAT_ORDER = ["Spirit", "Mixer", "Garnish", "Other"];

export function renderPage(snapshot) {
  const bar = escapeHtml(snapshot?.bar_name || "Bar");
  const updated = snapshot?.updated_at
    ? escapeHtml(formatUpdated(snapshot.updated_at))
    : "Never synced";
  const onHand = Number(snapshot?.on_hand_count ?? 0);
  const outCount = Number(snapshot?.out_count ?? 0);
  const ingredients = Array.isArray(snapshot?.ingredients)
    ? snapshot.ingredients
    : [];

  const out = ingredients.filter((i) => !i.on_hand);
  const on = ingredients.filter((i) => i.on_hand);

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex,nofollow">
  <title>Stock · ${bar}</title>
  <style>
    :root {
      --bg: #1a1410;
      --panel: #241c16;
      --text: #f3e6d4;
      --muted: #b39a82;
      --out: #e07a5f;
      --on: #81b29a;
      --line: #3a2e24;
      --chip: #2e241c;
      --link: #e8c39e;
      --match-on: #7ddea8;
      --match-out: #ff9f43;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.4;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 2;
      padding: 1rem 1rem 0.85rem;
      background: linear-gradient(180deg, #1a1410 70%, transparent);
      border-bottom: 1px solid var(--line);
      backdrop-filter: blur(8px);
    }
    h1 { margin: 0; font-size: 1.35rem; letter-spacing: 0.02em; }
    .meta { margin: 0.35rem 0 0; color: var(--muted); font-size: 0.9rem; }
    .search-wrap { margin-top: 0.75rem; }
    input[type="search"] {
      width: 100%;
      padding: 0.65rem 0.8rem;
      border-radius: 0.55rem;
      border: 1px solid var(--line);
      background: var(--chip);
      color: var(--text);
      font-size: 1rem;
    }
    main { padding: 1rem; display: grid; gap: 1rem; max-width: 42rem; margin: 0 auto; }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 0.85rem;
      padding: 0.85rem 0.9rem 0.95rem;
    }
    h2 {
      margin: 0 0 0.65rem;
      font-size: 1.05rem;
      display: flex;
      align-items: baseline;
      gap: 0.45rem;
    }
    h2 span {
      font-size: 0.85rem;
      font-weight: 600;
      color: var(--muted);
    }
    h2.out { color: var(--out); }
    h2.on { color: var(--on); }
    .cat {
      margin: 0.85rem 0 0.35rem;
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
    }
    .cat:first-child { margin-top: 0.15rem; }
    ul { list-style: none; margin: 0; padding: 0; }
    li.item {
      padding: 0.55rem 0;
      border-top: 1px solid rgba(58, 46, 36, 0.7);
    }
    li.item:first-of-type { border-top: 0; }
    .name { font-weight: 600; color: var(--text); }
    .name.is-match,
    button.drink-link.is-match {
      text-shadow:
        0 0 2px #000,
        0 0 6px #000,
        0 0 12px rgba(0, 0, 0, 0.95),
        0 0 20px rgba(0, 0, 0, 0.8);
    }
    .item.stock-on .name.is-match,
    .item.stock-on button.drink-link.is-match {
      color: var(--match-on);
    }
    .item.stock-out .name.is-match,
    .item.stock-out button.drink-link.is-match {
      color: var(--match-out);
    }
    .used-by {
      margin: 0.3rem 0 0;
      font-size: 0.88rem;
      color: var(--muted);
    }
    .used-by em { font-style: normal; opacity: 0.8; }
    button.drink-link {
      display: inline;
      margin: 0;
      padding: 0;
      border: 0;
      background: none;
      color: var(--link);
      font: inherit;
      text-decoration: underline;
      text-underline-offset: 0.12em;
      cursor: pointer;
    }
    button.drink-link.is-match {
      font-weight: 700;
    }
    button.drink-link:focus-visible {
      outline: 2px solid var(--link);
      outline-offset: 2px;
      border-radius: 2px;
    }
    .empty { color: var(--muted); margin: 0.25rem 0 0; }
    .hidden { display: none !important; }
    footer {
      max-width: 42rem;
      margin: 0 auto 1.5rem;
      padding: 0 1rem;
      color: var(--muted);
      font-size: 0.8rem;
    }
  </style>
</head>
<body>
  <header>
    <h1>${bar}</h1>
    <p class="meta">${outCount} out · ${onHand} on hand · Updated ${updated}</p>
    <div class="search-wrap">
      <input type="search" id="q" placeholder="Filter ingredients or drinks" autocomplete="off">
    </div>
  </header>
  <main>
    <section>
      <h2 class="out">Need to buy / restock <span>${outCount}</span></h2>
      ${renderIngredientGroups(out, "Everything is marked on hand. Nice.", false)}
    </section>
    <section>
      <h2 class="on">On hand <span>${onHand}</span></h2>
      ${renderIngredientGroups(on, "Nothing marked on hand yet.", true)}
    </section>
  </main>
  <footer>Read-only mirror from the home bar. Edit stock on the LAN. Tap a drink name to filter.</footer>
  <script>
    (function () {
      var input = document.getElementById('q');
      if (!input) return;

      function parseDrinks(el) {
        var raw = el.getAttribute('data-drinks') || '[]';
        try { return JSON.parse(raw); } catch (e) { return []; }
      }

      function drinkMatches(name, q) {
        var d = String(name || '').toLowerCase();
        return d === q || d.indexOf(q) === 0;
      }

      // Drink names: exact or prefix only ("Old Fashioned" must not hit "Rum Old Fashioned").
      // Ingredient / category names: substring is fine.
      function itemMatches(el, q) {
        if (!q) return true;
        var ing = (el.getAttribute('data-ing') || '').toLowerCase();
        var cat = (el.getAttribute('data-cat-name') || '').toLowerCase();
        if (ing.indexOf(q) !== -1 || cat.indexOf(q) !== -1) return true;
        var drinks = parseDrinks(el);
        for (var i = 0; i < drinks.length; i++) {
          if (drinkMatches(drinks[i], q)) return true;
        }
        return false;
      }

      function apply() {
        var q = (input.value || '').trim().toLowerCase();
        document.querySelectorAll('li.item').forEach(function (el) {
          var show = itemMatches(el, q);
          el.classList.toggle('hidden', !show);

          var nameEl = el.querySelector('.name');
          var ing = (el.getAttribute('data-ing') || '').toLowerCase();
          var cat = (el.getAttribute('data-cat-name') || '').toLowerCase();
          var ingHit = Boolean(q) && (ing.indexOf(q) !== -1 || cat.indexOf(q) !== -1);
          if (nameEl) nameEl.classList.toggle('is-match', ingHit);

          el.querySelectorAll('button.drink-link').forEach(function (btn) {
            var hit = Boolean(q) && drinkMatches(btn.getAttribute('data-drink') || '', q);
            btn.classList.toggle('is-match', hit);
          });
        });
        document.querySelectorAll('[data-cat]').forEach(function (cat) {
          var group = cat.nextElementSibling;
          while (group && group.tagName !== 'UL') group = group.nextElementSibling;
          if (!group) return;
          var any = false;
          group.querySelectorAll('li.item').forEach(function (li) {
            if (!li.classList.contains('hidden')) any = true;
          });
          cat.classList.toggle('hidden', !any);
          group.classList.toggle('hidden', !any);
        });
      }
      input.addEventListener('input', apply);
      document.addEventListener('click', function (ev) {
        var btn = ev.target.closest('button.drink-link');
        if (!btn) return;
        ev.preventDefault();
        var name = btn.getAttribute('data-drink') || btn.textContent || '';
        input.value = name;
        apply();
        input.focus();
        try { input.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); } catch (e) {}
      });
    })();
  </script>
</body>
</html>`;
}

function renderIngredientGroups(items, emptyMessage, onHand) {
  if (!items.length) {
    return `<p class="empty">${escapeHtml(emptyMessage)}</p>`;
  }
  return groupByCategory(items)
    .map(([category, rows]) => {
      const lis = rows
        .map((ing) => renderIngredientItem(ing, category, onHand))
        .join("");
      return `<div class="cat" data-cat>${escapeHtml(category)}</div><ul>${lis}</ul>`;
    })
    .join("");
}

function renderIngredientItem(ing, category, onHand) {
  const used = Array.isArray(ing.used_by) ? ing.used_by : [];
  const drinkNames = used.map((d) => d.name || "").filter(Boolean);
  const stockClass = onHand ? "stock-on" : "stock-out";
  const usedHtml = used.length
    ? `<p class="used-by"><strong>Used by:</strong> ${used
        .map((d, i) => {
          const name = d.name || "";
          const link = `<button type="button" class="drink-link" data-drink="${escapeAttr(
            name
          )}">${escapeHtml(name)}</button>`;
          const optional =
            d.necessary === false ? ` <em>(optional)</em>` : "";
          const sep = i === 0 ? "" : " · ";
          return `${sep}${link}${optional}`;
        })
        .join("")}</p>`
    : `<p class="used-by">Not used by any recipe</p>`;
  return `<li class="item ${stockClass}" data-ing="${escapeAttr(ing.name || "")}" data-cat-name="${escapeAttr(
    category
  )}" data-drinks="${escapeAttr(JSON.stringify(drinkNames))}">
    <div class="name">${escapeHtml(ing.name || "")}</div>
    ${usedHtml}
  </li>`;
}

function groupByCategory(items) {
  const map = new Map();
  for (const item of items) {
    const cat = item.category || "Other";
    if (!map.has(cat)) map.set(cat, []);
    map.get(cat).push(item);
  }
  for (const rows of map.values()) {
    rows.sort((a, b) =>
      String(a.name || "").localeCompare(String(b.name || ""), undefined, {
        sensitivity: "base",
      })
    );
  }
  const keys = [...map.keys()].sort((a, b) => {
    const ia = CAT_ORDER.indexOf(a);
    const ib = CAT_ORDER.indexOf(b);
    if (ia === -1 && ib === -1) return a.localeCompare(b);
    if (ia === -1) return 1;
    if (ib === -1) return -1;
    return ia - ib;
  });
  return keys.map((k) => [k, map.get(k)]);
}

function formatUpdated(iso) {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return iso;
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return `${hrs}h ago`;
  return new Date(t).toLocaleString();
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeAttr(s) {
  return escapeHtml(s).replace(/'/g, "&#39;");
}
