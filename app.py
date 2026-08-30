#!/usr/bin/env python3
"""Home bar recipes: TV display, interactive filter, open admin (Flask + SQLite)."""

import io
import os
import sqlite3
import time
import uuid
from pathlib import Path

import qrcode
from flask import (
    Flask,
    flash,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from ingredient_icons import category_slug, icon_for_ingredient
from ticker_quotes import extra_ticker_quotes
from themes import (
    FEATURED_THEME_SLUG,
    RECIPE_THEMES,
    THEME_FAMILIES,
    THEMES,
    artists_for_theme,
    palette_style,
    ticker_quotes_for_theme,
)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "drinks.db"

# Settings keys (stored in SQLite; env vars used as defaults when empty)
SETTING_WIFI_SSID = "wifi_ssid"
SETTING_WIFI_PASSWORD = "wifi_password"
SETTING_WIFI_SECURITY = "wifi_security"  # WPA | WEP | nopass
SETTING_WIFI_HIDDEN = "wifi_hidden"  # "0" | "1"
SETTING_PUBLIC_BASE_URL = "public_base_url"  # e.g. http://192.168.1.10
SETTING_BAR_NAME = "bar_name"
SETTING_TV_BOARD = "tv_board"
DEFAULT_BAR_NAME = "The Raven"

app = Flask(__name__)
app.secret_key = os.environ.get("DRINKS_SECRET_KEY", "change-me-in-production")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS ingredients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL DEFAULT 'Other',
            sort_order INTEGER NOT NULL DEFAULT 0,
            in_filter INTEGER NOT NULL DEFAULT 1,
            featured INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            instructions TEXT NOT NULL DEFAULT '',
            glassware TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT 'Other',
            tags TEXT NOT NULL DEFAULT '',
            published INTEGER NOT NULL DEFAULT 1,
            featured INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS recipe_ingredients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
            measure TEXT NOT NULL DEFAULT '',
            necessary INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            UNIQUE(recipe_id, ingredient_id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            device_id TEXT NOT NULL,
            stars INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(recipe_id, device_id)
        );

        CREATE TABLE IF NOT EXISTS movie_quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote TEXT NOT NULL,
            movie TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS made_drinks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tv_shows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            dismissed INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS ingredient_substitutes (
            ingredient_id INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
            substitute_id INTEGER NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
            PRIMARY KEY (ingredient_id, substitute_id),
            CHECK (ingredient_id != substitute_id)
        );

        CREATE TABLE IF NOT EXISTS themes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            family TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS recipe_themes (
            recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            theme_id INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
            PRIMARY KEY (recipe_id, theme_id)
        );
        """
    )

    recipe_cols = {
        row["name"] for row in db.execute("PRAGMA table_info(recipes)").fetchall()
    }
    if "featured" not in recipe_cols:
        db.execute(
            "ALTER TABLE recipes ADD COLUMN featured INTEGER NOT NULL DEFAULT 0"
        )

    ing_cols = {
        row["name"] for row in db.execute("PRAGMA table_info(ingredients)").fetchall()
    }
    if "featured" not in ing_cols:
        db.execute(
            "ALTER TABLE ingredients ADD COLUMN featured INTEGER NOT NULL DEFAULT 0"
        )

    tv_show_cols = {
        row["name"] for row in db.execute("PRAGMA table_info(tv_shows)").fetchall()
    }
    if "dismissed" not in tv_show_cols:
        db.execute(
            "ALTER TABLE tv_shows ADD COLUMN dismissed INTEGER NOT NULL DEFAULT 0"
        )

    # Ensure default setting keys exist (do not overwrite host edits)
    defaults = {
        SETTING_WIFI_SSID: os.environ.get("DRINKS_WIFI_SSID", ""),
        SETTING_WIFI_PASSWORD: os.environ.get("DRINKS_WIFI_PASSWORD", ""),
        SETTING_WIFI_SECURITY: os.environ.get("DRINKS_WIFI_SECURITY", "WPA"),
        SETTING_WIFI_HIDDEN: os.environ.get("DRINKS_WIFI_HIDDEN", "0"),
        SETTING_PUBLIC_BASE_URL: os.environ.get("DRINKS_PUBLIC_BASE_URL", ""),
        SETTING_BAR_NAME: os.environ.get("DRINKS_BAR_NAME", DEFAULT_BAR_NAME),
    }
    for key, value in defaults.items():
        row = db.execute(
            "SELECT key FROM settings WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)", (key, value)
            )

    count = db.execute("SELECT COUNT(*) FROM ingredients").fetchone()[0]
    if count == 0:
        _seed(db)
    _collapse_citrus_garnish_ingredients(db)
    _unify_coffee_liqueur_as_kahlua(db)
    _replace_sweet_and_sour_mix(db)
    _ensure_named_ingredients(db)
    _seed_ingredient_substitutes(db)
    _ensure_catalog_recipes(db)
    _ensure_themes(db)
    _seed_movie_quotes(db)
    db.commit()
    db.close()


def _seed_movie_quotes(db):
    quotes = [
        ("I'll be back.", "The Terminator"),
        ("May the Force be with you.", "Star Wars"),
        ("Here's looking at you, kid.", "Casablanca"),
        ("You talking to me?", "Taxi Driver"),
        ("I'll have what she's having.", "When Harry Met Sally"),
        ("We're gonna need a bigger boat.", "Jaws"),
        ("Life is like a box of chocolates.", "Forrest Gump"),
        ("You can't handle the truth!", "A Few Good Men"),
        ("I feel the need—the need for speed.", "Top Gun"),
        ("Nobody puts Baby in a corner.", "Dirty Dancing"),
        ("That'll do, pig. That'll do.", "Babe"),
        ("I am serious. And don't call me Shirley.", "Airplane!"),
        ("It's just a flesh wound.", "Monty Python and the Holy Grail"),
        ("Strange women lying in ponds distributing swords is no basis for a system of government.", "Monty Python and the Holy Grail"),
        ("Inconceivable!", "The Princess Bride"),
        ("Hello. My name is Inigo Montoya. You killed my father. Prepare to die.", "The Princess Bride"),
        ("Have fun storming the castle!", "The Princess Bride"),
        ("As you wish.", "The Princess Bride"),
        ("Why so serious?", "The Dark Knight"),
        ("I am Groot.", "Guardians of the Galaxy"),
        ("I volunteer as tribute!", "The Hunger Games"),
        ("Winter is coming.", "Game of Thrones"),
        ("This is the way.", "The Mandalorian"),
        ("Just keep swimming.", "Finding Nemo"),
        ("To infinity and beyond!", "Toy Story"),
        ("There's no place like home.", "The Wizard of Oz"),
        ("Toto, I've a feeling we're not in Kansas anymore.", "The Wizard of Oz"),
        ("I'm king of the world!", "Titanic"),
        ("Houston, we have a problem.", "Apollo 13"),
        ("Show me the money!", "Jerry Maguire"),
        ("You had me at hello.", "Jerry Maguire"),
        ("I see dead people.", "The Sixth Sense"),
        ("They may take our lives, but they'll never take our freedom!", "Braveheart"),
        ("Say hello to my little friend!", "Scarface"),
        ("Keep your friends close, but your enemies closer.", "The Godfather Part II"),
        ("I'm going to make him an offer he can't refuse.", "The Godfather"),
        ("Leave the gun. Take the cannoli.", "The Godfather"),
        ("What we've got here is failure to communicate.", "Cool Hand Luke"),
        ("Go ahead, make my day.", "Sudden Impact"),
        ("Do I feel lucky? Well, do ya, punk?", "Dirty Harry"),
        ("Yippee-ki-yay.", "Die Hard"),
        ("Welcome to the party, pal.", "Die Hard"),
        ("I love the smell of napalm in the morning.", "Apocalypse Now"),
        ("The first rule of Fight Club is: you do not talk about Fight Club.", "Fight Club"),
        ("Gentlemen, you can't fight in here! This is the War Room!", "Dr. Strangelove"),
        ("Roads? Where we're going, we don't need roads.", "Back to the Future"),
        ("Great Scott!", "Back to the Future"),
        ("If you build it, he will come.", "Field of Dreams"),
        ("There's no crying in baseball!", "A League of Their Own"),
        ("You're gonna need a bigger closet.", "Clueless"),
        ("On Wednesdays we wear pink.", "Mean Girls"),
        ("She doesn't even go here!", "Mean Girls"),
        ("Stop trying to make fetch happen.", "Mean Girls"),
        ("That is so fetch.", "Mean Girls"),
        ("I'm not bad. I'm just drawn that way.", "Who Framed Roger Rabbit"),
        ("I'm walking here! I'm walking here!", "Midnight Cowboy"),
        ("What is this, a center for ants?", "Zoolander"),
        ("Moisture is the essence of wetness, and wetness is the essence of beauty.", "Zoolander"),
        ("I feel pretty, oh so pretty.", "West Side Story"),
        ("It's not a tumor!", "Kindergarten Cop"),
        ("Get to the chopper!", "Predator"),
        ("If it bleeds, we can kill it.", "Predator"),
        ("Come with me if you want to live.", "Terminator 2"),
        ("Hasta la vista, baby.", "Terminator 2"),
        ("I know kung fu.", "The Matrix"),
        ("There is no spoon.", "The Matrix"),
        ("Why didn't you put the bunny back in the box?", "Con Air"),
        ("It's a trap!", "Return of the Jedi"),
        ("Do. Or do not. There is no try.", "The Empire Strikes Back"),
        ("I find your lack of faith disturbing.", "Star Wars"),
        ("These aren't the droids you're looking for.", "Star Wars"),
        ("I am your father.", "The Empire Strikes Back"),
        ("Never tell me the odds.", "The Empire Strikes Back"),
        ("That's no moon. It's a space station.", "Star Wars"),
        ("I've got a good feeling about this.", "Solo: A Star Wars Story"),
        ("I have a bad feeling about this.", "Star Wars"),
        ("The name's Bond. James Bond.", "Dr. No"),
        ("Shaken, not stirred.", "Goldfinger"),
        ("A martini. Shaken, not stirred.", "Goldfinger"),
        ("Bond. James Bond.", "Dr. No"),
        ("I'm the king of the world!", "Titanic"),
        ("You complete me.", "Jerry Maguire"),
        ("After all, tomorrow is another day!", "Gone with the Wind"),
        ("Frankly, my dear, I don't give a damn.", "Gone with the Wind"),
        ("I'm as mad as hell, and I'm not going to take this anymore!", "Network"),
        ("You can't sit with us!", "Mean Girls"),
        ("So you're telling me there's a chance.", "Dumb and Dumber"),
        ("We got no food, we got no jobs… our pets' heads are falling off!", "Dumb and Dumber"),
        ("Just when I thought I was out, they pull me back in.", "The Godfather Part III"),
        ("I'm not even supposed to be here today!", "Clerks"),
        ("Party on, Wayne. Party on, Garth.", "Wayne's World"),
        ("Schwing!", "Wayne's World"),
        ("We're not worthy!", "Wayne's World"),
        ("Bueller? Bueller?", "Ferris Bueller's Day Off"),
        ("Life moves pretty fast. If you don't stop and look around once in a while, you could miss it.", "Ferris Bueller's Day Off"),
        ("Carpe diem. Seize the day, boys.", "Dead Poets Society"),
        ("I'm the dude. So that's what you call me.", "The Big Lebowski"),
        ("The Dude abides.", "The Big Lebowski"),
        ("This is what happens when you find a stranger in the Alps!", "The Big Lebowski"),
        ("Yeah, well, you know, that's just like, your opinion, man.", "The Big Lebowski"),
        ("I drink your milkshake!", "There Will Be Blood"),
        ("I am the one who knocks.", "Breaking Bad"),
        ("Say my name.", "Breaking Bad"),
        ("They're taking the hobbits to Isengard!", "The Lord of the Rings"),
        ("My precious.", "The Lord of the Rings"),
        ("You shall not pass!", "The Lord of the Rings"),
        ("Even the smallest person can change the course of the future.", "The Lord of the Rings"),
        ("One does not simply walk into Mordor.", "The Lord of the Rings"),
        ("It's only a wafer-thin mint.", "Monty Python's The Meaning of Life"),
        ("This parrot is no more! It has ceased to be!", "Monty Python's Flying Circus"),
        ("I'm not a smart man, but I know what love is.", "Forrest Gump"),
        ("Run, Forrest, run!", "Forrest Gump"),
        ("Mama always said dying was a part of life.", "Forrest Gump"),
        ("Wilsoooon!", "Cast Away"),
        ("I wish I knew how to quit you.", "Brokeback Mountain"),
        ("I'm just one stomach flu away from my goal weight.", "The Devil Wears Prada"),
        ("That's what I do. I drink, and I know things.", "Game of Thrones"),
        ("Hold the door.", "Game of Thrones"),
        ("I solemnly swear that I am up to no good.", "Harry Potter"),
        ("You're a wizard, Harry.", "Harry Potter"),
        ("After all this time? Always.", "Harry Potter"),
        ("Why is the rum gone?", "Pirates of the Caribbean"),
        ("This is the day you will always remember as the day you almost caught Captain Jack Sparrow.", "Pirates of the Caribbean"),
        ("The code is more what you'd call guidelines than actual rules.", "Pirates of the Caribbean"),
        ("Savvy?", "Pirates of the Caribbean"),
    ]
    quotes = quotes[:100] + extra_ticker_quotes()
    existing = {
        row["quote"] for row in db.execute("SELECT quote FROM movie_quotes")
    }
    new_rows = [(q, src) for q, src in quotes if q not in existing]
    if new_rows:
        db.executemany(
            "INSERT INTO movie_quotes (quote, movie) VALUES (?, ?)",
            new_rows,
        )


# Whole citrus fruit is the inventory item; twist/wedge/peel is prep, not a SKU.
_CITRUS_GARNISH_VARIANTS = {
    "Lemon twist": "Lemon",
    "Lemon wedge": "Lemon",
    "Lemon peel": "Lemon",
    "Lemon wheel": "Lemon",
    "Lemon zest": "Lemon",
    "Lemon slice": "Lemon",
    "Orange twist": "Orange",
    "Orange wedge": "Orange",
    "Orange peel": "Orange",
    "Orange wheel": "Orange",
    "Orange zest": "Orange",
    "Orange slice": "Orange",
    "Lime twist": "Lime",
    "Lime wedge": "Lime",
    "Lime peel": "Lime",
    "Lime wheel": "Lime",
    "Lime zest": "Lime",
    "Lime slice": "Lime",
}


def _collapse_citrus_garnish_ingredients(db):
    """Map lemon/orange/lime garnish variants onto the whole fruit."""
    rows = db.execute("SELECT id, name, in_filter, sort_order FROM ingredients").fetchall()
    by_name = {r["name"]: r for r in rows}
    variants = {
        name: base
        for name, base in _CITRUS_GARNISH_VARIANTS.items()
        if name in by_name
    }
    if not variants:
        return

    bases_needed = sorted(set(variants.values()))
    sort_for_base = {"Lime": 20, "Orange": 30, "Lemon": 50}
    for base in bases_needed:
        if base in by_name:
            continue
        variant_rows = [by_name[n] for n, b in variants.items() if b == base]
        in_filter = 1 if any(v["in_filter"] for v in variant_rows) else 0
        sort_order = sort_for_base.get(base, min(v["sort_order"] for v in variant_rows))
        db.execute(
            """
            INSERT INTO ingredients (name, category, sort_order, in_filter)
            VALUES (?, 'Garnish', ?, ?)
            """,
            (base, sort_order, in_filter),
        )
        row = db.execute(
            "SELECT id, name, in_filter, sort_order FROM ingredients WHERE name = ?",
            (base,),
        ).fetchone()
        by_name[base] = row

    for base in bases_needed:
        variant_rows = [by_name[n] for n, b in variants.items() if b == base]
        if any(v["in_filter"] for v in variant_rows):
            db.execute(
                "UPDATE ingredients SET in_filter = 1 WHERE id = ?",
                (by_name[base]["id"],),
            )

    for variant_name, base in variants.items():
        variant_id = by_name[variant_name]["id"]
        base_id = by_name[base]["id"]
        lines = db.execute(
            """
            SELECT id, recipe_id FROM recipe_ingredients
            WHERE ingredient_id = ?
            """,
            (variant_id,),
        ).fetchall()
        for line in lines:
            existing = db.execute(
                """
                SELECT id FROM recipe_ingredients
                WHERE recipe_id = ? AND ingredient_id = ?
                """,
                (line["recipe_id"], base_id),
            ).fetchone()
            if existing:
                db.execute(
                    "DELETE FROM recipe_ingredients WHERE id = ?", (line["id"],)
                )
            else:
                db.execute(
                    "UPDATE recipe_ingredients SET ingredient_id = ? WHERE id = ?",
                    (base_id, line["id"]),
                )
        db.execute("DELETE FROM ingredients WHERE id = ?", (variant_id,))


def _merge_named_ingredients(db, mapping, default_category="Other", sort_for_base=None):
    """Fold alias ingredient names onto a single canonical row."""
    sort_for_base = sort_for_base or {}
    rows = db.execute(
        "SELECT id, name, category, in_filter, sort_order FROM ingredients"
    ).fetchall()
    groups = {}
    for row in rows:
        canon = mapping.get(row["name"])
        if not canon:
            continue
        groups.setdefault(canon, []).append(row)
    if not groups:
        return

    for canon, members in groups.items():
        members.sort(
            key=lambda m: (
                0 if m["name"] == canon else 1,
                0 if m["name"].casefold() == canon.casefold() else 1,
                m["id"],
            )
        )
        survivor = members[0]
        if survivor["name"] != canon:
            db.execute(
                "UPDATE ingredients SET name = ? WHERE id = ?",
                (canon, survivor["id"]),
            )
        if default_category:
            db.execute(
                "UPDATE ingredients SET category = ? WHERE id = ?",
                (default_category, survivor["id"]),
            )
        if canon in sort_for_base:
            db.execute(
                "UPDATE ingredients SET sort_order = ? WHERE id = ?",
                (sort_for_base[canon], survivor["id"]),
            )
        if any(m["in_filter"] for m in members):
            db.execute(
                "UPDATE ingredients SET in_filter = 1 WHERE id = ?",
                (survivor["id"],),
            )
        for member in members[1:]:
            lines = db.execute(
                """
                SELECT id, recipe_id FROM recipe_ingredients
                WHERE ingredient_id = ?
                """,
                (member["id"],),
            ).fetchall()
            for line in lines:
                existing = db.execute(
                    """
                    SELECT id FROM recipe_ingredients
                    WHERE recipe_id = ? AND ingredient_id = ?
                    """,
                    (line["recipe_id"], survivor["id"]),
                ).fetchone()
                if existing:
                    db.execute(
                        "DELETE FROM recipe_ingredients WHERE id = ?",
                        (line["id"],),
                    )
                else:
                    db.execute(
                        """
                        UPDATE recipe_ingredients
                        SET ingredient_id = ?
                        WHERE id = ?
                        """,
                        (survivor["id"], line["id"]),
                    )
            db.execute("DELETE FROM ingredients WHERE id = ?", (member["id"],))


def _unify_coffee_liqueur_as_kahlua(db):
    """Coffee liqueur and Kahlúa are the same bottle; stock it as Kahlua."""
    _merge_named_ingredients(
        db,
        {
            "Coffee liqueur": "Kahlua",
            "Kahlúa": "Kahlua",
            "Kahlua": "Kahlua",
        },
        default_category="Spirit",
        sort_for_base={"Kahlua": 120},
    )
    for old, new in (
        ("coffee liqueur", "Kahlua"),
        ("Coffee liqueur", "Kahlua"),
        ("Kahlúa", "Kahlua"),
    ):
        for field in ("description", "instructions", "notes"):
            db.execute(
                f"UPDATE recipes SET {field} = replace({field}, ?, ?) "
                f"WHERE {field} LIKE ?",
                (old, new, f"%{old}%"),
            )


def _replace_sweet_and_sour_mix(db):
    """Bottled sour mix is not stocked; recipes use lemon, lime, and simple syrup."""
    sour = db.execute(
        "SELECT id FROM ingredients WHERE name = ?",
        ("Sweet and sour mix",),
    ).fetchone()
    if not sour:
        return
    sour_id = sour["id"]
    bases = []
    for name in ("Lemon juice", "Lime juice", "Simple syrup"):
        row = db.execute("SELECT id FROM ingredients WHERE name = ?", (name,)).fetchone()
        if not row:
            return
        bases.append(row["id"])

    lines = db.execute(
        """
        SELECT id, recipe_id, measure, necessary, sort_order
        FROM recipe_ingredients
        WHERE ingredient_id = ?
        """,
        (sour_id,),
    ).fetchall()
    for line in lines:
        measure = (line["measure"] or "").strip()
        low = measure.lower()
        if "fill" in low or "splash" in low:
            part_measure = "equal parts, to fill"
        elif "1½" in measure or "1 1/2" in measure or "1.5" in measure:
            part_measure = "½ oz"
        else:
            part_measure = measure or "equal parts"
        for offset, ing_id in enumerate(bases):
            existing = db.execute(
                """
                SELECT id FROM recipe_ingredients
                WHERE recipe_id = ? AND ingredient_id = ?
                """,
                (line["recipe_id"], ing_id),
            ).fetchone()
            if existing:
                continue
            db.execute(
                """
                INSERT INTO recipe_ingredients
                    (recipe_id, ingredient_id, measure, necessary, sort_order)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    line["recipe_id"],
                    ing_id,
                    part_measure,
                    line["necessary"],
                    line["sort_order"] + offset,
                ),
            )
        db.execute("DELETE FROM recipe_ingredients WHERE id = ?", (line["id"],))

    db.execute("DELETE FROM ingredients WHERE id = ?", (sour_id,))

    sling = db.execute(
        "SELECT id FROM recipes WHERE name = ?", ("Singapore Sling",)
    ).fetchone()
    if sling:
        db.execute(
            """
            UPDATE recipes
            SET instructions = ?, notes = ?
            WHERE id = ?
            """,
            (
                "Fill a shaker with ice.\n"
                "Add gin, lemon juice, lime juice, simple syrup, and grenadine.\n"
                "Shake well.\n"
                "Strain into an ice-filled collins glass.\n"
                "Top with club soda.\n"
                "Float the cherry brandy on top.",
                "Homemade sour is equal parts lemon juice, lime juice, and simple syrup. "
                "Soda water is club soda.",
                sling["id"],
            ),
        )
    sheets = db.execute(
        "SELECT id FROM recipes WHERE name = ?", ("Between the Sheets",)
    ).fetchone()
    if sheets:
        db.execute(
            """
            UPDATE recipes
            SET description = ?, instructions = ?
            WHERE id = ?
            """,
            (
                "Brandy, rum, and triple sec lengthened with homemade sour "
                "(lemon juice, lime juice, and simple syrup).",
                "Pour brandy, white rum, and triple sec into an ice-filled tumbler.\n"
                "Fill with homemade sour — equal parts lemon juice, lime juice, "
                "and simple syrup.",
                sheets["id"],
            ),
        )
    for old, new in (
        ("sweet and sour mix", "homemade sour"),
        ("Sweet and sour mix", "homemade sour"),
        ("sour mix", "homemade sour"),
    ):
        for field in ("description", "instructions", "notes"):
            db.execute(
                f"UPDATE recipes SET {field} = replace({field}, ?, ?) "
                f"WHERE {field} LIKE ?",
                (old, new, f"%{old}%"),
            )


