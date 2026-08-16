"""
Lightweight SQLite persistence layer.
No ORM on purpose -- keeps the self-hosted footprint to a single file
(pinforge.db) with zero extra services to run.
"""
import os
import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timedelta

DB_PATH = os.environ.get("DB_PATH", "data/pinforge.db")


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amazon_url TEXT NOT NULL,
                asin TEXT,
                product_title TEXT,
                product_price TEXT,
                product_image_url TEXT,
                pin_title TEXT,
                pin_description TEXT,
                affiliate_link TEXT,
                generated_image_path TEXT,
                pinterest_board_id TEXT,
                pinterest_pin_id TEXT,
                status TEXT DEFAULT 'draft',
                retailer_label TEXT DEFAULT 'Amazon',
                wordpress_post_id INTEGER,
                wordpress_post_url TEXT,
                created_at TEXT NOT NULL,
                published_at TEXT
            );

            CREATE TABLE IF NOT EXISTS scout_watches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                search_index TEXT NOT NULL,
                keywords TEXT,
                min_price REAL,
                max_price REAL,
                min_rating REAL,
                min_reviews INTEGER,
                item_count INTEGER DEFAULT 10,
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                last_run_at TEXT
            );

            CREATE TABLE IF NOT EXISTS scout_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watch_id INTEGER,
                asin TEXT NOT NULL UNIQUE,
                title TEXT,
                price TEXT,
                image_url TEXT,
                rating REAL,
                review_count INTEGER,
                status TEXT DEFAULT 'pending',
                pin_id INTEGER,
                discovered_at TEXT NOT NULL,
                decided_at TEXT
            );

            CREATE TABLE IF NOT EXISTS oauth_tokens (
                provider TEXT PRIMARY KEY,
                token_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS retailer_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                domain_pattern TEXT NOT NULL,
                link_template TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS clicks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pin_id INTEGER NOT NULL,
                clicked_at TEXT NOT NULL,
                referrer TEXT,
                user_agent TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_clicks_pin_id ON clicks(pin_id);
            CREATE INDEX IF NOT EXISTS idx_clicks_clicked_at ON clicks(clicked_at);
            """
        )
        _migrate_scout_candidates_to_global_unique(conn)
        _migrate_pins_add_retailer_label(conn)
        _migrate_pins_add_wordpress_fields(conn)


def _migrate_pins_add_retailer_label(conn):
    """Installs from before generic-retailer support won't have this
    column yet -- add it rather than requiring a fresh database."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(pins)").fetchall()]
    if "retailer_label" not in cols:
        conn.execute("ALTER TABLE pins ADD COLUMN retailer_label TEXT DEFAULT 'Amazon'")


def _migrate_pins_add_wordpress_fields(conn):
    """Installs from before landing-page support won't have these
    columns yet -- add them rather than requiring a fresh database."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(pins)").fetchall()]
    if "wordpress_post_id" not in cols:
        conn.execute("ALTER TABLE pins ADD COLUMN wordpress_post_id INTEGER")
    if "wordpress_post_url" not in cols:
        conn.execute("ALTER TABLE pins ADD COLUMN wordpress_post_url TEXT")


def _migrate_scout_candidates_to_global_unique(conn):
    """One-time migration for installs created before Scout deduplicated
    globally: the original schema allowed the same ASIN to appear once per
    watch (UNIQUE(asin, watch_id)). This rebuilds the table with a single
    UNIQUE(asin) so a product you've already seen or decided on under any
    watch is never suggested again, keeping the oldest decision on record."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='scout_candidates'"
    ).fetchone()
    if not row or "UNIQUE(asin, watch_id)" not in (row["sql"] or ""):
        return  # already on the new schema, or table didn't exist before executescript created it

    conn.executescript(
        """
        ALTER TABLE scout_candidates RENAME TO scout_candidates_old;

        CREATE TABLE scout_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watch_id INTEGER,
            asin TEXT NOT NULL UNIQUE,
            title TEXT,
            price TEXT,
            image_url TEXT,
            rating REAL,
            review_count INTEGER,
            status TEXT DEFAULT 'pending',
            pin_id INTEGER,
            discovered_at TEXT NOT NULL,
            decided_at TEXT
        );

        INSERT OR IGNORE INTO scout_candidates
            (watch_id, asin, title, price, image_url, rating, review_count,
             status, pin_id, discovered_at, decided_at)
        SELECT watch_id, asin, title, price, image_url, rating, review_count,
               status, pin_id, discovered_at, decided_at
        FROM scout_candidates_old
        ORDER BY id ASC;

        DROP TABLE scout_candidates_old;
        """
    )


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------- Pins ----------