# Stock separately; any member of a group can stand in for the others.
DEFAULT_SUBSTITUTE_GROUPS = (
    ("Cognac", "Brandy"),
    ("Peach liqueur", "Apricot brandy", "Peach schnapps", "Peach brandy"),
)


def _add_substitute_pair(db, a_id, b_id):
    if not a_id or not b_id or a_id == b_id:
        return False
    db.execute(
        """
        INSERT OR IGNORE INTO ingredient_substitutes (ingredient_id, substitute_id)
        VALUES (?, ?)
        """,
        (a_id, b_id),
    )
    db.execute(
        """
        INSERT OR IGNORE INTO ingredient_substitutes (ingredient_id, substitute_id)
        VALUES (?, ?)
        """,
        (b_id, a_id),
    )
    return True


def _seed_ingredient_substitutes(db):
    """Install default swap groups once; later edits stay in the table."""
    n = db.execute("SELECT COUNT(*) FROM ingredient_substitutes").fetchone()[0]
    if n:
        return
    by_name = {
        r["name"]: r["id"]
        for r in db.execute("SELECT id, name FROM ingredients")
    }
    for group in DEFAULT_SUBSTITUTE_GROUPS:
        ids = [by_name[name] for name in group if name in by_name]
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                _add_substitute_pair(db, a, b)


CATALOG_RECIPES = (
    {
        "name": "Vieux Carré",
        "description": "Rye, cognac, vermouth, and Bénédictine — the French Quarter in a mixing glass.",
        "instructions": "Stir rye, cognac, sweet vermouth, Bénédictine, and both bitters with ice until cold.\nStrain into a chilled rocks glass over a large cube (or serve up in a coupe).\nExpress a lemon twist over the drink.",
        "glassware": "Rocks",
        "notes": "Hotel Monteleone, New Orleans. Stir it like a Manhattan that packed for the Quarter.",
        "category": "Cocktail",
        "tags": "whiskey, classic, New Orleans, stirred",
        "lines": [
            ("Rye whiskey", "1 oz", 1, 10),
            ("Cognac", "1 oz", 1, 20),
            ("Sweet vermouth", "1 oz", 1, 30),
            ("Bénédictine", "¼ oz", 1, 40),
            ("Peychaud's bitters", "2 dashes", 1, 50),
            ("Angostura bitters", "2 dashes", 1, 60),
            ("Ice", "for stirring", 0, 70),
            ("Lemon", "1", 0, 80),
        ],
    },
    {
        "name": "New York Sour",
        "description": "A rye whiskey sour with a red-wine float — tart, then velvety.",
        "instructions": "Shake rye, lemon juice, and simple syrup hard with ice (egg white optional: dry-shake first, then shake with ice).\nStrain into a rocks glass over fresh ice.\nSlowly pour the red wine over the back of a spoon so it floats.\nCherry optional.",
        "glassware": "Rocks",
        "notes": "The wine float is the whole point. Rye is traditional; bourbon is rounder.",
        "category": "Cocktail",
        "tags": "whiskey, sour, classic",
        "lines": [
            ("Rye whiskey", "2 oz", 1, 10),
            ("Lemon juice", "1 oz", 1, 20),
            ("Simple syrup", "¾ oz", 1, 30),
            ("Red wine", "½ oz", 1, 40),
            ("Egg white", "1", 0, 50),
            ("Ice", "as needed", 0, 60),
            ("Maraschino cherry", "1", 0, 70),
            ("Lemon", "1", 0, 80),
        ],
    },
    {
        "name": "Scofflaw",
        "description": "Rye, dry vermouth, lemon, and grenadine — the Prohibition drink named for people who ignored Prohibition.",
        "instructions": "Shake rye, dry vermouth, lemon juice, grenadine, and bitters with ice.\nStrain into a chilled coupe.\nLemon twist optional.",
        "glassware": "Coupe",
        "notes": "Harry's New York Bar, Paris, 1924. Orange bitters if you have them; Angostura is close enough.",
        "category": "Cocktail",
        "tags": "whiskey, classic, prohibition, sour",
        "lines": [
            ("Rye whiskey", "2 oz", 1, 10),
            ("Dry vermouth", "1 oz", 1, 20),
            ("Lemon juice", "¾ oz", 1, 30),
            ("Grenadine", "¾ oz", 1, 40),
            ("Angostura bitters", "2 dashes", 0, 50),
            ("Ice", "for shaking", 0, 60),
            ("Lemon", "1", 0, 70),
        ],
    },
)


# Bottles that should exist even on a live DB that already ran the first-time seed.
CATALOG_INGREDIENTS = (
    ("Grand Marnier", "Spirit", 82),
)


def _ensure_named_ingredients(db):
    """Insert catalog bottles if missing; do not change existing rows."""
    for name, category, sort_order in CATALOG_INGREDIENTS:
        db.execute(
            """
            INSERT INTO ingredients (name, category, sort_order, in_filter)
            SELECT ?, ?, ?, 1
            WHERE NOT EXISTS (
                SELECT 1 FROM ingredients WHERE name = ?
            )
            """,
            (name, category, sort_order, name),
        )


def _ensure_catalog_recipes(db):
    """Insert extra house recipes when missing; never overwrite bartender edits."""
    existing = {
        r["name"] for r in db.execute("SELECT name FROM recipes").fetchall()
    }
    by_name = {
        r["name"]: r["id"]
        for r in db.execute("SELECT id, name FROM ingredients").fetchall()
    }
    max_sort = db.execute(
        "SELECT COALESCE(MAX(sort_order), 0) FROM recipes"
    ).fetchone()[0]
    added = 0
    for recipe in CATALOG_RECIPES:
        if recipe["name"] in existing:
            continue
        needed = [
            name
            for name, _measure, necessary, _sort in recipe["lines"]
            if necessary and name not in by_name
        ]
        if needed:
            continue
        added += 1
        cur = db.execute(
            """
            INSERT INTO recipes
                (name, description, instructions, glassware, notes, category,
                 tags, published, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                recipe["name"],
                recipe["description"],
                recipe["instructions"],
                recipe["glassware"],
                recipe["notes"],
                recipe["category"],
                recipe["tags"],
                max_sort + added * 10,
            ),
        )
        recipe_id = cur.lastrowid
        for ing_name, measure, necessary, sort_order in recipe["lines"]:
            ingredient_id = by_name.get(ing_name)
            if not ingredient_id:
                continue
            db.execute(
                """
                INSERT INTO recipe_ingredients
                    (recipe_id, ingredient_id, measure, necessary, sort_order)
                VALUES (?, ?, ?, ?, ?)
                """,
                (recipe_id, ingredient_id, measure, necessary, sort_order),
            )


def _ensure_themes(db):
    """Upsert the theme catalog and assign drinks from RECIPE_THEMES."""
    for slug, name, family, description, sort_order in THEMES:
        row = db.execute("SELECT id FROM themes WHERE slug = ?", (slug,)).fetchone()
        if row:
            db.execute(
                """
                UPDATE themes
                SET name = ?, family = ?, description = ?, sort_order = ?
                WHERE slug = ?
                """,
                (name, family, description, sort_order, slug),
            )
        else:
            db.execute(
                """
                INSERT INTO themes (slug, name, family, description, sort_order)
                VALUES (?, ?, ?, ?, ?)
                """,
                (slug, name, family, description, sort_order),
            )
    by_slug = {
        r["slug"]: r["id"]
        for r in db.execute("SELECT id, slug FROM themes")
    }
    by_recipe = {
        r["name"]: r["id"]
        for r in db.execute("SELECT id, name FROM recipes")
    }
    for recipe_name, slugs in RECIPE_THEMES.items():
        recipe_id = by_recipe.get(recipe_name)
        if not recipe_id:
            continue
        for slug in slugs:
            theme_id = by_slug.get(slug)
            if not theme_id:
                continue
            db.execute(
                """
                INSERT OR IGNORE INTO recipe_themes (recipe_id, theme_id)
                VALUES (?, ?)
                """,
                (recipe_id, theme_id),
            )


def fetch_themes():
    return get_db().execute(
        """
        SELECT * FROM themes
        ORDER BY family ASC, sort_order ASC, name COLLATE NOCASE ASC
        """
    ).fetchall()


def fetch_theme_by_slug(slug):
    if slug == FEATURED_THEME_SLUG:
        return featured_theme_dict()
    return (
        get_db()
        .execute("SELECT * FROM themes WHERE slug = ?", (slug,))
        .fetchone()
    )


def featured_ingredient_names():
    return [
        r["name"]
        for r in get_db().execute(
            """
            SELECT name FROM ingredients
            WHERE featured = 1
            ORDER BY name COLLATE NOCASE ASC
            """
        ).fetchall()
    ]


def featured_theme_dict():
    """Virtual theme: makeable drinks that use any starred ingredient."""
    names = featured_ingredient_names()
    if not names:
        desc = "Star bottles on What’s On Hand to push them tonight."
    elif len(names) == 1:
        desc = f"Makeable drinks using {names[0]}."
    elif len(names) == 2:
        desc = f"Makeable drinks using {names[0]} or {names[1]}."
    else:
        desc = (
            "Makeable drinks using "
            + ", ".join(names[:-1])
            + f", or {names[-1]}."
        )
    return {
        "id": None,
        "slug": FEATURED_THEME_SLUG,
        "name": "Featured",
        "family": "",
        "description": desc,
        "sort_order": -1,
        "featured_names": names,
    }


def _theme_is_featured(theme):
    return theme is not None and theme["slug"] == FEATURED_THEME_SLUG


def _featured_recipe_sql():
    return """
      EXISTS (
        SELECT 1
        FROM recipe_ingredients ri_f
        JOIN ingredients i_f ON i_f.id = ri_f.ingredient_id
        WHERE ri_f.recipe_id = r.id
          AND i_f.featured = 1
      )
    """


def fetch_recipe_theme_ids(recipe_id):
    rows = get_db().execute(
        "SELECT theme_id FROM recipe_themes WHERE recipe_id = ?",
        (recipe_id,),
    ).fetchall()
    return {r["theme_id"] for r in rows}


def save_recipe_themes(db, recipe_id, form):
    db.execute("DELETE FROM recipe_themes WHERE recipe_id = ?", (recipe_id,))
    for raw in form.getlist("theme_id"):
        try:
            theme_id = int(raw)
        except ValueError:
            continue
        db.execute(
            """
            INSERT OR IGNORE INTO recipe_themes (recipe_id, theme_id)
            VALUES (?, ?)
            """,
            (recipe_id, theme_id),
        )


def theme_picker_list(makeable_counts=None):
    """Flat, A–Z theme cards for the guest theme picker."""
    counts = makeable_counts or {}
    rows = get_db().execute(
        """
        SELECT * FROM themes
        ORDER BY name COLLATE NOCASE ASC
        """
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["makeable_count"] = counts.get(row["id"], 0)
        item["palette_style"] = palette_style(row["slug"])
        items.append(item)
    featured = featured_theme_card()
    if featured:
        items.insert(0, featured)
    return items


def featured_theme_card():
    """Guest/TV tile for the featured-ingredient theme, or None if nothing is starred."""
    theme = featured_theme_dict()
    if not theme["featured_names"]:
        return None
    item = dict(theme)
    item["makeable_count"] = len(fetch_makeable_recipes(featured_ingredients=True))
    item["palette_style"] = palette_style(FEATURED_THEME_SLUG)
    return item


def theme_family_catalog(makeable_counts=None):
    """Families → theme dicts for the master page / TV picker."""
    counts = makeable_counts or {}
    rows = fetch_themes()
    by_family = {fam["key"]: [] for fam in THEME_FAMILIES}
    family_meta = {fam["key"]: fam for fam in THEME_FAMILIES}
    for row in rows:
        item = dict(row)
        item["makeable_count"] = counts.get(row["id"], 0)
        item["palette_style"] = palette_style(row["slug"])
        by_family.setdefault(row["family"], []).append(item)
    catalog = []
    for fam in THEME_FAMILIES:
        catalog.append(
            {
                "key": fam["key"],
                "name": fam["name"],
                "blurb": fam["blurb"],
                "themes": by_family.get(fam["key"], []),
            }
        )
    extras = [k for k in by_family if k not in family_meta]
    for key in extras:
        catalog.append(
            {
                "key": key,
                "name": key.replace("-", " ").title(),
                "blurb": "",
                "themes": by_family[key],
            }
        )
    return catalog


def makeable_counts_by_theme():
    """theme_id → count of currently makeable published recipes."""
    covered = _ingredient_covered_sql("i")
    rows = get_db().execute(
        f"""
        SELECT rt.theme_id AS theme_id, COUNT(*) AS n
        FROM recipe_themes rt
        JOIN recipes r ON r.id = rt.recipe_id
        WHERE r.published = 1
          AND NOT EXISTS (
            SELECT 1
            FROM recipe_ingredients ri
            JOIN ingredients i ON i.id = ri.ingredient_id
            WHERE ri.recipe_id = r.id
              AND ri.necessary = 1
              AND NOT {covered}
          )
        GROUP BY rt.theme_id
        """
    ).fetchall()
    return {r["theme_id"]: r["n"] for r in rows}


def _ingredient_covered_sql(alias="i"):
    """True when this ingredient is on hand or a listed substitute is."""
    return f"""
        (
          {alias}.in_filter = 1
          OR EXISTS (
            SELECT 1
            FROM ingredient_substitutes s
            JOIN ingredients alt ON alt.id = s.substitute_id
            WHERE s.ingredient_id = {alias}.id
              AND alt.in_filter = 1
          )
        )
    """


def fetch_substitute_pairs():
    """Undirected pairs for admin, one row per couple."""
    return get_db().execute(
        """
        SELECT a.id AS a_id, a.name AS a_name, b.id AS b_id, b.name AS b_name
        FROM ingredient_substitutes s
        JOIN ingredients a ON a.id = s.ingredient_id
        JOIN ingredients b ON b.id = s.substitute_id
        WHERE a.id < b.id
        ORDER BY a.name COLLATE NOCASE, b.name COLLATE NOCASE
        """
    ).fetchall()


def _seed(db):
    ingredients = [
        # name, category, sort_order
        ("White rum", "Spirit", 10),
        ("Gin", "Spirit", 20),
        ("Vodka", "Spirit", 30),
        ("Dark rum", "Spirit", 40),
        ("Bourbon", "Spirit", 50),
        ("Rye whiskey", "Spirit", 55),
        ("Tequila", "Spirit", 60),
        ("Ouzo", "Spirit", 70),
        ("Triple sec", "Spirit", 80),
        ("Grand Marnier", "Spirit", 82),
        ("Dry vermouth", "Spirit", 90),
        ("Sweet vermouth", "Spirit", 100),
        ("Campari", "Spirit", 110),
        ("Kahlua", "Spirit", 120),
        ("Cola", "Mixer", 10),
        ("Tonic water", "Mixer", 20),
        ("Club soda", "Mixer", 30),
        ("Ginger beer", "Mixer", 40),
        ("Lime juice", "Mixer", 50),
        ("Simple syrup", "Mixer", 60),
        ("Lemonade", "Mixer", 70),
        ("Lemon juice", "Mixer", 80),
        ("Orange juice", "Mixer", 90),
        ("Cranberry juice", "Mixer", 100),
        ("Grapefruit juice", "Mixer", 110),
        ("Heavy cream", "Mixer", 120),
        ("Angostura bitters", "Mixer", 130),
        ("Mint leaves", "Garnish", 10),
        ("Lime", "Garnish", 20),
        ("Orange", "Garnish", 30),
        ("Cucumber", "Garnish", 40),
        ("Lemon", "Garnish", 50),
        ("Maraschino cherry", "Garnish", 80),
        ("Ice", "Other", 10),
        ("Sugar cube", "Other", 20),
        ("Salt", "Other", 30),
    ]
    db.executemany(
        """
        INSERT INTO ingredients (name, category, sort_order, in_filter)
        VALUES (?, ?, ?, 1)
        """,
        ingredients,
    )

    rows = db.execute("SELECT id, name FROM ingredients").fetchall()
    by_name = {r["name"]: r["id"] for r in rows}

    recipes = [
        {
            "name": "Rum and Coke",
            "description": "Classic highball — rum, cola, and a lime if you have it.",
            "instructions": "Fill a highball with ice.\nAdd rum.\nTop with cola and stir gently.\nGarnish with a lime wedge.",
            "glassware": "Highball",
            "notes": "Use whatever cola you like; Mexican Coke is a nice upgrade.",
            "category": "Cocktail",
            "tags": "classic, easy, highball",
            "sort_order": 10,
            "lines": [
                ("White rum", "2 oz", 1, 10),
                ("Cola", "fill", 1, 20),
                ("Ice", "as needed", 0, 30),
                ("Lime", "1", 0, 40),
            ],
        },
        {
            "name": "Gin & Tonic",
            "description": "Crisp, bitter, and endlessly tweakable.",
            "instructions": "Fill a highball or copa with ice.\nAdd gin.\nTop with tonic and give a gentle stir.\nGarnish with a lime wedge or an orange twist.",
            "glassware": "Highball",
            "notes": "A good tonic matters as much as the gin.",
            "category": "Cocktail",
            "tags": "classic, easy, refreshing",
            "sort_order": 20,
            "lines": [
                ("Gin", "2 oz", 1, 10),
                ("Tonic water", "fill", 1, 20),
                ("Ice", "as needed", 0, 30),
                ("Lime", "1", 0, 40),
                ("Orange", "1", 0, 50),
            ],
        },
        {
            "name": "Vodka Soda",
            "description": "Light, clean, and low-calorie.",
            "instructions": "Fill a highball with ice.\nAdd vodka.\nTop with club soda.\nSqueeze in a lime wedge if desired.",
            "glassware": "Highball",
            "notes": "",
            "category": "Cocktail",
            "tags": "easy, light",
            "sort_order": 30,
            "lines": [
                ("Vodka", "2 oz", 1, 10),
                ("Club soda", "fill", 1, 20),
                ("Ice", "as needed", 0, 30),
                ("Lime", "1", 0, 40),
            ],
        },
        {
            "name": "Dark 'n' Stormy",
            "description": "Dark rum and spicy ginger beer.",
            "instructions": "Fill a highball with ice.\nAdd dark rum.\nTop with ginger beer.\nGarnish with a lime wedge.",
            "glassware": "Highball",
            "notes": "Traditionally Gosling's Black Seal; any dark rum works at home.",
            "category": "Cocktail",
            "tags": "spicy, classic",
            "sort_order": 40,
            "lines": [
                ("Dark rum", "2 oz", 1, 10),
                ("Ginger beer", "fill", 1, 20),
                ("Ice", "as needed", 0, 30),
                ("Lime", "1", 0, 40),
            ],
        },
        {
            "name": "Mojito",
            "description": "Rum, mint, lime, and a little sweetness.",
            "instructions": "Muddle mint gently with simple syrup and lime juice in a glass.\nAdd rum and ice.\nTop with club soda and stir.\nGarnish with a mint sprig and a lime wedge.",
            "glassware": "Highball",
            "notes": "Don't pulverize the mint — bruise it.",
            "category": "Cocktail",
            "tags": "classic, mint, summer",
            "sort_order": 50,
            "lines": [
                ("White rum", "2 oz", 1, 10),
                ("Lime juice", "1 oz", 1, 20),
                ("Simple syrup", "¾ oz", 1, 30),
                ("Mint leaves", "8–10", 1, 40),
                ("Club soda", "splash", 1, 50),
                ("Ice", "as needed", 0, 60),
                ("Lime", "1", 0, 70),
            ],
        },
        {
            "name": "Virgin Mojito",
            "description": "All the mint-lime refreshment, no booze.",
            "instructions": "Muddle mint with simple syrup and lime juice.\nAdd ice, top with club soda, and stir.\nGarnish with mint and a lime wedge.",
            "glassware": "Highball",
            "notes": "Great when the spirits are out or for non-drinkers.",
            "category": "Mocktail",
            "tags": "mocktail, mint, refreshing",
            "sort_order": 60,
            "lines": [
                ("Lime juice", "1 oz", 1, 10),
                ("Simple syrup", "¾ oz", 1, 20),
                ("Mint leaves", "8–10", 1, 30),
                ("Club soda", "fill", 1, 40),
                ("Ice", "as needed", 0, 50),
                ("Lime", "1", 0, 60),
            ],
        },
        {
            "name": "Moscow Mule",
            "description": "Vodka, spicy ginger beer, and bright lime in a copper mug (or any glass).",
            "instructions": "Fill a mug or highball with ice.\nAdd vodka and lime juice.\nTop with ginger beer and stir gently.\nGarnish with a lime wedge.",
            "glassware": "Copper mug",
            "notes": "Ginger beer, not ginger ale — you want the spice.",
            "category": "Cocktail",
            "tags": "classic, easy, spicy",
            "sort_order": 70,
            "lines": [
                ("Vodka", "2 oz", 1, 10),
                ("Ginger beer", "4–6 oz", 1, 20),
                ("Lime juice", "½ oz", 1, 30),
                ("Ice", "as needed", 0, 40),
                ("Lime", "1", 0, 50),
            ],
        },
        {
            "name": "Ouzo Lemonade",
            "description": "Anise-forward ouzo lengthened with cold lemonade — simple and striking.",
            "instructions": "Fill a highball with ice.\nAdd ouzo.\nTop with lemonade and stir.\nGarnish with a lemon wedge.",
            "glassware": "Highball",
            "notes": "Start light on the ouzo if guests are new to anise.",
            "category": "Cocktail",
            "tags": "easy, anise, summer",
            "sort_order": 80,
            "lines": [
                ("Ouzo", "1½ oz", 1, 10),
                ("Lemonade", "fill", 1, 20),
                ("Ice", "as needed", 0, 30),
                ("Lemon", "1", 0, 40),
            ],
        },
        {
            "name": "Cumbersome",
            "description": "A cucumber–gin–lime martini: crisp, green, and not nearly as much trouble as the name.",
            "instructions": "Muddle a few cucumber slices gently in a shaker.\nAdd gin, lime juice, and simple syrup with ice.\nShake hard until well chilled.\nDouble-strain into a chilled coupe or martini glass.\nGarnish with a thin cucumber slice.",
            "glassware": "Martini / Coupe",
            "notes": "Optional splash of dry vermouth if you want it more classic-martini.",
            "category": "Cocktail",
            "tags": "gin, cucumber, martini",
            "sort_order": 90,
            "lines": [
                ("Gin", "2 oz", 1, 10),
                ("Lime juice", "¾ oz", 1, 20),
                ("Simple syrup", "½ oz", 1, 30),
                ("Cucumber", "3–4 slices", 1, 40),
                ("Dry vermouth", "¼ oz", 0, 50),
                ("Ice", "for shaking", 0, 60),
            ],
        },
        {
            "name": "Old Fashioned",
            "description": "Whiskey, sugar, bitters — the template cocktail.",
            "instructions": "Place a sugar cube in a rocks glass; soak with 2–3 dashes bitters and a splash of water.\nMuddle to a paste (or stir until dissolved).\nAdd whiskey and a large ice cube; stir until cold.\nExpress an orange peel over the glass and drop it in.\nCherry optional.",
            "glassware": "Rocks",
            "notes": "Bourbon is sweeter; rye is spicier. Simple syrup (¼ oz) works if you're out of sugar cubes.",
            "category": "Cocktail",
            "tags": "classic, whiskey, stirred",
            "sort_order": 100,
            "lines": [
                ("Bourbon", "2 oz", 1, 10),
                ("Angostura bitters", "2–3 dashes", 1, 20),
                ("Sugar cube", "1", 1, 30),
                ("Ice", "1 large cube", 0, 40),
                ("Orange", "1", 0, 50),
                ("Maraschino cherry", "1", 0, 60),
            ],
        },
        {
            "name": "Whiskey Sour",
            "description": "Bourbon, lemon, and a touch of sweet — bright and balanced.",
            "instructions": "Shake bourbon, lemon juice, and simple syrup hard with ice.\nStrain into a rocks glass over fresh ice (or serve up).\nGarnish with a cherry and/or a lemon wedge.",
            "glassware": "Rocks",
            "notes": "Optional egg white for a silky foam if you're feeling fancy.",
            "category": "Cocktail",
            "tags": "classic, sour, whiskey",
            "sort_order": 110,
            "lines": [
                ("Bourbon", "2 oz", 1, 10),
                ("Lemon juice", "¾ oz", 1, 20),
                ("Simple syrup", "¾ oz", 1, 30),
                ("Ice", "as needed", 0, 40),
                ("Maraschino cherry", "1", 0, 50),
                ("Lemon", "1", 0, 60),
            ],
        },
        {
            "name": "Margarita",
            "description": "Tequila, orange liqueur, and lime — the backyard classic.",
            "instructions": "Optional: rim a glass with salt.\nShake tequila, triple sec, and lime juice with ice.\nStrain over fresh ice in a rocks glass (or serve up).\nGarnish with a lime wedge.",
            "glassware": "Rocks",
            "notes": "Equal parts works in a pinch: 1 oz each tequila, triple sec, lime.",
            "category": "Cocktail",
            "tags": "classic, tequila, easy",
            "sort_order": 120,
            "lines": [
                ("Tequila", "2 oz", 1, 10),
                ("Triple sec", "1 oz", 1, 20),
                ("Lime juice", "1 oz", 1, 30),
                ("Ice", "as needed", 0, 40),
                ("Salt", "rim", 0, 50),
                ("Lime", "1", 0, 60),
            ],
        },
        {
            "name": "Daiquiri",
            "description": "Rum, lime, sugar — three ingredients, no blender required.",
            "instructions": "Shake white rum, lime juice, and simple syrup with ice.\nStrain into a chilled coupe.\nNo garnish needed (lime wheel if you like).",
            "glassware": "Coupe",
            "notes": "The real daiquiri is shaken and tart, not frozen strawberry.",
            "category": "Cocktail",
            "tags": "classic, rum, sour",
            "sort_order": 130,
            "lines": [
                ("White rum", "2 oz", 1, 10),
                ("Lime juice", "1 oz", 1, 20),
                ("Simple syrup", "¾ oz", 1, 30),
                ("Ice", "for shaking", 0, 40),
                ("Lime", "1", 0, 50),
            ],
        },
        {
            "name": "Screwdriver",
            "description": "Vodka and orange juice — breakfast-adjacent and foolproof.",
            "instructions": "Fill a highball with ice.\nAdd vodka.\nTop with orange juice and stir.\nGarnish with an orange twist.",
            "glassware": "Highball",
            "notes": "Fresh OJ makes a bigger difference than fancy vodka.",
            "category": "Cocktail",
            "tags": "easy, classic, brunch",
            "sort_order": 140,
            "lines": [
                ("Vodka", "2 oz", 1, 10),
                ("Orange juice", "fill", 1, 20),
                ("Ice", "as needed", 0, 30),
                ("Orange", "1", 0, 40),
            ],
        },
        {
            "name": "Cosmopolitan",
            "description": "Vodka, triple sec, cranberry, and lime — pink and precise.",
            "instructions": "Shake vodka, triple sec, cranberry juice, and lime juice with ice.\nStrain into a chilled martini glass.\nGarnish with an orange or lime twist.",
            "glassware": "Martini",
            "notes": "Go easy on the cranberry so it stays tart, not juice-box.",
            "category": "Cocktail",
            "tags": "classic, vodka, shaken",
            "sort_order": 150,
            "lines": [
                ("Vodka", "1½ oz", 1, 10),
                ("Triple sec", "1 oz", 1, 20),
                ("Cranberry juice", "1 oz", 1, 30),
                ("Lime juice", "½ oz", 1, 40),
                ("Ice", "for shaking", 0, 50),
                ("Orange", "1", 0, 60),
            ],
        },
        {
            "name": "Tom Collins",
            "description": "Gin sour lengthened with soda — tall, lemony, and refreshing.",
            "instructions": "Shake gin, lemon juice, and simple syrup with ice.\nStrain into a tall glass over fresh ice.\nTop with club soda and stir once.\nGarnish with a lemon wedge and a cherry if you have one.",
            "glassware": "Collins",
            "notes": "Same formula as a gin sour, then fizz.",
            "category": "Cocktail",
            "tags": "classic, gin, easy",
            "sort_order": 160,
            "lines": [
                ("Gin", "2 oz", 1, 10),
                ("Lemon juice", "1 oz", 1, 20),
                ("Simple syrup", "½ oz", 1, 30),
                ("Club soda", "top", 1, 40),
                ("Ice", "as needed", 0, 50),
                ("Lemon", "1", 0, 60),
                ("Maraschino cherry", "1", 0, 70),
            ],
        },
        {
            "name": "Negroni",
            "description": "Equal parts gin, Campari, and sweet vermouth — bitter and beautiful.",
            "instructions": "Stir gin, Campari, and sweet vermouth with ice until very cold.\nStrain over a large cube in a rocks glass.\nExpress an orange peel over the drink and drop it in.",
            "glassware": "Rocks",
            "notes": "Equal parts is the law. Build in the glass and stir if you prefer.",
            "category": "Cocktail",
            "tags": "classic, bitter, stirred",
            "sort_order": 170,
            "lines": [
                ("Gin", "1 oz", 1, 10),
                ("Campari", "1 oz", 1, 20),
                ("Sweet vermouth", "1 oz", 1, 30),
                ("Ice", "as needed", 0, 40),
                ("Orange", "1", 0, 50),
            ],
        },
        {
            "name": "Manhattan",
            "description": "Whiskey and sweet vermouth with bitters — polished and simple.",
            "instructions": "Stir whiskey, sweet vermouth, and bitters with ice until cold.\nStrain into a chilled coupe (or over ice in a rocks glass).\nGarnish with a cherry.",
            "glassware": "Coupe",
            "notes": "Rye is traditional; bourbon is softer. 2:1 whiskey to vermouth is the usual home ratio.",
            "category": "Cocktail",
            "tags": "classic, whiskey, stirred",
            "sort_order": 180,
            "lines": [
                ("Rye whiskey", "2 oz", 1, 10),
                ("Sweet vermouth", "1 oz", 1, 20),
                ("Angostura bitters", "2 dashes", 1, 30),
                ("Ice", "for stirring", 0, 40),
                ("Maraschino cherry", "1", 0, 50),
            ],
        },
        {
            "name": "White Russian",
            "description": "Vodka, Kahlua, and cream — dessert in a glass.",
            "instructions": "Fill a rocks glass with ice.\nAdd vodka and Kahlua; stir.\nFloat or stir in heavy cream.",
            "glassware": "Rocks",
            "notes": "Half-and-half works if you don't have heavy cream.",
            "category": "Cocktail",
            "tags": "easy, creamy, classic",
            "sort_order": 190,
            "lines": [
                ("Vodka", "2 oz", 1, 10),
                ("Kahlua", "1 oz", 1, 20),
                ("Heavy cream", "1 oz", 1, 30),
                ("Ice", "as needed", 0, 40),
            ],
        },
        {
            "name": "Paloma",
            "description": "Tequila and grapefruit — Mexico's favorite tequila highball.",
            "instructions": "Fill a highball with ice.\nAdd tequila and a squeeze of lime.\nTop with grapefruit juice and a splash of club soda.\nSalt rim optional.\nGarnish with a lime wedge.",
            "glassware": "Highball",
            "notes": "Jarritos Toronja or Squirt can replace juice + soda if you have them.",
            "category": "Cocktail",
            "tags": "tequila, easy, refreshing",
            "sort_order": 200,
            "lines": [
                ("Tequila", "2 oz", 1, 10),
                ("Grapefruit juice", "3–4 oz", 1, 20),
                ("Lime juice", "½ oz", 1, 30),
                ("Club soda", "splash", 1, 40),
                ("Ice", "as needed", 0, 50),
                ("Salt", "rim", 0, 60),
                ("Lime", "1", 0, 70),
            ],
        },
    ]

    for r in recipes:
        cur = db.execute(
            """
            INSERT INTO recipes
                (name, description, instructions, glassware, notes, category, tags, published, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                r["name"],
                r["description"],
                r["instructions"],
                r["glassware"],
                r["notes"],
                r["category"],
                r["tags"],
                r["sort_order"],
            ),
        )
        recipe_id = cur.lastrowid
        for ing_name, measure, necessary, sort_order in r["lines"]:
            db.execute(
                """
                INSERT INTO recipe_ingredients
                    (recipe_id, ingredient_id, measure, necessary, sort_order)
                VALUES (?, ?, ?, ?, ?)
                """,
                (recipe_id, by_name[ing_name], measure, necessary, sort_order),
            )