def create_pin(data: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO pins (
                amazon_url, asin, product_title, product_price, product_image_url,
                pin_title, pin_description, affiliate_link, generated_image_path,
                status, retailer_label, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("amazon_url"),
                data.get("asin"),
                data.get("product_title"),
                data.get("product_price"),
                data.get("product_image_url"),
                data.get("pin_title"),
                data.get("pin_description"),
                data.get("affiliate_link"),
                data.get("generated_image_path"),
                data.get("status", "draft"),
                data.get("retailer_label", "Amazon"),
                datetime.utcnow().isoformat(),
            ),
        )
        return cur.lastrowid


def update_pin(pin_id: int, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [pin_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE pins SET {cols} WHERE id = ?", values)


def get_pin(pin_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM pins WHERE id = ?", (pin_id,)).fetchone()
        return dict(row) if row else None


def delete_pin(pin_id: int) -> str | None:
    """Deletes a pin's DB row (and any clicks logged against it -- their
    /go/<id> redirect would just 404 forever otherwise, no reason to keep
    them). Un-links any Scout candidate that pointed at this pin rather
    than leaving it referencing a pin that no longer exists.

    Returns the generated_image_path so the caller can also remove the
    actual PNG file from disk -- that's a filesystem concern, not a DB
    one, so it's left to app.py rather than done here."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT generated_image_path FROM pins WHERE id = ?", (pin_id,)
        ).fetchone()
        if not row:
            return None
        image_path = row["generated_image_path"]

        conn.execute("DELETE FROM clicks WHERE pin_id = ?", (pin_id,))
        conn.execute(
            "UPDATE scout_candidates SET pin_id = NULL WHERE pin_id = ?", (pin_id,)
        )
        conn.execute("DELETE FROM pins WHERE id = ?", (pin_id,))
        return image_path


def list_pins(limit: int = 50):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT p.*, COALESCE(c.click_count, 0) AS click_count
            FROM pins p
            LEFT JOIN (
                SELECT pin_id, COUNT(*) AS click_count FROM clicks GROUP BY pin_id
            ) c ON c.pin_id = p.id
            ORDER BY p.id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- OAuth tokens ----------

def save_token(provider: str, token: dict):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO oauth_tokens (provider, token_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET
                token_json = excluded.token_json,
                updated_at = excluded.updated_at
            """,
            (provider, json.dumps(token), datetime.utcnow().isoformat()),
        )


def load_token(provider: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT token_json FROM oauth_tokens WHERE provider = ?", (provider,)
        ).fetchone()
        return json.loads(row["token_json"]) if row else None


# ---------- Settings (e.g. selected default Pinterest board) ----------

def set_setting(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def get_setting(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


# ---------- Scout: watches (what to look for) ----------

def create_watch(data: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO scout_watches (
                label, search_index, keywords, min_price, max_price,
                min_rating, min_reviews, item_count, active, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                data.get("label"),
                data.get("search_index"),
                data.get("keywords"),
                data.get("min_price"),
                data.get("max_price"),
                data.get("min_rating"),
                data.get("min_reviews"),
                data.get("item_count", 10),
                datetime.utcnow().isoformat(),
            ),
        )
        return cur.lastrowid


def list_watches(active_only: bool = False):
    with get_conn() as conn:
        q = "SELECT * FROM scout_watches"
        if active_only:
            q += " WHERE active = 1"
        q += " ORDER BY id DESC"
        return [dict(r) for r in conn.execute(q).fetchall()]


def delete_watch(watch_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM scout_watches WHERE id = ?", (watch_id,))


def touch_watch_run(watch_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE scout_watches SET last_run_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), watch_id),
        )


# ---------- Scout: candidates (what was found) ----------

def add_candidate(data: dict) -> int | None:
    """Insert a discovered product. Returns None if it's a duplicate --
    this ASIN has been seen before under ANY watch, not just this one --
    instead of raising."""
    with get_conn() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO scout_candidates (
                    watch_id, asin, title, price, image_url, rating,
                    review_count, status, discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    data.get("watch_id"),
                    data.get("asin"),
                    data.get("title"),
                    data.get("price"),
                    data.get("image_url"),
                    data.get("rating"),
                    data.get("review_count"),
                    datetime.utcnow().isoformat(),
                ),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def list_candidates(status: str = "pending", limit: int = 100):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.*, w.label AS watch_label
            FROM scout_candidates c
            LEFT JOIN scout_watches w ON w.id = c.watch_id
            WHERE c.status = ?
            ORDER BY c.id DESC LIMIT ?
            """,
            (status, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_candidate(candidate_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM scout_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        return dict(row) if row else None


def decide_candidate(candidate_id: int, status: str, pin_id: int | None = None):
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE scout_candidates
            SET status = ?, pin_id = ?, decided_at = ?
            WHERE id = ?
            """,
            (status, pin_id, datetime.utcnow().isoformat(), candidate_id),
        )


def asin_already_pinned(asin: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM pins WHERE asin = ?", (asin,)).fetchone()
        return row is not None


def all_known_products() -> list[dict]:
    """Every product Scout has ever surfaced (any status, any watch) plus
    every product that's ever been forged into a pin -- the full pool to
    check new discoveries against, for both exact-ASIN and fuzzy-title
    duplicate detection."""
    with get_conn() as conn:
        candidates = conn.execute(
            "SELECT asin, title FROM scout_candidates"
        ).fetchall()
        pins = conn.execute(
            "SELECT asin, product_title AS title FROM pins WHERE asin IS NOT NULL"
        ).fetchall()
    seen_asins = set()
    result = []
    for row in list(candidates) + list(pins):
        if row["asin"] in seen_asins:
            continue
        seen_asins.add(row["asin"])
        result.append({"asin": row["asin"], "title": row["title"] or ""})
    return result


# ---------- Retailer profiles (generic, non-Amazon retailers) ----------

def create_retailer_profile(data: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO retailer_profiles (label, domain_pattern, link_template, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                data.get("label"),
                data.get("domain_pattern"),
                data.get("link_template") or None,
                datetime.utcnow().isoformat(),
            ),
        )
        return cur.lastrowid


def list_retailer_profiles() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM retailer_profiles ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_retailer_profile(profile_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM retailer_profiles WHERE id = ?", (profile_id,))


def find_retailer_profile_for_domain(domain: str) -> dict | None:
    """Longest-match: if someone has both 'walmart.com' and
    'grocery.walmart.com' configured, the more specific one wins."""
    profiles = list_retailer_profiles()
    matches = [p for p in profiles if p["domain_pattern"] and p["domain_pattern"] in domain]
    if not matches:
        return None
    return max(matches, key=lambda p: len(p["domain_pattern"]))


# ---------- Click tracking ----------

def record_click(pin_id: int, referrer: str | None = None, user_agent: str | None = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO clicks (pin_id, clicked_at, referrer, user_agent) VALUES (?, ?, ?, ?)",
            (pin_id, datetime.utcnow().isoformat(), referrer, user_agent),
        )


def get_analytics_summary(top_n: int = 10, recent_days: int = 7) -> dict:
    with get_conn() as conn:
        total_clicks = conn.execute("SELECT COUNT(*) AS n FROM clicks").fetchone()["n"]

        recent_cutoff = (datetime.utcnow() - timedelta(days=recent_days)).isoformat()
        recent_clicks = conn.execute(
            "SELECT COUNT(*) AS n FROM clicks WHERE clicked_at >= ?", (recent_cutoff,)
        ).fetchone()["n"]

        published_pins = conn.execute(
            "SELECT COUNT(*) AS n FROM pins WHERE status = 'published'"
        ).fetchone()["n"]

        top_pins = conn.execute(
            """
            SELECT p.id, p.pin_title, p.retailer_label, p.product_price,
                   p.generated_image_path, p.pinterest_pin_id, p.published_at,
                   COUNT(c.id) AS click_count
            FROM pins p
            JOIN clicks c ON c.pin_id = p.id
            GROUP BY p.id
            ORDER BY click_count DESC, p.id DESC
            LIMIT ?
            """,
            (top_n,),
        ).fetchall()

        by_retailer = conn.execute(
            """
            SELECT p.retailer_label AS retailer_label, COUNT(c.id) AS click_count
            FROM clicks c
            JOIN pins p ON p.id = c.pin_id
            GROUP BY p.retailer_label
            ORDER BY click_count DESC
            """
        ).fetchall()

        return {
            "total_clicks": total_clicks,
            "recent_clicks": recent_clicks,
            "recent_days": recent_days,
            "published_pins": published_pins,
            "top_pins": [dict(r) for r in top_pins],
            "by_retailer": [dict(r) for r in by_retailer],
        }