def group_by_category(rows, key="category"):
    groups = {}
    for row in rows:
        groups.setdefault(row[key] or "Other", []).append(row)
    return groups


def parse_tags(tags_str):
    if not tags_str:
        return []
    return [t.strip() for t in tags_str.split(",") if t.strip()]


DEVICE_COOKIE = "bar_device"
DEVICE_COOKIE_MAX_AGE = 365 * 24 * 3600


def ensure_device_id():
    """Return (device_id, should_set_cookie)."""
    existing = (request.cookies.get(DEVICE_COOKIE) or "").strip()
    if existing and len(existing) <= 64:
        return existing, False
    return str(uuid.uuid4()), True


def attach_device_cookie(response, device_id, set_cookie):
    if set_cookie:
        response.set_cookie(
            DEVICE_COOKIE,
            device_id,
            max_age=DEVICE_COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
        )
    return response


def recipe_rating_summaries(recipe_ids):
    """Map recipe_id -> {avg, count}."""
    if not recipe_ids:
        return {}
    placeholders = ",".join("?" * len(recipe_ids))
    rows = get_db().execute(
        f"""
        SELECT recipe_id,
               AVG(stars) AS avg_stars,
               COUNT(*) AS vote_count
        FROM ratings
        WHERE recipe_id IN ({placeholders})
        GROUP BY recipe_id
        """,
        list(recipe_ids),
    ).fetchall()
    out = {}
    for row in rows:
        out[row["recipe_id"]] = {
            "avg": round(float(row["avg_stars"]), 1),
            "count": int(row["vote_count"]),
        }
    return out


def device_vote(recipe_id, device_id):
    if not device_id:
        return None
    row = get_db().execute(
        "SELECT stars FROM ratings WHERE recipe_id = ? AND device_id = ?",
        (recipe_id, device_id),
    ).fetchone()
    return int(row["stars"]) if row else None


# Categories treated as food / non-drink → appetizer thumbnail
FOOD_CATEGORIES = frozenset(
    {
        "appetizer",
        "appetizers",
        "food",
        "snack",
        "snacks",
        "bite",
        "bites",
        "side",
        "sides",
        "dessert",
        "desserts",
        "entree",
        "entrée",
        "main",
        "meal",
    }
)

# Map free-text glassware → static/glassware/<key>.svg
GLASSWARE_ICON_MAP = {
    "highball": "highball",
    "collins": "collins",
    "rocks": "rocks",
    "old fashioned": "rocks",
    "old-fashioned": "rocks",
    "lowball": "rocks",
    "coupe": "coupe",
    "champagne saucer": "coupe",
    "martini": "martini",
    "cocktail": "martini",
    "nick & nora": "coupe",
    "nick and nora": "coupe",
    "copper mug": "copper_mug",
    "mule mug": "copper_mug",
    "julep cup": "julep",
    "julep": "julep",
    "mug": "mug",
    "coffee mug": "mug",
    "coffee": "mug",
    "wine": "wine",
    "wine glass": "wine",
    "flute": "flute",
    "champagne flute": "flute",
    "stemmed glass": "wine",
    "stemmed": "wine",
    "tulip": "wine",
    "tulip glass": "wine",
    "pint": "highball",
    "beer": "highball",
    "shot": "default",
    "plate": "appetizer",
    "platter": "appetizer",
    "bowl": "appetizer",
    "board": "appetizer",
    "skewer": "appetizer",
}


def glassware_icon_key(glassware="", category=""):
    """
    Pick a thumbnail key for a recipe.
    Food categories / food-like glassware → appetizer; else map glass text.
    """
    cat = (category or "").strip().lower()
    if cat in FOOD_CATEGORIES:
        return "appetizer"

    raw = (glassware or "").strip().lower()
    if not raw:
        # No vessel named — default glass for drink categories, appetizer otherwise
        if cat in ("cocktail", "mocktail", "coffee", "tea", "drink", "drinks", "spirit", "other", ""):
            return "default"
        return "appetizer"

    # Exact and substring match (handles "Martini / Coupe", "Highball glass", …)
    if raw in GLASSWARE_ICON_MAP:
        return GLASSWARE_ICON_MAP[raw]

    # Prefer more specific tokens first
    for token in (
        "copper mug",
        "coffee mug",
        "julep cup",
        "wine glass",
        "champagne flute",
        "champagne saucer",
        "old fashioned",
        "old-fashioned",
        "nick & nora",
        "nick and nora",
        "martini",
        "coupe",
        "collins",
        "highball",
        "rocks",
        "lowball",
        "julep",
        "flute",
        "wine",
        "mug",
        "pint",
        "plate",
        "platter",
        "bowl",
        "board",
        "skewer",
    ):
        if token in raw and token in GLASSWARE_ICON_MAP:
            return GLASSWARE_ICON_MAP[token]

    return "default"


def glassware_icon_url(glassware="", category=""):
    key = glassware_icon_key(glassware, category)
    return url_for("static", filename=f"glassware/{key}.svg")


@app.context_processor
def inject_glassware_helpers():
    return {
        "glassware_icon_url": glassware_icon_url,
        "glassware_icon_key": glassware_icon_key,
    }


def get_setting(key, default=""):
    row = get_db().execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return default
    return row["value"] if row["value"] is not None else default


def bar_name():
    name = (get_setting(SETTING_BAR_NAME, DEFAULT_BAR_NAME) or "").strip()
    return name or DEFAULT_BAR_NAME


def set_setting(key, value):
    get_db().execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value if value is not None else ""),
    )


def get_all_settings():
    rows = get_db().execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def wifi_qr_escape(value):
    """Escape special characters for WIFI: QR payload fields."""
    if not value:
        return ""
    out = []
    for ch in value:
        if ch in ("\\", ";", ",", '"', ":"):
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def build_wifi_qr_payload(ssid, password, security="WPA", hidden=False):
    """
    Build a WIFI: QR string (MECARD-like) for phone network join.
    https://en.wikipedia.org/wiki/QR_code#Joining_a_Wi%E2%80%91Fi_network
    """
    sec = (security or "WPA").strip().upper()
    if sec in ("WPA2", "WPA3", "WPA/WPA2"):
        sec = "WPA"
    if sec not in ("WPA", "WEP", "NOPASS"):
        sec = "WPA"
    if sec == "NOPASS":
        sec = "nopass"
    hidden_flag = "true" if hidden else "false"
    parts = [
        f"T:{sec}",
        f"S:{wifi_qr_escape(ssid)}",
    ]
    if sec != "nopass":
        parts.append(f"P:{wifi_qr_escape(password)}")
    parts.append(f"H:{hidden_flag}")
    return "WIFI:" + ";".join(parts) + ";;"


def resolve_public_base_url():
    """Host-configured base URL, or the URL of the current request."""
    configured = (get_setting(SETTING_PUBLIC_BASE_URL) or "").strip().rstrip("/")
    if configured:
        return configured
    return request.url_root.rstrip("/")


def resolve_bar_url(ingredients=False, theme_slug=None):
    if ingredients:
        path = url_for("bar_ingredients")
    elif theme_slug:
        path = url_for("bar_theme", slug=theme_slug)
    else:
        path = url_for("bar")
    return resolve_public_base_url() + path


def make_qr_png(data, box_size=10, border=2):
    """Return PNG bytes for a QR code encoding `data`."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def fetch_ingredients():
    return get_db().execute(
        """
        SELECT * FROM ingredients
        ORDER BY category ASC, name COLLATE NOCASE ASC
        """
    ).fetchall()


def _recipe_scope_sql(theme_id=None, featured_ingredients=False, ingredient_id=None):
    """Extra WHERE clause + params to scope recipes by theme, featured, or ingredient."""
    if featured_ingredients:
        return f"AND {_featured_recipe_sql()}", []
    if ingredient_id is not None:
        return (
            """
          AND EXISTS (
            SELECT 1 FROM recipe_ingredients ri_ing
            WHERE ri_ing.recipe_id = r.id AND ri_ing.ingredient_id = ?
          )
        """,
            [ingredient_id],
        )
    if theme_id is not None:
        return (
            """
          AND EXISTS (
            SELECT 1 FROM recipe_themes rt
            WHERE rt.recipe_id = r.id AND rt.theme_id = ?
          )
        """,
            [theme_id],
        )
    return "", []


def fetch_makeable_recipes(
    theme_id=None, featured_ingredients=False, ingredient_id=None
):
    extra, params = _recipe_scope_sql(
        theme_id=theme_id,
        featured_ingredients=featured_ingredients,
        ingredient_id=ingredient_id,
    )
    covered = _ingredient_covered_sql("i")
    return get_db().execute(
        f"""
        SELECT r.*
        FROM recipes r
        WHERE r.published = 1
          {extra}
          AND NOT EXISTS (
            SELECT 1
            FROM recipe_ingredients ri
            JOIN ingredients i ON i.id = ri.ingredient_id
            WHERE ri.recipe_id = r.id
              AND ri.necessary = 1
              AND NOT {covered}
          )
        ORDER BY r.name COLLATE NOCASE ASC
        """,
        params,
    ).fetchall()


def fetch_published_recipes(
    theme_id=None, featured_ingredients=False, ingredient_id=None
):
    extra, params = _recipe_scope_sql(
        theme_id=theme_id,
        featured_ingredients=featured_ingredients,
        ingredient_id=ingredient_id,
    )
    return get_db().execute(
        f"""
        SELECT r.*
        FROM recipes r
        WHERE r.published = 1
          {extra}
        ORDER BY r.name COLLATE NOCASE ASC
        """,
        params,
    ).fetchall()


def fetch_ingredient(ingredient_id):
    return (
        get_db()
        .execute("SELECT * FROM ingredients WHERE id = ?", (ingredient_id,))
        .fetchone()
    )


def fetch_start_ingredients():
    """Ingredients used in published recipes, with makeable drink counts."""
    covered = _ingredient_covered_sql("i_cov")
    rows = get_db().execute(
        f"""
        SELECT
            i.id,
            i.name,
            i.category,
            i.in_filter,
            i.featured,
            (
                SELECT COUNT(*)
                FROM recipes r
                WHERE r.published = 1
                  AND EXISTS (
                    SELECT 1 FROM recipe_ingredients ri0
                    WHERE ri0.recipe_id = r.id AND ri0.ingredient_id = i.id
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM recipe_ingredients ri
                    JOIN ingredients i_cov ON i_cov.id = ri.ingredient_id
                    WHERE ri.recipe_id = r.id
                      AND ri.necessary = 1
                      AND NOT {covered}
                  )
            ) AS makeable_count,
            (
                SELECT COUNT(*)
                FROM recipes r
                WHERE r.published = 1
                  AND EXISTS (
                    SELECT 1 FROM recipe_ingredients ri0
                    WHERE ri0.recipe_id = r.id AND ri0.ingredient_id = i.id
                  )
            ) AS recipe_count
        FROM ingredients i
        WHERE EXISTS (
            SELECT 1
            FROM recipe_ingredients ri
            JOIN recipes r ON r.id = ri.recipe_id
            WHERE ri.ingredient_id = i.id AND r.published = 1
        )
        ORDER BY i.in_filter DESC, i.name COLLATE NOCASE ASC
        """
    ).fetchall()
    ingredients = []
    for row in rows:
        item = dict(row)
        item["icon"] = icon_for_ingredient(item["name"], item.get("category") or "Other")
        item["cat_slug"] = category_slug(item.get("category") or "Other")
        ingredients.append(item)
    return ingredients


def fetch_recent_recipes():
    """Published drinks, newest pour first (one row per recipe)."""
    return get_db().execute(
        """
        SELECT r.*
        FROM recipes r
        JOIN (
            SELECT recipe_id, MAX(id) AS last_id
            FROM made_drinks
            GROUP BY recipe_id
        ) last ON last.recipe_id = r.id
        JOIN made_drinks m ON m.id = last.last_id
        WHERE r.published = 1
        ORDER BY datetime(m.created_at) DESC, m.id DESC
        """
    ).fetchall()


def fetch_popular_recipes():
    """Published drinks ranked by average guest rating, then number of ratings."""
    return get_db().execute(
        """
        SELECT r.*
        FROM recipes r
        WHERE r.published = 1
          AND EXISTS (SELECT 1 FROM ratings t WHERE t.recipe_id = r.id)
        ORDER BY
          (SELECT AVG(stars) FROM ratings t WHERE t.recipe_id = r.id) DESC,
          (SELECT COUNT(*) FROM ratings t WHERE t.recipe_id = r.id) DESC,
          r.name COLLATE NOCASE ASC
        """
    ).fetchall()


def missing_necessary_by_recipe(recipe_ids):
    """Necessary bottles not on hand and not covered by a substitute."""
    if not recipe_ids:
        return {}
    covered = _ingredient_covered_sql("i")
    placeholders = ",".join("?" * len(recipe_ids))
    rows = get_db().execute(
        f"""
        SELECT ri.recipe_id, i.name
        FROM recipe_ingredients ri
        JOIN ingredients i ON i.id = ri.ingredient_id
        WHERE ri.recipe_id IN ({placeholders})
          AND ri.necessary = 1
          AND NOT {covered}
        ORDER BY i.name COLLATE NOCASE
        """,
        list(recipe_ids),
    ).fetchall()
    out = {}
    for row in rows:
        out.setdefault(row["recipe_id"], []).append(row["name"])
    return out


def fetch_all_recipes():
    return get_db().execute(
        """
        SELECT * FROM recipes
        ORDER BY name COLLATE NOCASE ASC
        """
    ).fetchall()


def fetch_recipe(recipe_id):
    return (
        get_db()
        .execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,))
        .fetchone()
    )


def fetch_recipe_lines(recipe_id):
    return get_db().execute(
        """
        SELECT
            ri.id AS line_id,
            ri.measure,
            ri.necessary,
            ri.sort_order AS line_sort,
            i.id AS ingredient_id,
            i.name AS ingredient_name,
            i.category AS ingredient_category,
            i.in_filter,
            (
                SELECT alt.name
                FROM ingredient_substitutes s
                JOIN ingredients alt ON alt.id = s.substitute_id
                WHERE s.ingredient_id = i.id
                  AND alt.in_filter = 1
                ORDER BY alt.name COLLATE NOCASE
                LIMIT 1
            ) AS substitute_name
        FROM recipe_ingredients ri
        JOIN ingredients i ON i.id = ri.ingredient_id
        WHERE ri.recipe_id = ?
        ORDER BY ri.sort_order ASC, i.name ASC
        """,
        (recipe_id,),
    ).fetchall()


def recipe_ingredient_summary(recipe_id):
    """Short names of necessary ingredients for TV/list display."""
    rows = get_db().execute(
        """
        SELECT i.name
        FROM recipe_ingredients ri
        JOIN ingredients i ON i.id = ri.ingredient_id
        WHERE ri.recipe_id = ? AND ri.necessary = 1
        ORDER BY ri.sort_order ASC, i.name ASC
        """,
        (recipe_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def save_recipe_lines(db, recipe_id, form):
    """Parse multi-row ingredient fields from a form and replace lines."""
    db.execute(
        "DELETE FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,)
    )
    ingredient_ids = form.getlist("line_ingredient_id")
    measures = form.getlist("line_measure")
    necessaries = form.getlist("line_necessary")
    sorts = form.getlist("line_sort_order")

    seen = set()
    for idx, raw_id in enumerate(ingredient_ids):
        raw_id = (raw_id or "").strip()
        if not raw_id:
            continue
        try:
            ingredient_id = int(raw_id)
        except ValueError:
            continue
        if ingredient_id in seen:
            continue
        seen.add(ingredient_id)
        measure = (measures[idx] if idx < len(measures) else "").strip()
        # Checkbox arrays only include checked rows; use parallel hidden flags.
        necessary = 1 if (idx < len(necessaries) and necessaries[idx] == "1") else 0
        try:
            sort_order = int(sorts[idx]) if idx < len(sorts) and sorts[idx] else (idx + 1) * 10
        except ValueError:
            sort_order = (idx + 1) * 10
        db.execute(
            """
            INSERT INTO recipe_ingredients
                (recipe_id, ingredient_id, measure, necessary, sort_order)
            VALUES (?, ?, ?, ?, ?)
            """,
            (recipe_id, ingredient_id, measure, necessary, sort_order),
        )


# —— Public: TV ——

# Last guest-viewed drink menu the TV should show: "main", "featured",
# "screens", "stock", or "theme:<slug>".
TV_BOARDS = ("main", "featured", "screens", "stock")


def redirect_canonical(endpoint, **values):
    """Send old bookmarks/QR codes to the current path, keeping the query string."""
    target = url_for(endpoint, **values)
    qs = request.query_string.decode()
    if qs:
        target = f"{target}?{qs}"
    code = 308 if request.method not in ("GET", "HEAD") else 301
    return redirect(target, code=code)


def tv_board_for_theme(theme):
    if theme is None:
        return "main"
    if _theme_is_featured(theme):
        return "featured"
    slug = (theme["slug"] or "").strip()
    return f"theme:{slug}" if slug else "main"


def tv_board_from_render(theme=None, stock=False, theme_index=False):
    if stock:
        return "stock"
    if theme_index:
        return "screens"
    return tv_board_for_theme(theme)


def remember_tv_board(board):
    """Phone drink-menu views write the board the living-room TV should follow."""
    board = resolve_tv_board(board)
    if (get_setting(SETTING_TV_BOARD) or "").strip() == board:
        return
    set_setting(SETTING_TV_BOARD, board)
    get_db().commit()


def resolve_tv_board(board):
    board = (board or "").strip() or "main"
    if board == "featured" or board == f"theme:{FEATURED_THEME_SLUG}":
        if featured_ingredient_names():
            return "featured"
        return "screens"
    if board.startswith("theme:"):
        slug = board.split(":", 1)[1].strip()
        if not slug:
            return "main"
        theme = fetch_theme_by_slug(slug)
        if not theme:
            return "main"
        if _theme_is_featured(theme):
            if featured_ingredient_names():
                return "featured"
            return "screens"
        return f"theme:{theme['slug']}"
    if board in TV_BOARDS:
        return board
    return "main"


def tv_board_path(board):
    board = resolve_tv_board(board)
    if board == "featured":
        return url_for("display_featured")
    if board == "screens":
        return url_for("display_theme_index")
    if board == "stock":
        return url_for("display_stock")
    if board.startswith("theme:"):
        return url_for("display_theme", slug=board.split(":", 1)[1])
    return url_for("display")


def tv_menu_context(theme=None):
    """Makeable recipes + ratings for the TV board (page or fragment)."""
    featured = _theme_is_featured(theme)
    theme_id = None if featured else (theme["id"] if theme is not None else None)
    recipes = fetch_makeable_recipes(
        theme_id=theme_id, featured_ingredients=featured
    )
    ratings = recipe_rating_summaries([r["id"] for r in recipes])
    items = [
        {
            "recipe": r,
            "ingredients": recipe_ingredient_summary(r["id"]),
            "rating": ratings.get(r["id"]),
        }
        for r in recipes
    ]
    return {
        "items": items,
        "recipe_count": len(recipes),
        "theme": theme,
        "featured_names": featured_ingredient_names(),
    }


def tv_stock_context():
    """On-hand vs out ingredients for the TV stock board."""
    ings = fetch_ingredients()
    on_hand = sorted(
        [i for i in ings if i["in_filter"]],
        key=lambda i: (i["name"] or "").casefold(),
    )
    out = sorted(
        [i for i in ings if not i["in_filter"]],
        key=lambda i: (i["name"] or "").casefold(),
    )
    return {
        "on_hand": on_hand,
        "out": out,
        "recipe_count": len(ings),
        "theme": None,
        "items": [],
    }


@app.route("/favicon.ico")
def favicon():
    return redirect(url_for("static", filename="favicon.ico"))


def _render_tv(theme=None, stock=False, theme_index=False):
    wifi_ssid = get_setting(SETTING_WIFI_SSID).strip()
    if theme:
        ticker_quotes = ticker_quotes_for_theme(theme["slug"])
    else:
        ticker_quotes = [
            {"quote": row["quote"], "movie": row["movie"]}
            for row in get_db().execute(
                "SELECT quote, movie FROM movie_quotes"
            ).fetchall()
        ]
    if stock:
        extra = tv_stock_context()
        extra["theme_catalog"] = []
        extra["featured_tile"] = None
        extra["featured_names"] = featured_ingredient_names()
    elif theme_index:
        counts = makeable_counts_by_theme()
        extra = {
            "items": [],
            "recipe_count": 0,
            "theme": None,
            "theme_catalog": [],
            "themes": [
                t
                for t in theme_picker_list(counts)
                if t["slug"] != FEATURED_THEME_SLUG
            ],
            "featured_tile": featured_theme_card(),
            "featured_names": featured_ingredient_names(),
        }
    else:
        extra = tv_menu_context(theme=theme)
        extra["theme_catalog"] = []
        extra["featured_tile"] = None
    phone_url = resolve_bar_url(
        ingredients=stock,
        theme_slug=theme["slug"] if theme else None,
    )
    # Theme boards (/tv/themes) and Main stay put so you can pick; opening a
    # themed TV menu still tells the living-room set what to follow.
    if theme:
        remember_tv_board(tv_board_for_theme(theme))
    return render_template(
        "display.html",
        wifi_ssid=wifi_ssid,
        wifi_ready=bool(wifi_ssid),
        bar_url=phone_url,
        ticker_quotes=ticker_quotes,
        bar_name=bar_name(),
        stock=stock,
        theme_index=theme_index,
        tv_board=tv_board_from_render(
            theme=theme, stock=stock, theme_index=theme_index
        ),
        palette_style=palette_style(theme["slug"]) if theme else "",
        music_artists=artists_for_theme(theme["slug"]) if theme else [],
        **extra,
    )


@app.route("/tv")
def display():
    return _render_tv()


@app.route("/")
def display_legacy_root():
    return redirect_canonical("display")


@app.route("/tv/featured")
def display_featured():
    theme = featured_theme_dict()
    if not theme["featured_names"]:
        return redirect(url_for("display_theme_index"))
    return _render_tv(theme=theme)


@app.route("/featured")
def display_featured_legacy():
    return redirect_canonical("display_featured")


@app.route("/tv/themes")
def display_theme_index():
    """TV board of theme families — pick a themed menu to show."""
    return _render_tv(theme_index=True)


@app.route("/screens")
def display_theme_index_legacy():
    return redirect_canonical("display_theme_index")


@app.route("/tv/theme/<slug>")
def display_theme(slug):
    theme = fetch_theme_by_slug(slug)
    if not theme:
        flash("Theme not found.", "error")
        return redirect(url_for("display_theme_index"))
    return _render_tv(theme=theme)


@app.route("/theme/<slug>")
def display_theme_legacy(slug):
    return redirect_canonical("display_theme", slug=slug)


@app.route("/tv/stock")
def display_stock():
    return _render_tv(stock=True)


@app.route("/stock")
def display_stock_legacy():
    return redirect_canonical("display_stock")


@app.route("/tv/menu")
@app.route("/tv/live/menu")
def tv_menu():
    """HTML fragment for the drink grid only — ticker and QR stay on the page."""
    slug = (request.args.get("theme") or "").strip()
    theme = fetch_theme_by_slug(slug) if slug else None
    html = render_template("tv_menu.html", **tv_menu_context(theme=theme))
    resp = make_response(html)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/tv/live/stock")
def tv_stock():
    """HTML fragment for the on-hand / out ingredient board."""
    html = render_template("tv_stock.html", **tv_stock_context())
    resp = make_response(html)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/tv/made")
@app.route("/tv/live/made")
def tv_made():
    """Latest completed drink for the TV toast."""
    row = get_db().execute(
        """
        SELECT m.id,
               r.name,
               r.glassware,
               r.category,
               strftime('%s', m.created_at) AS made_at
        FROM made_drinks m
        JOIN recipes r ON r.id = m.recipe_id
        ORDER BY m.id DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        resp = jsonify(id=None)
        resp.headers["Cache-Control"] = "no-store"
        return resp
    payload = {
        "id": row["id"],
        "name": row["name"],
        "glassware": row["glassware"] or "",
        "icon": glassware_icon_url(row["glassware"], row["category"]),
        "made_at": int(row["made_at"] or 0),
    }
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _latest_tv_show_row():
    return get_db().execute(
        """
        SELECT s.id,
               s.recipe_id,
               s.created_at,
               s.dismissed,
               strftime('%s', s.created_at) AS shown_at,
               r.name,
               r.instructions,
               r.glassware,
               r.category
        FROM tv_shows s
        JOIN recipes r ON r.id = s.recipe_id
        ORDER BY s.id DESC
        LIMIT 1
        """
    ).fetchone()


def _tv_show_is_active(row):
    if not row:
        return False
    if int(row["dismissed"] or 0):
        return False
    made = get_db().execute(
        """
        SELECT 1 FROM made_drinks
        WHERE recipe_id = ?
          AND datetime(created_at) >= datetime(?)
        LIMIT 1
        """,
        (row["recipe_id"], row["created_at"]),
    ).fetchone()
    return made is None


def _tv_show_payload(row):
    lines = fetch_recipe_lines(row["recipe_id"])
    ingredients = [
        {
            "name": line["ingredient_name"],
            "measure": line["measure"] or "",
            "necessary": bool(line["necessary"]),
            "in_filter": bool(line["in_filter"]),
            "substitute_name": line["substitute_name"] or "",
        }
        for line in lines
    ]
    shown_at = int(row["shown_at"] or 0)
    return {
        "id": row["id"],
        "recipe_id": row["recipe_id"],
        "name": row["name"],
        "instructions": row["instructions"] or "",
        "glassware": row["glassware"] or "",
        "icon": glassware_icon_url(row["glassware"], row["category"]),
        "shown_at": shown_at,
        "ingredients": ingredients,
    }


@app.route("/tv/follow")
@app.route("/tv/live/follow")
def tv_follow():
    """Board the TV should show — last drink menu opened on a phone."""
    raw = (get_setting(SETTING_TV_BOARD) or "").strip()
    if not raw:
        resp = jsonify(board=None, path=None)
        resp.headers["Cache-Control"] = "no-store"
        return resp
    board = resolve_tv_board(raw)
    resp = jsonify(board=board, path=tv_board_path(board))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/tv/show")
@app.route("/tv/live/show")
def tv_show():
    """Current recipe pinned to the TV until made, dismissed, or replaced."""
    row = _latest_tv_show_row()
    if not _tv_show_is_active(row):
        resp = jsonify(id=None)
        resp.headers["Cache-Control"] = "no-store"
        return resp
    resp = jsonify(_tv_show_payload(row))
    resp.headers["Cache-Control"] = "no-store"
    return resp


# —— Public: interactive bar ——

GUEST_LISTS = {
    "recent": {
        "title": "Most recent",
        "blurb": "The last five drinks poured",
        "nav": "bar-recent",
        "endpoint": "bar_recent",
        "limit": 5,
        "empty": "No drinks have been poured yet. Finish a recipe checklist to log one.",
    },
    "top": {
        "title": "Top ten",
        "blurb": "The ten highest-rated drinks",
        "nav": "bar-top",
        "endpoint": "bar_top",
        "limit": 10,
        "empty": "Rate drinks to build this list.",
    },
}


def parse_list_kind():
    kind = (request.args.get("list") or "").strip()
    return kind if kind in GUEST_LISTS else None


def _render_bar(theme=None, include_unmakeable=False, list_kind=None, ingredient=None):
    """Handheld recipe list: makeable drinks, or the whole published set."""
    featured = _theme_is_featured(theme)
    theme_id = None if featured else (theme["id"] if theme is not None else None)
    ingredient_id = ingredient["id"] if ingredient is not None else None
    list_meta = GUEST_LISTS.get(list_kind) if list_kind else None
    makeable = fetch_makeable_recipes(
        theme_id=theme_id,
        featured_ingredients=featured,
        ingredient_id=ingredient_id,
    )
    makeable_ids = {r["id"] for r in makeable}
    if list_meta:
        ranked = (
            fetch_recent_recipes()
            if list_kind == "recent"
            else fetch_popular_recipes()
        )
        published = ranked[: list_meta["limit"]]
        recipes = (
            published
            if include_unmakeable
            else [r for r in published if r["id"] in makeable_ids]
        )
        makeable_count = sum(1 for r in published if r["id"] in makeable_ids)
    else:
        published = fetch_published_recipes(
            theme_id=theme_id,
            featured_ingredients=featured,
            ingredient_id=ingredient_id,
        )
        recipes = published if include_unmakeable else makeable
        makeable_count = len(makeable)
    missing = missing_necessary_by_recipe(
        [r["id"] for r in recipes if r["id"] not in makeable_ids]
    )
    cards = []
    for r in recipes:
        can_make = r["id"] in makeable_ids
        cards.append(
            {
                "recipe": r,
                "tags": parse_tags(r["tags"]),
                "ingredients": recipe_ingredient_summary(r["id"]),
                "makeable": can_make,
                "missing": missing.get(r["id"], []),
            }
        )
    on_hand = get_db().execute(
        "SELECT COUNT(*) AS n FROM ingredients WHERE in_filter = 1"
    ).fetchone()["n"]
    total_ings = get_db().execute(
        "SELECT COUNT(*) AS n FROM ingredients"
    ).fetchone()["n"]
    ratings = recipe_rating_summaries([r["id"] for r in recipes])
    for card in cards:
        card["rating"] = ratings.get(card["recipe"]["id"])
    if not list_meta and ingredient is None:
        remember_tv_board(tv_board_for_theme(theme))
    if list_meta:
        list_title = list_meta["title"]
        list_blurb = list_meta["blurb"]
        list_endpoint = list_meta["endpoint"]
        nav_current = list_meta["nav"]
        list_empty = list_meta["empty"]
        breadcrumbs = [
            {"label": "Themes", "url": url_for("bar")},
            {"label": list_title, "url": url_for(list_endpoint)},
        ]
    elif ingredient is not None:
        list_title = ingredient["name"]
        list_blurb = "Drinks that use this bottle"
        list_endpoint = None
        nav_current = "start"
        list_empty = "No published drinks use this ingredient yet."
        breadcrumbs = [
            {"label": "Themes", "url": url_for("bar")},
            {"label": "Start with…", "url": url_for("bar_start")},
            {
                "label": ingredient["name"],
                "url": url_for("bar_ingredient", ingredient_id=ingredient["id"]),
            },
        ]
    else:
        list_title = theme["name"] if theme else "Full menu"
        list_blurb = (theme["description"] if theme else "") or ""
        list_endpoint = None
        nav_current = "bar-theme" if theme else "bar-menu"
        list_empty = ""
        breadcrumbs = [
            {"label": "Themes", "url": url_for("bar")},
            {
                "label": theme["name"] if theme else "Full Menu",
                "url": (
                    url_for("bar_theme", slug=theme["slug"])
                    if theme
                    else url_for("bar_menu")
                ),
            },
        ]
    device_id, set_cookie = ensure_device_id()
    resp = make_response(
        render_template(
            "bar.html",
            cards=cards,
            recipe_count=len(recipes),
            makeable_count=makeable_count,
            published_count=len(published),
            on_hand_count=on_hand,
            ingredient_count=total_ings,
            theme=theme,
            ingredient=ingredient,
            include_unmakeable=include_unmakeable,
            palette_style=palette_style(theme["slug"]) if theme else "",
            featured_names=featured_ingredient_names(),
            list_kind=list_kind or "",
            list_title=list_title,
            list_blurb=list_blurb,
            list_endpoint=list_endpoint,
            list_empty=list_empty,
            nav_current=nav_current,
            breadcrumbs=breadcrumbs,
        )
    )
    return attach_device_cookie(resp, device_id, set_cookie)


@app.route("/guest")
def bar():
    """Master theme page — clickable themed menus, A–Z."""
    counts = makeable_counts_by_theme()
    on_hand = get_db().execute(
        "SELECT COUNT(*) AS n FROM ingredients WHERE in_filter = 1"
    ).fetchone()["n"]
    total_ings = get_db().execute(
        "SELECT COUNT(*) AS n FROM ingredients"
    ).fetchone()["n"]
    return render_template(
        "bar_themes.html",
        themes=theme_picker_list(counts),
        on_hand_count=on_hand,
        ingredient_count=total_ings,
    )


def _wants_all_recipes():
    return request.args.get("all", "").lower() in ("1", "true", "yes")


@app.route("/guest/menu")
def bar_menu():
    """Full handheld menu — makeable drinks, or every published drink."""
    return _render_bar(include_unmakeable=_wants_all_recipes())


@app.route("/guest/recent")
def bar_recent():
    """The five most recently poured drinks."""
    return _render_bar(include_unmakeable=_wants_all_recipes(), list_kind="recent")


@app.route("/guest/top")
def bar_top():
    """The ten highest-rated drinks."""
    return _render_bar(include_unmakeable=_wants_all_recipes(), list_kind="top")


@app.route("/guest/featured")
def bar_featured():
    theme = featured_theme_dict()
    if not theme["featured_names"]:
        flash("Star a bottle on What’s On Hand to build a featured menu.", "ok")
        return redirect(url_for("bar_ingredients"))
    return _render_bar(theme=theme, include_unmakeable=_wants_all_recipes())


@app.route("/guest/theme/<slug>")
def bar_theme(slug):
    theme = fetch_theme_by_slug(slug)
    if not theme:
        flash("Theme not found.", "error")
        return redirect(url_for("bar"))
    return _render_bar(theme=theme, include_unmakeable=_wants_all_recipes())


@app.route("/guest/on-hand")
def bar_ingredients():
    """Toggle which ingredients are on hand (shared filter for TV + recipes)."""
    rows = get_db().execute(
        """
        SELECT * FROM ingredients
        ORDER BY name COLLATE NOCASE ASC
        """
    ).fetchall()
    ingredients = []
    seen_cats = []
    for row in rows:
        item = dict(row)
        item["icon"] = icon_for_ingredient(item["name"], item.get("category") or "Other")
        item["cat_slug"] = category_slug(item.get("category") or "Other")
        ingredients.append(item)
        cat = item.get("category") or "Other"
        if cat not in seen_cats:
            seen_cats.append(cat)
    cat_order = ["Spirit", "Mixer", "Garnish", "Other"]
    legend = [c for c in cat_order if c in seen_cats]
    legend.extend(c for c in seen_cats if c not in legend)
    on_hand = sum(1 for i in ingredients if i["in_filter"])
    featured_names = [i["name"] for i in ingredients if i.get("featured")]
    return render_template(
        "bar_ingredients.html",
        ingredients=ingredients,
        category_legend=legend,
        on_hand_count=on_hand,
        ingredient_count=len(ingredients),
        featured_names=featured_names,
    )


@app.route("/guest/start")
def bar_start():
    """Pick one ingredient, then see drinks that use it."""
    ingredients = fetch_start_ingredients()
    on_hand = sum(1 for i in ingredients if i["in_filter"])
    makeable_with = sum(1 for i in ingredients if i["makeable_count"])
    return render_template(
        "bar_start.html",
        ingredients=ingredients,
        on_hand_count=on_hand,
        ingredient_count=len(ingredients),
        makeable_with_count=makeable_with,
    )


@app.route("/guest/ingredient/<int:ingredient_id>")
def bar_ingredient(ingredient_id):
    """Drinks that use a single ingredient (makeable first)."""
    row = fetch_ingredient(ingredient_id)
    if not row:
        flash("Ingredient not found.", "error")
        return redirect(url_for("bar_start"))
    ingredient = dict(row)
    ingredient["icon"] = icon_for_ingredient(
        ingredient["name"], ingredient.get("category") or "Other"
    )
    ingredient["cat_slug"] = category_slug(ingredient.get("category") or "Other")
    published = fetch_published_recipes(ingredient_id=ingredient_id)
    if not published:
        flash(f"No published drinks use {ingredient['name']}.", "error")
        return redirect(url_for("bar_start"))
    return _render_bar(
        ingredient=ingredient, include_unmakeable=_wants_all_recipes()
    )


def _parse_ingredient_arg():
    raw = (request.args.get("ingredient") or "").strip()
    if not raw:
        return None
    try:
        ingredient_id = int(raw)
    except ValueError:
        return None
    row = fetch_ingredient(ingredient_id)
    if not row:
        return None
    return dict(row)


@app.route("/guest/recipe/<int:recipe_id>")
def recipe_detail(recipe_id):
    recipe = fetch_recipe(recipe_id)
    theme_slug = (request.args.get("theme") or "").strip()
    theme = fetch_theme_by_slug(theme_slug) if theme_slug else None
    list_kind = parse_list_kind()
    list_meta = GUEST_LISTS.get(list_kind)
    ingredient = _parse_ingredient_arg()
    if not recipe:
        flash("Recipe not found.", "error")
        if list_meta:
            return redirect(url_for(list_meta["endpoint"]))
        if ingredient:
            return redirect(
                url_for("bar_ingredient", ingredient_id=ingredient["id"])
            )
        if theme:
            return redirect(url_for("bar_theme", slug=theme["slug"]))
        return redirect(url_for("bar_menu"))

    lines = fetch_recipe_lines(recipe_id)
    device_id, set_cookie = ensure_device_id()
    summary = recipe_rating_summaries([recipe_id]).get(recipe_id)
    list_title = list_meta["title"] if list_meta else ""
    list_endpoint = list_meta["endpoint"] if list_meta else None
    if list_meta:
        return_url = url_for(list_endpoint)
    elif ingredient:
        return_url = url_for("bar_ingredient", ingredient_id=ingredient["id"])
    elif theme:
        return_url = (
            url_for("bar_featured")
            if _theme_is_featured(theme)
            else url_for("bar_theme", slug=theme["slug"])
        )
    else:
        return_url = url_for("bar_menu")
    if list_meta:
        nav_current = list_meta["nav"]
        breadcrumbs = [
            {"label": "Themes", "url": url_for("bar")},
            {"label": list_title, "url": url_for(list_endpoint)},
            {
                "label": recipe["name"],
                "url": url_for(
                    "recipe_detail", recipe_id=recipe_id, list=list_kind
                ),
            },
        ]
    elif ingredient:
        nav_current = "start"
        breadcrumbs = [
            {"label": "Themes", "url": url_for("bar")},
            {"label": "Start with…", "url": url_for("bar_start")},
            {
                "label": ingredient["name"],
                "url": url_for("bar_ingredient", ingredient_id=ingredient["id"]),
            },
            {
                "label": recipe["name"],
                "url": url_for(
                    "recipe_detail",
                    recipe_id=recipe_id,
                    ingredient=ingredient["id"],
                ),
            },
        ]
    else:
        nav_current = "bar-theme" if theme else "bar-menu"
        breadcrumbs = [
            {"label": "Themes", "url": url_for("bar")},
            {
                "label": theme["name"] if theme else "Full Menu",
                "url": (
                    url_for("bar_theme", slug=theme["slug"])
                    if theme
                    else url_for("bar_menu")
                ),
            },
            {
                "label": recipe["name"],
                "url": (
                    url_for(
                        "recipe_detail",
                        recipe_id=recipe_id,
                        theme=theme["slug"],
                    )
                    if theme
                    else url_for("recipe_detail", recipe_id=recipe_id)
                ),
            },
        ]
    resp = make_response(
        render_template(
            "recipe.html",
            recipe=recipe,
            lines=lines,
            tags=parse_tags(recipe["tags"]),
            rating=summary,
            my_stars=device_vote(recipe_id, device_id),
            theme=theme,
            ingredient=ingredient,
            palette_style=palette_style(theme["slug"]) if theme else "",
            featured_names=featured_ingredient_names(),
            tv_showing=_recipe_is_on_tv(recipe_id),
            list_kind=list_kind or "",
            list_title=list_title,
            list_endpoint=list_endpoint,
            nav_current=nav_current,
            breadcrumbs=breadcrumbs,
            return_url=return_url,
        )
    )
    return attach_device_cookie(resp, device_id, set_cookie)


@app.route("/bar")
def bar_legacy():
    return redirect_canonical("bar")


@app.route("/bar/menu")
def bar_menu_legacy():
    return redirect_canonical("bar_menu")


@app.route("/bar/recent")
def bar_recent_legacy():
    return redirect_canonical("bar_recent")


@app.route("/bar/top")
def bar_top_legacy():
    return redirect_canonical("bar_top")


@app.route("/bar/featured")
def bar_featured_legacy():
    return redirect_canonical("bar_featured")


@app.route("/bar/theme/<slug>")
def bar_theme_legacy(slug):
    return redirect_canonical("bar_theme", slug=slug)


@app.route("/bar/ingredients")
def bar_ingredients_legacy():
    return redirect_canonical("bar_ingredients")


@app.route("/bar/start")
def bar_start_legacy():
    return redirect_canonical("bar_start")


@app.route("/bar/ingredient/<int:ingredient_id>")
def bar_ingredient_legacy(ingredient_id):
    return redirect_canonical("bar_ingredient", ingredient_id=ingredient_id)


@app.route("/bar/recipe/<int:recipe_id>")
def recipe_detail_legacy(recipe_id):
    return redirect_canonical("recipe_detail", recipe_id=recipe_id)


@app.route("/bar/recipe/<int:recipe_id>/rate", methods=["POST"])
@app.route("/guest/recipe/<int:recipe_id>/rate", methods=["POST"])
def rate_recipe(recipe_id):
    recipe = fetch_recipe(recipe_id)
    if not recipe or not recipe["published"]:
        if _wants_json():
            return jsonify(ok=False, error="not found"), 404
        flash("Recipe not found.", "error")
        return redirect(url_for("bar"))
    try:
        stars = int(request.form.get("stars") or 0)
    except ValueError:
        stars = 0
    if stars < 1 or stars > 5:
        if _wants_json():
            return jsonify(ok=False, error="Pick a rating from 1 to 5 stars."), 400
        flash("Pick a rating from 1 to 5 stars.", "error")
        return _redirect_recipe_detail(recipe_id)

    device_id, set_cookie = ensure_device_id()
    db = get_db()
    db.execute(
        """
        INSERT INTO ratings (recipe_id, device_id, stars)
        VALUES (?, ?, ?)
        ON CONFLICT(recipe_id, device_id) DO UPDATE SET
            stars = excluded.stars,
            created_at = datetime('now')
        """,
        (recipe_id, device_id, stars),
    )
    db.commit()
    if _wants_json():
        summary = recipe_rating_summaries([recipe_id]).get(recipe_id) or {}
        resp = make_response(
            jsonify(
                ok=True,
                stars=stars,
                avg=summary.get("avg"),
                count=summary.get("count"),
            )
        )
        return attach_device_cookie(resp, device_id, set_cookie)
    flash(f"You rated this {stars}/5.", "ok")
    resp = make_response(_redirect_recipe_detail(recipe_id))
    return attach_device_cookie(resp, device_id, set_cookie)


@app.route("/bar/recipe/<int:recipe_id>/made", methods=["POST"])
@app.route("/guest/recipe/<int:recipe_id>/made", methods=["POST"])
def mark_recipe_made(recipe_id):
    """Guest finished the on-recipe ingredient checklist."""
    recipe = fetch_recipe(recipe_id)
    if not recipe or not recipe["published"]:
        return jsonify(ok=False, error="not found"), 404
    db = get_db()
    cur = db.execute(
        "INSERT INTO made_drinks (recipe_id) VALUES (?)", (recipe_id,)
    )
    db.commit()
    return jsonify(ok=True, id=cur.lastrowid, name=recipe["name"])


def _recipe_is_on_tv(recipe_id):
    latest = _latest_tv_show_row()
    return bool(
        latest
        and latest["recipe_id"] == recipe_id
        and _tv_show_is_active(latest)
    )


def _redirect_recipe_detail(recipe_id):
    list_kind = parse_list_kind()
    if list_kind:
        return redirect(
            url_for("recipe_detail", recipe_id=recipe_id, list=list_kind)
        )
    ingredient = _parse_ingredient_arg()
    if ingredient:
        return redirect(
            url_for(
                "recipe_detail",
                recipe_id=recipe_id,
                ingredient=ingredient["id"],
            )
        )
    theme_slug = (request.args.get("theme") or "").strip()
    if theme_slug:
        return redirect(
            url_for("recipe_detail", recipe_id=recipe_id, theme=theme_slug)
        )
    return redirect(url_for("recipe_detail", recipe_id=recipe_id))


@app.route("/bar/recipe/<int:recipe_id>/show", methods=["POST"])
@app.route("/guest/recipe/<int:recipe_id>/show", methods=["POST"])
def show_recipe_on_tv(recipe_id):
    """Pin or dismiss this recipe on the TV."""
    recipe = fetch_recipe(recipe_id)
    if not recipe or not recipe["published"]:
        if _wants_json():
            return jsonify(ok=False, error="not found"), 404
        flash("Recipe not found.", "error")
        return redirect(url_for("bar"))
    db = get_db()
    latest = _latest_tv_show_row()
    active_here = bool(
        latest
        and latest["recipe_id"] == recipe_id
        and _tv_show_is_active(latest)
    )
    intent = (request.form.get("intent") or "").strip().lower()
    hide = intent == "hide" or (not intent and active_here)
    show_id = latest["id"] if latest else None
    showing = False
    if hide:
        if active_here:
            db.execute(
                "UPDATE tv_shows SET dismissed = 1 WHERE id = ?",
                (latest["id"],),
            )
            show_id = latest["id"]
        showing = False
    else:
        cur = db.execute(
            "INSERT INTO tv_shows (recipe_id) VALUES (?)", (recipe_id,)
        )
        show_id = cur.lastrowid
        showing = True
    db.commit()
    if _wants_json():
        return jsonify(
            ok=True, id=show_id, name=recipe["name"], showing=showing
        )
    if showing:
        flash(f'"{recipe["name"]}" is on the TV.', "ok")
    else:
        flash(f'"{recipe["name"]}" is off the TV.', "ok")
    return _redirect_recipe_detail(recipe_id)


@app.route("/bar/filter", methods=["POST"])
@app.route("/guest/filter", methods=["POST"])
def update_filter():
    """Set in_filter from checkbox list: checked IDs are on hand."""
    checked = set()
    for raw in request.form.getlist("ingredient_id"):
        try:
            checked.add(int(raw))
        except ValueError:
            continue
    db = get_db()
    all_ids = [
        row["id"]
        for row in db.execute("SELECT id FROM ingredients").fetchall()
    ]
    for iid in all_ids:
        db.execute(
            "UPDATE ingredients SET in_filter = ? WHERE id = ?",
            (1 if iid in checked else 0, iid),
        )
    db.commit()
    return redirect(url_for("bar_ingredients"))


def _wants_json():
    if request.headers.get("X-Requested-With") == "fetch":
        return True
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    return best == "application/json" and (
        request.accept_mimetypes[best]
        > request.accept_mimetypes["text/html"]
    )


@app.route("/bar/filter/toggle/<int:ingredient_id>", methods=["POST"])
@app.route("/guest/filter/toggle/<int:ingredient_id>", methods=["POST"])
def toggle_filter_ingredient(ingredient_id):
    """Toggle a single ingredient (AJAX keeps scroll; form POST still works)."""
    db = get_db()
    row = db.execute(
        "SELECT in_filter FROM ingredients WHERE id = ?", (ingredient_id,)
    ).fetchone()
    if not row:
        if _wants_json():
            return jsonify(ok=False, error="not found"), 404
        return redirect(url_for("bar_ingredients"))

    new_val = 0 if row["in_filter"] else 1
    db.execute(
        "UPDATE ingredients SET in_filter = ? WHERE id = ?",
        (new_val, ingredient_id),
    )
    db.commit()
    on_hand = db.execute(
        "SELECT COUNT(*) AS n FROM ingredients WHERE in_filter = 1"
    ).fetchone()["n"]
    total = db.execute("SELECT COUNT(*) AS n FROM ingredients").fetchone()["n"]

    if _wants_json():
        return jsonify(
            ok=True,
            id=ingredient_id,
            in_filter=bool(new_val),
            on_hand_count=on_hand,
            ingredient_count=total,
        )
    return redirect(url_for("bar_ingredients"))


@app.route("/bar/filter/feature/<int:ingredient_id>", methods=["POST"])
@app.route("/guest/filter/feature/<int:ingredient_id>", methods=["POST"])
def toggle_featured_ingredient(ingredient_id):
    """Star/unstar a bottle for the Featured theme (TV + guest)."""
    db = get_db()
    row = db.execute(
        "SELECT featured FROM ingredients WHERE id = ?", (ingredient_id,)
    ).fetchone()
    if not row:
        if _wants_json():
            return jsonify(ok=False, error="not found"), 404
        return redirect(url_for("bar_ingredients"))

    new_val = 0 if row["featured"] else 1
    db.execute(
        "UPDATE ingredients SET featured = ? WHERE id = ?",
        (new_val, ingredient_id),
    )
    db.commit()
    names = featured_ingredient_names()
    if _wants_json():
        return jsonify(
            ok=True,
            id=ingredient_id,
            featured=bool(new_val),
            featured_names=names,
            featured_count=len(names),
        )
    return redirect(url_for("bar_ingredients"))


@app.route("/bar/filter/reset", methods=["POST"])
@app.route("/guest/filter/reset", methods=["POST"])
def reset_filter():
    db = get_db()
    db.execute("UPDATE ingredients SET in_filter = 1")
    db.commit()
    flash("Filter reset — everything is on hand.", "ok")
    return redirect(url_for("bar_ingredients"))


@app.route("/bar/filter/clear", methods=["POST"])
@app.route("/guest/filter/clear", methods=["POST"])
def clear_filter():
    db = get_db()
    db.execute("UPDATE ingredients SET in_filter = 0")
    db.commit()
    flash("Filter cleared — check what you have.", "ok")
    return redirect(url_for("bar_ingredients"))


# —— Guest QR images (shown on the TV boards) ——


@app.route("/join/qr/wifi.png")
def join_qr_wifi():
    ssid = get_setting(SETTING_WIFI_SSID).strip()
    if not ssid:
        flash("Set your Wi‑Fi name in Admin → Settings first.", "error")
        return redirect(url_for("admin_settings"))
    password = get_setting(SETTING_WIFI_PASSWORD)
    security = get_setting(SETTING_WIFI_SECURITY, "WPA") or "WPA"
    hidden = get_setting(SETTING_WIFI_HIDDEN, "0") == "1"
    payload = build_wifi_qr_payload(ssid, password, security, hidden)
    return send_file(
        make_qr_png(payload, box_size=12, border=2),
        mimetype="image/png",
        download_name="wifi-qr.png",
        max_age=0,
    )


@app.route("/join/qr/bar.png")
def join_qr_bar():
    ingredients = request.args.get("ingredients", "").lower() in ("1", "true", "yes")
    theme_slug = (request.args.get("theme") or "").strip() or None
    return send_file(
        make_qr_png(
            resolve_bar_url(ingredients=ingredients, theme_slug=theme_slug),
            box_size=12,
            border=2,
        ),
        mimetype="image/png",
        download_name=(
            "bar-ingredients-qr.png"
            if ingredients
            else f"bar-theme-{theme_slug}-qr.png" if theme_slug else "bar-qr.png"
        ),
        max_age=0,
    )


# —— Admin (open, no password) ——


@app.route("/admin")
def admin():
    recipes = fetch_all_recipes()
    return render_template(
        "admin.html",
        recipes=recipes,
        recipe_count=len(recipes),
        wifi_configured=bool(get_setting(SETTING_WIFI_SSID).strip()),
    )


@app.route("/admin/inventory")
def admin_inventory():
    """Shopping-oriented inventory: out of stock first, then on hand."""
    ingredients = fetch_ingredients()
    out = [i for i in ingredients if not i["in_filter"]]
    on_hand = [i for i in ingredients if i["in_filter"]]

    # For each out-of-stock ingredient: recipes that use it (necessary first)
    recipes_by_ing = {}
    if out:
        out_ids = [i["id"] for i in out]
        placeholders = ",".join("?" * len(out_ids))
        rows = get_db().execute(
            f"""
            SELECT
                ri.ingredient_id,
                ri.necessary,
                r.id AS recipe_id,
                r.name AS recipe_name
            FROM recipe_ingredients ri
            JOIN recipes r ON r.id = ri.recipe_id
            WHERE ri.ingredient_id IN ({placeholders})
            ORDER BY ri.necessary DESC, r.name COLLATE NOCASE ASC
            """,
            out_ids,
        ).fetchall()
        for row in rows:
            iid = row["ingredient_id"]
            recipes_by_ing.setdefault(iid, []).append(
                {
                    "id": row["recipe_id"],
                    "name": row["recipe_name"],
                    "necessary": bool(row["necessary"]),
                }
            )

    return render_template(
        "admin_inventory.html",
        out_groups=group_by_category(out),
        on_hand_groups=group_by_category(on_hand),
        recipes_by_ing=recipes_by_ing,
        out_count=len(out),
        on_hand_count=len(on_hand),
        total_count=len(ingredients),
    )


@app.route("/admin/substitutes", methods=["GET", "POST"])
def admin_substitutes():
    """Pair bottles that can stand in for each other while stocking separately."""
    db = get_db()
    if request.method == "POST":
        action = (request.form.get("action") or "add").strip()
        if action == "delete":
            try:
                a_id = int(request.form.get("a_id") or 0)
                b_id = int(request.form.get("b_id") or 0)
            except ValueError:
                a_id = b_id = 0
            if a_id and b_id:
                db.execute(
                    """
                    DELETE FROM ingredient_substitutes
                    WHERE (ingredient_id = ? AND substitute_id = ?)
                       OR (ingredient_id = ? AND substitute_id = ?)
                    """,
                    (a_id, b_id, b_id, a_id),
                )
                db.commit()
                flash("Removed that substitute pair.", "ok")
            return redirect(url_for("admin_substitutes"))

        try:
            a_id = int(request.form.get("ingredient_id") or 0)
            b_id = int(request.form.get("substitute_id") or 0)
        except ValueError:
            a_id = b_id = 0
        if not a_id or not b_id:
            flash("Pick two ingredients.", "error")
        elif a_id == b_id:
            flash("A bottle can’t substitute for itself.", "error")
        else:
            _add_substitute_pair(db, a_id, b_id)
            db.commit()
            flash("Substitute pair saved.", "ok")
        return redirect(url_for("admin_substitutes"))

    return render_template(
        "admin_substitutes.html",
        pairs=fetch_substitute_pairs(),
        ingredients=fetch_ingredients(),
    )


@app.route("/admin/settings", methods=["GET", "POST"])
def admin_settings():
    if request.method == "POST":
        ssid = (request.form.get("wifi_ssid") or "").strip()
        password = request.form.get("wifi_password") or ""
        security = (request.form.get("wifi_security") or "WPA").strip()
        if security not in ("WPA", "WEP", "nopass"):
            security = "WPA"
        hidden = "1" if request.form.get("wifi_hidden") else "0"
        base_url = (request.form.get("public_base_url") or "").strip().rstrip("/")
        set_setting(SETTING_WIFI_SSID, ssid)
        set_setting(SETTING_WIFI_PASSWORD, password)
        set_setting(SETTING_WIFI_SECURITY, security)
        set_setting(SETTING_WIFI_HIDDEN, hidden)
        set_setting(SETTING_PUBLIC_BASE_URL, base_url)
        name = (request.form.get("bar_name") or "").strip() or DEFAULT_BAR_NAME
        set_setting(SETTING_BAR_NAME, name)
        get_db().commit()
        flash("Settings saved.", "ok")
        return redirect(url_for("admin_settings"))

    return render_template(
        "admin_settings.html",
        bar_name=bar_name(),
        wifi_ssid=get_setting(SETTING_WIFI_SSID),
        wifi_password=get_setting(SETTING_WIFI_PASSWORD),
        wifi_security=get_setting(SETTING_WIFI_SECURITY, "WPA") or "WPA",
        wifi_hidden=get_setting(SETTING_WIFI_HIDDEN, "0") == "1",
        public_base_url=get_setting(SETTING_PUBLIC_BASE_URL),
        suggested_base_url=request.url_root.rstrip("/"),
        bar_url=resolve_bar_url(),
    )


@app.route("/admin/recipes")
def admin_recipes():
    recipes = fetch_all_recipes()
    # Missing = on the recipe, not on hand, and no on-hand substitute
    missing_by_recipe = {}
    rows = get_db().execute(
        f"""
        SELECT
            ri.recipe_id,
            i.name AS ingredient_name,
            ri.necessary,
            i.in_filter,
            (
                SELECT alt.name
                FROM ingredient_substitutes s
                JOIN ingredients alt ON alt.id = s.substitute_id
                WHERE s.ingredient_id = i.id
                  AND alt.in_filter = 1
                ORDER BY alt.name COLLATE NOCASE
                LIMIT 1
            ) AS substitute_name
        FROM recipe_ingredients ri
        JOIN ingredients i ON i.id = ri.ingredient_id
        WHERE i.in_filter = 0
        ORDER BY ri.necessary DESC, i.name COLLATE NOCASE ASC
        """
    ).fetchall()
    for row in rows:
        if row["substitute_name"]:
            missing_by_recipe.setdefault(row["recipe_id"], []).append(
                {
                    "name": row["ingredient_name"],
                    "necessary": bool(row["necessary"]),
                    "substitute": row["substitute_name"],
                }
            )
        else:
            missing_by_recipe.setdefault(row["recipe_id"], []).append(
                {
                    "name": row["ingredient_name"],
                    "necessary": bool(row["necessary"]),
                    "substitute": None,
                }
            )
    ratings = recipe_rating_summaries([r["id"] for r in recipes])
    return render_template(
        "admin_recipes.html",
        recipes=recipes,
        missing_by_recipe=missing_by_recipe,
        ratings=ratings,
    )


@app.route("/admin/recipes/new", methods=["GET", "POST"])
def admin_recipe_new():
    ingredients = fetch_ingredients()
    if request.method == "GET":
        return render_template(
            "admin_recipe_edit.html",
            recipe=None,
            lines=[],
            ingredients=ingredients,
            blank_rows=6,
            theme_catalog=theme_family_catalog(),
            selected_theme_ids=set(),
        )

    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Recipe name is required.", "error")
        return redirect(url_for("admin_recipe_new"))
    try:
        sort_order = int(request.form.get("sort_order") or 0)
    except ValueError:
        sort_order = 0
    published = 1 if request.form.get("published") else 0
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO recipes
            (name, description, instructions, glassware, notes, category, tags,
             published, sort_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            (request.form.get("description") or "").strip(),
            (request.form.get("instructions") or "").strip(),
            (request.form.get("glassware") or "").strip(),
            (request.form.get("notes") or "").strip(),
            (request.form.get("category") or "Other").strip() or "Other",
            (request.form.get("tags") or "").strip(),
            published,
            sort_order,
        ),
    )
    recipe_id = cur.lastrowid
    save_recipe_lines(db, recipe_id, request.form)
    save_recipe_themes(db, recipe_id, request.form)
    db.commit()
    flash(f'Added recipe "{name}".', "ok")
    return redirect(url_for("admin_recipe_edit", recipe_id=recipe_id))


@app.route("/admin/recipes/<int:recipe_id>", methods=["GET", "POST"])
def admin_recipe_edit(recipe_id):
    recipe = fetch_recipe(recipe_id)
    if not recipe:
        flash("Recipe not found.", "error")
        return redirect(url_for("admin_recipes"))
    ingredients = fetch_ingredients()

    if request.method == "GET":
        lines = fetch_recipe_lines(recipe_id)
        return render_template(
            "admin_recipe_edit.html",
            recipe=recipe,
            lines=lines,
            ingredients=ingredients,
            blank_rows=3,
            rating=recipe_rating_summaries([recipe_id]).get(recipe_id),
            theme_catalog=theme_family_catalog(),
            selected_theme_ids=fetch_recipe_theme_ids(recipe_id),
        )

    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Recipe name is required.", "error")
        return redirect(url_for("admin_recipe_edit", recipe_id=recipe_id))
    try:
        sort_order = int(request.form.get("sort_order") or 0)
    except ValueError:
        sort_order = 0
    published = 1 if request.form.get("published") else 0
    db = get_db()
    db.execute(
        """
        UPDATE recipes
        SET name = ?, description = ?, instructions = ?, glassware = ?,
            notes = ?, category = ?, tags = ?, published = ?,
            sort_order = ?
        WHERE id = ?
        """,
        (
            name,
            (request.form.get("description") or "").strip(),
            (request.form.get("instructions") or "").strip(),
            (request.form.get("glassware") or "").strip(),
            (request.form.get("notes") or "").strip(),
            (request.form.get("category") or "Other").strip() or "Other",
            (request.form.get("tags") or "").strip(),
            published,
            sort_order,
            recipe_id,
        ),
    )
    save_recipe_lines(db, recipe_id, request.form)
    save_recipe_themes(db, recipe_id, request.form)
    db.commit()
    flash(f'Updated "{name}".', "ok")
    return redirect(url_for("admin_recipe_edit", recipe_id=recipe_id))


@app.route("/admin/recipes/<int:recipe_id>/ratings/clear", methods=["POST"])
def clear_recipe_ratings(recipe_id):
    db = get_db()
    row = db.execute(
        "SELECT name FROM recipes WHERE id = ?", (recipe_id,)
    ).fetchone()
    if not row:
        flash("Recipe not found.", "error")
        return redirect(url_for("admin_recipes"))
    cur = db.execute("DELETE FROM ratings WHERE recipe_id = ?", (recipe_id,))
    db.commit()
    flash(f'Cleared {cur.rowcount} rating(s) for "{row["name"]}".', "ok")
    nxt = request.form.get("next") or request.args.get("next")
    if nxt == "edit":
        return redirect(url_for("admin_recipe_edit", recipe_id=recipe_id))
    return redirect(url_for("admin_recipes"))


@app.route("/admin/recipes/delete/<int:recipe_id>", methods=["POST"])
def delete_recipe(recipe_id):
    db = get_db()
    row = db.execute(
        "SELECT name FROM recipes WHERE id = ?", (recipe_id,)
    ).fetchone()
    db.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
    db.commit()
    if row:
        flash(f'Deleted "{row["name"]}".', "ok")
    else:
        flash("Recipe not found.", "error")
    return redirect(url_for("admin_recipes"))


@app.route("/admin/recipes/toggle/<int:recipe_id>", methods=["POST"])
def toggle_recipe(recipe_id):
    db = get_db()
    row = db.execute(
        "SELECT name, published FROM recipes WHERE id = ?", (recipe_id,)
    ).fetchone()
    if not row:
        flash("Recipe not found.", "error")
        return redirect(url_for("admin_recipes"))
    new_val = 0 if row["published"] else 1
    db.execute(
        "UPDATE recipes SET published = ? WHERE id = ?", (new_val, recipe_id)
    )
    db.commit()
    state = "published" if new_val else "hidden"
    flash(f'"{row["name"]}" is now {state}.', "ok")
    return redirect(url_for("admin_recipes"))


init_db()


if __name__ == "__main__":
    host = os.environ.get("DRINKS_HOST", "0.0.0.0")
    port = int(os.environ.get("DRINKS_PORT", "80"))
    debug = os.environ.get("DRINKS_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)
