"""
Capa de datos: SQLite + parseo de batallas + estadísticas.

Multi-jugador. Cada partida se guarda asociada al tag del jugador seguido.
Además:
- Copas de cada brawler (mías, aliados y rivales) para contextualizar match-ups.
- Stats manuales OPCIONALES por partida (asesinatos, muertes, daño, curación),
  que el jugador anota a mano (la API no las da). Siempre parciales.
- Icono del jugador (id) para mostrarlo en la cabecera.

init_db() migra en sitio las bases antiguas (añade columnas/tablas que falten)
para no perder datos ya recopilados.
"""

from __future__ import annotations

import os
import sqlite3
import hashlib
import json
import secrets
from datetime import datetime, timezone, timedelta

from . import brawler_extra  # índice de roles (para filtrar/agregar por rol)

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "brawl_stats.db"))

GROUP_COLUMNS = {"brawler": "my_brawler", "mode": "mode", "map": "map"}


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(cur, table, col, decl):
    cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]
    if col not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS players (
            tag TEXT PRIMARY KEY, name TEXT, added_at TEXT,
            last_polled TEXT, active INTEGER DEFAULT 1, icon_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS battles (
            id TEXT PRIMARY KEY, player_tag TEXT, battle_time TEXT,
            mode TEXT, map TEXT, battle_type TEXT, result TEXT, rank INTEGER,
            is_win INTEGER, my_brawler TEXT, my_trophies INTEGER,
            trophy_change INTEGER, duration INTEGER, is_star_player INTEGER, ingested_at TEXT
        );
        CREATE TABLE IF NOT EXISTS opponents (battle_id TEXT, brawler TEXT, trophies INTEGER);
        CREATE TABLE IF NOT EXISTS allies    (battle_id TEXT, brawler TEXT, trophies INTEGER);
        CREATE TABLE IF NOT EXISTS brawler_collection (
            player_tag TEXT NOT NULL, brawler_id INTEGER NOT NULL, brawler_name TEXT,
            power INTEGER, rank INTEGER, trophies INTEGER, highest_trophies INTEGER,
            star_power_ids TEXT, gadget_ids TEXT, gear_ids TEXT,
            hypercharge_ids TEXT, skin_id INTEGER, skin_name TEXT, prestige_level INTEGER,
            updated_at TEXT,
            PRIMARY KEY (player_tag, brawler_id)
        );
        CREATE TABLE IF NOT EXISTS manual_stats (
            battle_id TEXT PRIMARY KEY, kills INTEGER, deaths INTEGER,
            damage INTEGER, healing INTEGER, notes TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT, player_tag TEXT, name TEXT,
            filters_json TEXT, scope_label TEXT, status TEXT,
            content TEXT, error TEXT, created_at TEXT, completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, created_at TEXT,
            reports_remaining INTEGER, quota_period TEXT, country TEXT
        );
        CREATE TABLE IF NOT EXISTS user_players (
            user_id INTEGER NOT NULL, player_tag TEXT NOT NULL, added_at TEXT,
            PRIMARY KEY (user_id, player_tag)
        );
        CREATE TABLE IF NOT EXISTS custom_rankings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner_user_id INTEGER NOT NULL,
            name TEXT NOT NULL, share_token TEXT UNIQUE NOT NULL,
            player_tags TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS user_custom_rankings (
            user_id INTEGER NOT NULL, ranking_id INTEGER NOT NULL, added_at TEXT,
            PRIMARY KEY (user_id, ranking_id)
        );
        CREATE TABLE IF NOT EXISTS wiki_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL,
            parent_id INTEGER, title TEXT NOT NULL, body TEXT,
            sort_order REAL NOT NULL DEFAULT 0, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS wiki_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, author_user_id INTEGER NOT NULL,
            kind TEXT NOT NULL, node_id INTEGER, payload TEXT,
            summary TEXT, justification TEXT, status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT, reviewed_at TEXT, reviewer_user_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS wiki_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER, type TEXT,
            title TEXT, body TEXT, parent_id INTEGER,
            change_kind TEXT, by_user_id INTEGER, changed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner_user_id INTEGER NOT NULL,
            name TEXT NOT NULL, kind TEXT NOT NULL, mode TEXT NOT NULL, visibility TEXT NOT NULL,
            language TEXT, max_participants INTEGER DEFAULT 12, format TEXT,
            match_type TEXT DEFAULT 'bo1', date_start TEXT, date_end TEXT,
            description TEXT, poster_url TEXT, password_hash TEXT,
            require_confirmation INTEGER DEFAULT 1, settings TEXT,
            status TEXT DEFAULT 'open', hidden INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS event_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
            user_id INTEGER, player_tag TEXT NOT NULL, team_id INTEGER,
            added_by_owner INTEGER DEFAULT 0, joined_at TEXT, seed_cups INTEGER,
            UNIQUE(event_id, player_tag)
        );
        CREATE TABLE IF NOT EXISTS event_teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
            name TEXT NOT NULL, logo_url TEXT, captain_user_id INTEGER, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS event_follows (
            event_id INTEGER NOT NULL, user_id INTEGER NOT NULL, followed_at TEXT,
            PRIMARY KEY (event_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            type TEXT, title TEXT, body TEXT, event_id INTEGER, data TEXT,
            read INTEGER DEFAULT 0, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS event_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL, player_tag TEXT NOT NULL, team_name TEXT,
            message TEXT, status TEXT DEFAULT 'pending', created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS event_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
            round INTEGER DEFAULT 1, bracket_pos INTEGER, a_tag TEXT, b_tag TEXT, a_team INTEGER, b_team INTEGER,
            mode TEXT, map TEXT, status TEXT DEFAULT 'pending',
            score_a INTEGER, score_b INTEGER, winner TEXT, evidence_battle_id TEXT, roster_a TEXT, roster_b TEXT,
            scheduled_at TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_userplayers_tag ON user_players(player_tag);
        CREATE INDEX IF NOT EXISTS idx_reports_player ON reports(player_tag);
        CREATE INDEX IF NOT EXISTS idx_battles_player  ON battles(player_tag);
        CREATE INDEX IF NOT EXISTS idx_battles_brawler ON battles(my_brawler);
        CREATE INDEX IF NOT EXISTS idx_battles_mode    ON battles(mode);
        CREATE INDEX IF NOT EXISTS idx_battles_map     ON battles(map);
        CREATE INDEX IF NOT EXISTS idx_opponents_brawler ON opponents(brawler);
        CREATE INDEX IF NOT EXISTS idx_opponents_battle  ON opponents(battle_id);
        CREATE INDEX IF NOT EXISTS idx_allies_battle     ON allies(battle_id);
        CREATE INDEX IF NOT EXISTS idx_brcoll_player     ON brawler_collection(player_tag);
        CREATE INDEX IF NOT EXISTS idx_ematches_event    ON event_matches(event_id);
        """
    )
    # Migración de bases antiguas: añade columnas nuevas si faltan.
    _ensure_column(cur, "players", "icon_id", "INTEGER")
    _ensure_column(cur, "players", "club_name", "TEXT")
    _ensure_column(cur, "users", "country", "TEXT")
    _ensure_column(cur, "users", "ranking_order", "TEXT")
    _ensure_column(cur, "users", "is_admin", "INTEGER DEFAULT 0")
    _ensure_column(cur, "battles", "my_trophies", "INTEGER")
    _ensure_column(cur, "opponents", "trophies", "INTEGER")
    _ensure_column(cur, "allies", "trophies", "INTEGER")
    _ensure_column(cur, "events", "hidden", "INTEGER DEFAULT 0")
    _ensure_column(cur, "event_matches", "bracket_pos", "INTEGER")
    _ensure_column(cur, "event_participants", "seed_cups", "INTEGER")
    _ensure_column(cur, "event_matches", "roster_a", "TEXT")
    _ensure_column(cur, "event_matches", "roster_b", "TEXT")
    _ensure_column(cur, "brawler_collection", "hypercharge_ids", "TEXT")
    _ensure_column(cur, "brawler_collection", "skin_id", "INTEGER")
    _ensure_column(cur, "brawler_collection", "skin_name", "TEXT")
    _ensure_column(cur, "brawler_collection", "prestige_level", "INTEGER")
    # El usuario itxialdiak es administrador por defecto.
    cur.execute("UPDATE users SET is_admin=1 WHERE username='itxialdiak'")
    conn.commit()
    conn.close()
    seed_wiki_if_empty()


# ---------------------------------------------------------------------------
# Jugadores
# ---------------------------------------------------------------------------

def normalize_tag(tag: str) -> str:
    t = (tag or "").strip().upper()
    return t if t.startswith("#") else "#" + t


def add_player(tag: str, name: str | None = None, icon_id: int | None = None,
               club_name: str | None = None) -> bool:
    tag = normalize_tag(tag)
    conn = get_conn(); cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cur.execute("INSERT OR IGNORE INTO players (tag, name, added_at, active, icon_id, club_name) VALUES (?,?,?,1,?,?)",
                (tag, name, now, icon_id, club_name))
    added = cur.rowcount == 1
    if not added and (name or icon_id is not None or club_name is not None):
        cur.execute("UPDATE players SET name=COALESCE(?,name), icon_id=COALESCE(?,icon_id), club_name=COALESCE(?,club_name) WHERE tag=?",
                    (name, icon_id, club_name, tag))
    conn.commit(); conn.close()
    return added


def update_player_profile(tag: str, name: str | None, icon_id: int | None,
                          club_name: str | None = None) -> None:
    conn = get_conn()
    conn.execute("UPDATE players SET name=COALESCE(?,name), icon_id=COALESCE(?,icon_id), club_name=COALESCE(?,club_name) WHERE tag=?",
                 (name, icon_id, club_name, normalize_tag(tag)))
    conn.commit(); conn.close()


def player_needs_profile(tag: str) -> bool:
    """True si aún no tenemos icono (para hacer un backfill puntual del perfil)."""
    conn = get_conn()
    row = conn.execute("SELECT icon_id FROM players WHERE tag=?", (normalize_tag(tag),)).fetchone()
    conn.close()
    return bool(row) and row["icon_id"] is None


def remove_player(tag: str) -> None:
    tag = normalize_tag(tag)
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM manual_stats WHERE battle_id IN (SELECT id FROM battles WHERE player_tag=?)", (tag,))
    cur.execute("DELETE FROM opponents WHERE battle_id IN (SELECT id FROM battles WHERE player_tag=?)", (tag,))
    cur.execute("DELETE FROM allies    WHERE battle_id IN (SELECT id FROM battles WHERE player_tag=?)", (tag,))
    cur.execute("DELETE FROM battles WHERE player_tag=?", (tag,))
    cur.execute("DELETE FROM players WHERE tag=?", (tag,))
    conn.commit(); conn.close()


def list_players() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT p.tag, p.name, p.added_at, p.last_polled, p.active, p.icon_id, p.club_name,
               (SELECT COUNT(*) FROM battles b WHERE b.player_tag = p.tag) AS battles
        FROM players p ORDER BY p.added_at
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def active_player_tags() -> list[str]:
    """Jugadores a sondear: la unión deduplicada de los que algún usuario sigue."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT DISTINCT up.player_tag FROM user_players up
           JOIN players p ON p.tag = up.player_tag WHERE p.active=1"""
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def mark_polled(tag: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE players SET last_polled=? WHERE tag=?",
                 (datetime.now(timezone.utc).isoformat(), normalize_tag(tag)))
    conn.commit(); conn.close()


# ---------------------------------------------------------------------------
# Colección de brawlers del jugador (snapshot persistido de la API oficial)
# ---------------------------------------------------------------------------

def snapshot_brawlers(tag: str, brawlers: list | None) -> int:
    """Guarda/actualiza la colección de brawlers del jugador a partir de
    `profile["brawlers"]` de la API oficial. Idempotente (upsert por
    player_tag+brawler_id). Devuelve cuántos brawlers se han escrito."""
    if not brawlers:
        return 0
    tag = normalize_tag(tag)
    now = datetime.now(timezone.utc).isoformat()

    def ids(b, key):
        return json.dumps([x.get("id") for x in (b.get(key) or []) if x.get("id") is not None])

    conn = get_conn(); cur = conn.cursor()
    n = 0
    for b in brawlers:
        bid = b.get("id")
        if bid is None:
            continue
        skin = b.get("skin") or {}
        cur.execute(
            """INSERT INTO brawler_collection
                 (player_tag, brawler_id, brawler_name, power, rank, trophies, highest_trophies,
                  star_power_ids, gadget_ids, gear_ids, hypercharge_ids, skin_id, skin_name,
                  prestige_level, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(player_tag, brawler_id) DO UPDATE SET
                 brawler_name=excluded.brawler_name, power=excluded.power, rank=excluded.rank,
                 trophies=excluded.trophies, highest_trophies=excluded.highest_trophies,
                 star_power_ids=excluded.star_power_ids, gadget_ids=excluded.gadget_ids,
                 gear_ids=excluded.gear_ids, hypercharge_ids=excluded.hypercharge_ids,
                 skin_id=excluded.skin_id, skin_name=excluded.skin_name,
                 prestige_level=excluded.prestige_level, updated_at=excluded.updated_at""",
            (tag, bid, b.get("name"), b.get("power"), b.get("rank"), b.get("trophies"),
             b.get("highestTrophies"), ids(b, "starPowers"), ids(b, "gadgets"), ids(b, "gears"),
             ids(b, "hyperCharges"), skin.get("id"), skin.get("name"), b.get("prestigeLevel"), now),
        )
        n += 1
    conn.commit(); conn.close()
    return n


def get_collection(tag: str) -> list[dict]:
    """Colección persistida del jugador, con los arrays JSON ya deserializados,
    ordenada por trofeos descendentes."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM brawler_collection WHERE player_tag=? ORDER BY trophies DESC",
        (normalize_tag(tag),),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("star_power_ids", "gadget_ids", "gear_ids", "hypercharge_ids"):
            try:
                d[k] = json.loads(d.get(k) or "[]")
            except Exception:  # noqa: BLE001
                d[k] = []
        out.append(d)
    return out


def collection_counts(tag: str) -> dict:
    """Resumen de la colección: brawlers, star powers/gadgets/gears poseídos,
    distribución de poder y bandas de trofeos. Base de contadores y rating."""
    coll = get_collection(tag)
    powers = [b.get("power") or 0 for b in coll]
    trophies = [b.get("trophies") or 0 for b in coll]
    return {
        "brawlers": len(coll),
        "star_powers_owned": sum(len(b["star_power_ids"]) for b in coll),
        "gadgets_owned": sum(len(b["gadget_ids"]) for b in coll),
        "gears_owned": sum(len(b["gear_ids"]) for b in coll),
        "hypercharges_owned": sum(1 for b in coll if b.get("hypercharge_ids")),
        "p11": sum(1 for p in powers if p >= 11),
        "avg_power": round(sum(powers) / len(powers), 1) if powers else 0,
        "total_trophies": sum(trophies),
        "bands": {b: sum(1 for t in trophies if t >= b) for b in (300, 500, 750, 1000, 1250)},
    }


def account_rating(tag: str, catalog_totals: dict | None = None) -> dict:
    """Rating de cuenta 0–100 con sub-scores, calculado por nosotros (la API no lo
    da). `catalog_totals` = {brawlers, star_powers, gadgets} del catálogo de
    Brawlify; si falta, se usan estimaciones para no romper. Pesos ajustables."""
    coll = get_collection(tag)
    ct = catalog_totals or {}
    total_brawlers = ct.get("brawlers") or len(coll) or 1
    # Maestría: star powers + gadgets + hipercargas poseídos / disponibles.
    avail = (ct.get("star_powers") or 0) + (ct.get("gadgets") or 0) + (ct.get("hypercharges") or 0) \
        or (len(coll) * 5) or 1

    owned = sum(len(b["star_power_ids"]) + len(b["gadget_ids"]) + (1 if b.get("hypercharge_ids") else 0)
                for b in coll)
    powers = [b.get("power") or 0 for b in coll]
    trophies = [b.get("trophies") or 0 for b in coll]

    collection = 100 * len(coll) / total_brawlers
    mastery = 100 * owned / avail
    efficiency = 100 * sum(powers) / (len(powers) * 11) if powers else 0
    # Pushing: media de trofeos por brawler, con techo de 1000 por brawler.
    pushing = 100 * sum(min(t, 1000) for t in trophies) / (len(trophies) * 1000) if trophies else 0

    def clamp(x):
        return round(max(0, min(100, x)))

    collection, mastery, efficiency, pushing = map(clamp, (collection, mastery, efficiency, pushing))
    overall = round(0.30 * collection + 0.30 * mastery + 0.20 * efficiency + 0.20 * pushing)
    tier = next(name for thr, name in
                ((85, "Élite"), (65, "Avanzado"), (45, "Competente"), (25, "En desarrollo"), (0, "Iniciado"))
                if overall >= thr)
    return {"overall": overall, "tier": tier, "collection": collection,
            "mastery": mastery, "efficiency": efficiency, "pushing": pushing}


# ---------------------------------------------------------------------------
# Usuarios y relación usuario <-> jugadores
# ---------------------------------------------------------------------------

def create_user(username: str, password_hash: str) -> int | None:
    """Crea un usuario. Devuelve su id, o None si el nombre ya existe."""
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)",
            (username, password_hash, datetime.now(timezone.utc).isoformat()),
        )
        uid = cur.lastrowid
        conn.commit()
        return uid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def get_user_by_username(username: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_user_country(user_id: int, country: str | None) -> None:
    conn = get_conn()
    conn.execute("UPDATE users SET country=? WHERE id=?", (country, user_id))
    conn.commit(); conn.close()


def set_user_password(user_id: int, password_hash: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
    conn.commit(); conn.close()


# --------------------------- Rankings personalizados (liguillas) ---------------------------

def _norm_tags(tags) -> list:
    out, seen = [], set()
    for t in (tags or []):
        if not t or not str(t).strip():
            continue
        nt = normalize_tag(str(t))
        if nt not in seen:
            seen.add(nt); out.append(nt)
    return out


def _cr_row(row) -> dict | None:
    if not row:
        return None
    d = dict(row)
    try:
        d["player_tags"] = json.loads(d.get("player_tags") or "[]")
    except Exception:  # noqa: BLE001
        d["player_tags"] = []
    return d


def create_custom_ranking(owner_user_id: int, name: str, tags) -> int:
    token = secrets.token_urlsafe(9)
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO custom_rankings (owner_user_id, name, share_token, player_tags, created_at) "
        "VALUES (?,?,?,?,?)",
        (owner_user_id, (name or "Liguilla").strip()[:60], token,
         json.dumps(_norm_tags(tags)), datetime.now(timezone.utc).isoformat()))
    rid = cur.lastrowid
    conn.commit(); conn.close()
    return rid


def get_custom_ranking(rid: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM custom_rankings WHERE id=?", (rid,)).fetchone()
    conn.close()
    return _cr_row(row)


def get_custom_ranking_by_token(token: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM custom_rankings WHERE share_token=?", (token,)).fetchone()
    conn.close()
    return _cr_row(row)


def list_custom_rankings_for_user(user_id: int) -> list:
    """Rankings que el usuario posee + a los que se ha suscrito (importado)."""
    conn = get_conn()
    owned = conn.execute(
        "SELECT * FROM custom_rankings WHERE owner_user_id=? ORDER BY id", (user_id,)).fetchall()
    subs = conn.execute(
        "SELECT cr.* FROM custom_rankings cr "
        "JOIN user_custom_rankings ucr ON ucr.ranking_id=cr.id "
        "WHERE ucr.user_id=? AND cr.owner_user_id<>? ORDER BY ucr.added_at",
        (user_id, user_id)).fetchall()
    conn.close()
    res = []
    for r in owned:
        d = _cr_row(r); d["owned"] = True; res.append(d)
    for r in subs:
        d = _cr_row(r); d["owned"] = False; res.append(d)
    return res


def user_can_view_ranking(user_id: int, rid: int) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM custom_rankings WHERE id=? AND owner_user_id=? "
        "UNION SELECT 1 FROM user_custom_rankings WHERE ranking_id=? AND user_id=?",
        (rid, user_id, rid, user_id)).fetchone()
    conn.close()
    return row is not None


def update_custom_ranking(rid: int, owner_user_id: int, name: str, tags) -> bool:
    conn = get_conn()
    cur = conn.execute(
        "UPDATE custom_rankings SET name=?, player_tags=? WHERE id=? AND owner_user_id=?",
        ((name or "Liguilla").strip()[:60], json.dumps(_norm_tags(tags)), rid, owner_user_id))
    n = cur.rowcount
    conn.commit(); conn.close()
    return n > 0


def delete_or_unsubscribe_ranking(user_id: int, rid: int) -> str:
    """Dueño -> borra el ranking y sus suscripciones. Suscrito -> solo se da de baja."""
    conn = get_conn(); cur = conn.cursor()
    owner = cur.execute("SELECT owner_user_id FROM custom_rankings WHERE id=?", (rid,)).fetchone()
    if owner and owner[0] == user_id:
        cur.execute("DELETE FROM user_custom_rankings WHERE ranking_id=?", (rid,))
        cur.execute("DELETE FROM custom_rankings WHERE id=?", (rid,))
        result = "deleted"
    else:
        cur.execute("DELETE FROM user_custom_rankings WHERE ranking_id=? AND user_id=?", (rid, user_id))
        result = "unsubscribed"
    conn.commit(); conn.close()
    return result


def subscribe_ranking(user_id: int, rid: int) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO user_custom_rankings (user_id, ranking_id, added_at) VALUES (?,?,?)",
        (user_id, rid, datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()


def set_rankings_order(user_id: int, order: list) -> None:
    conn = get_conn()
    conn.execute("UPDATE users SET ranking_order=? WHERE id=?", (json.dumps(order), user_id))
    conn.commit(); conn.close()


def get_rankings_order(user_id: int):
    conn = get_conn()
    row = conn.execute("SELECT ranking_order FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if row and row["ranking_order"]:
        try:
            return json.loads(row["ranking_order"])
        except Exception:  # noqa: BLE001
            return None
    return None


# --------------------------- Wiki / Guía de estrategia ---------------------------

WIKI_SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "wiki_seed.json")


def seed_wiki_if_empty() -> None:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) c FROM wiki_nodes").fetchone()["c"]
    if n > 0:
        conn.close(); return
    try:
        seed = json.load(open(WIKI_SEED_PATH, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        conn.close(); return
    now = datetime.now(timezone.utc).isoformat()
    order = 0
    for node in seed:
        order += 1
        if node.get("type") == "separator":
            conn.execute(
                "INSERT INTO wiki_nodes (type,parent_id,title,body,sort_order,updated_at) "
                "VALUES ('separator',NULL,?,NULL,?,?)", (node["title"], order, now))
        else:
            cur = conn.execute(
                "INSERT INTO wiki_nodes (type,parent_id,title,body,sort_order,updated_at) "
                "VALUES ('section',NULL,?,?,?,?)", (node["title"], node.get("body", ""), order, now))
            sid = cur.lastrowid
            so = 0
            for sub in node.get("subs", []):
                so += 1
                conn.execute(
                    "INSERT INTO wiki_nodes (type,parent_id,title,body,sort_order,updated_at) "
                    "VALUES ('subsection',?,?,?,?,?)", (sid, sub["title"], sub.get("body", ""), so, now))
    conn.commit(); conn.close()


def get_wiki_tree() -> list:
    conn = get_conn()
    tops = conn.execute(
        "SELECT id,type,title FROM wiki_nodes WHERE parent_id IS NULL ORDER BY sort_order, id").fetchall()
    result, secnum = [], 0
    for t in tops:
        d = {"id": t["id"], "type": t["type"], "title": t["title"]}
        if t["type"] == "section":
            secnum += 1
            d["number"] = secnum
            subs = conn.execute(
                "SELECT id,title FROM wiki_nodes WHERE parent_id=? ORDER BY sort_order, id", (t["id"],)).fetchall()
            d["subs"] = [{"id": s["id"], "title": s["title"], "number": f"{secnum}.{i + 1}"}
                         for i, s in enumerate(subs)]
        result.append(d)
    conn.close()
    return result


def get_wiki_node(nid: int) -> dict | None:
    conn = get_conn()
    r = conn.execute("SELECT * FROM wiki_nodes WHERE id=?", (nid,)).fetchone()
    conn.close()
    return dict(r) if r else None


def _wiki_subsections(section_id: int) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM wiki_nodes WHERE parent_id=? ORDER BY sort_order, id", (section_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _wiki_max_order(parent_id) -> float:
    conn = get_conn()
    if parent_id is None:
        r = conn.execute("SELECT MAX(sort_order) m FROM wiki_nodes WHERE parent_id IS NULL").fetchone()
    else:
        r = conn.execute("SELECT MAX(sort_order) m FROM wiki_nodes WHERE parent_id=?", (parent_id,)).fetchone()
    conn.close()
    return r["m"] or 0


def wiki_create_node(ntype: str, parent_id, title: str, body) -> int:
    conn = get_conn()
    order = _wiki_max_order(parent_id) + 1
    cur = conn.execute(
        "INSERT INTO wiki_nodes (type,parent_id,title,body,sort_order,updated_at) VALUES (?,?,?,?,?,?)",
        (ntype, parent_id, title, body, order, datetime.now(timezone.utc).isoformat()))
    nid = cur.lastrowid
    conn.commit(); conn.close()
    return nid


def wiki_update_node(nid: int, title: str, body) -> None:
    conn = get_conn()
    conn.execute("UPDATE wiki_nodes SET title=?, body=?, updated_at=? WHERE id=?",
                 (title, body, datetime.now(timezone.utc).isoformat(), nid))
    conn.commit(); conn.close()


def wiki_delete_node(nid: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM wiki_nodes WHERE id=?", (nid,))
    conn.commit(); conn.close()


def wiki_reorder(top_ids: list, subs_map: dict) -> None:
    conn = get_conn()
    for i, nid in enumerate(top_ids or []):
        conn.execute("UPDATE wiki_nodes SET sort_order=? WHERE id=? AND parent_id IS NULL", (i + 1, nid))
    for sec_id, sub_ids in (subs_map or {}).items():
        for i, sub_id in enumerate(sub_ids):
            conn.execute("UPDATE wiki_nodes SET sort_order=?, parent_id=? WHERE id=?", (i + 1, int(sec_id), sub_id))
    conn.commit(); conn.close()


def _wiki_snapshot(node: dict, change_kind: str, by_user_id) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO wiki_history (node_id,type,title,body,parent_id,change_kind,by_user_id,changed_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (node["id"], node["type"], node["title"], node.get("body"), node.get("parent_id"),
         change_kind, by_user_id, datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()


def create_proposal(author_user_id, kind, node_id, payload, summary, justification) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO wiki_proposals (author_user_id,kind,node_id,payload,summary,justification,status,created_at) "
        "VALUES (?,?,?,?,?,?, 'pending', ?)",
        (author_user_id, kind, node_id, json.dumps(payload or {}),
         (summary or "")[:300], (justification or "")[:3000], datetime.now(timezone.utc).isoformat()))
    pid = cur.lastrowid
    conn.commit(); conn.close()
    return pid


def _proposal_row(r) -> dict:
    d = dict(r)
    try:
        d["payload"] = json.loads(d.get("payload") or "{}")
    except Exception:  # noqa: BLE001
        d["payload"] = {}
    return d


def list_proposals(status: str | None = None) -> list:
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT p.*, u.username FROM wiki_proposals p LEFT JOIN users u ON u.id=p.author_user_id "
            "WHERE p.status=? ORDER BY p.created_at DESC", (status,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT p.*, u.username FROM wiki_proposals p LEFT JOIN users u ON u.id=p.author_user_id "
            "ORDER BY p.created_at DESC").fetchall()
    conn.close()
    return [_proposal_row(r) for r in rows]


def get_proposal(pid: int) -> dict | None:
    conn = get_conn()
    r = conn.execute(
        "SELECT p.*, u.username FROM wiki_proposals p LEFT JOIN users u ON u.id=p.author_user_id "
        "WHERE p.id=?", (pid,)).fetchone()
    conn.close()
    return _proposal_row(r) if r else None


def count_pending_proposals() -> int:
    conn = get_conn()
    c = conn.execute("SELECT COUNT(*) c FROM wiki_proposals WHERE status='pending'").fetchone()["c"]
    conn.close()
    return c


def set_proposal_status(pid: int, status: str, reviewer_id) -> None:
    conn = get_conn()
    conn.execute("UPDATE wiki_proposals SET status=?, reviewed_at=?, reviewer_user_id=? WHERE id=?",
                 (status, datetime.now(timezone.utc).isoformat(), reviewer_id, pid))
    conn.commit(); conn.close()


def apply_proposal(pid: int, reviewer_id) -> bool:
    p = get_proposal(pid)
    if not p or p["status"] != "pending":
        return False
    kind, payload = p["kind"], p["payload"]
    if kind == "edit":
        node = get_wiki_node(p["node_id"])
        if node:
            _wiki_snapshot(node, "edit", p["author_user_id"])
            wiki_update_node(p["node_id"], payload.get("title", node["title"]), payload.get("body", node.get("body")))
    elif kind == "create_section":
        wiki_create_node("section", None, payload.get("title", "Sección"), payload.get("body", ""))
    elif kind == "create_subsection":
        wiki_create_node("subsection", payload.get("parent_id"), payload.get("title", "Subsección"), payload.get("body", ""))
    elif kind == "create_separator":
        wiki_create_node("separator", None, payload.get("title", "Separador"), None)
    elif kind == "delete":
        node = get_wiki_node(p["node_id"])
        if node:
            for sub in _wiki_subsections(p["node_id"]):
                _wiki_snapshot(sub, "delete", p["author_user_id"]); wiki_delete_node(sub["id"])
            _wiki_snapshot(node, "delete", p["author_user_id"]); wiki_delete_node(p["node_id"])
    elif kind == "reorder":
        wiki_reorder(payload.get("top", []), payload.get("subs", {}))
    set_proposal_status(pid, "approved", reviewer_id)
    return True


def list_wiki_history(limit: int = 200) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT h.*, u.username, n.title AS current_title FROM wiki_history h "
        "LEFT JOIN users u ON u.id=h.by_user_id LEFT JOIN wiki_nodes n ON n.id=h.node_id "
        "ORDER BY h.changed_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_wiki_history_entry(hid: int) -> dict | None:
    conn = get_conn()
    r = conn.execute("SELECT * FROM wiki_history WHERE id=?", (hid,)).fetchone()
    conn.close()
    return dict(r) if r else None


def revert_wiki_version(hid: int, by_user_id) -> bool:
    h = get_wiki_history_entry(hid)
    if not h:
        return False
    node = get_wiki_node(h["node_id"])
    if node:
        _wiki_snapshot(node, "revert", by_user_id)
        wiki_update_node(h["node_id"], h["title"], h["body"])
    else:
        wiki_create_node(h["type"], h["parent_id"], h["title"], h["body"])
    return True


# --------------------------- Administración de usuarios ---------------------------

def list_users() -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, username, is_admin, country, created_at FROM users ORDER BY username").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_user(uid: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM user_players WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM user_custom_rankings WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM custom_rankings WHERE owner_user_id=?", (uid,))
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit(); conn.close()


def set_user_admin(uid: int, is_admin: bool) -> None:
    conn = get_conn()
    conn.execute("UPDATE users SET is_admin=? WHERE id=?", (1 if is_admin else 0, uid))
    conn.commit(); conn.close()


def follow_player(user_id: int, tag: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO user_players (user_id, player_tag, added_at) VALUES (?,?,?)",
        (user_id, normalize_tag(tag), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit(); conn.close()


def unfollow_player(user_id: int, tag: str) -> None:
    """Desvincula el jugador de este usuario. Si ya no lo sigue nadie, borra
    el jugador y sus datos (deja de sondearse)."""
    tag = normalize_tag(tag)
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM user_players WHERE user_id=? AND player_tag=?", (user_id, tag))
    remaining = cur.execute(
        "SELECT COUNT(*) FROM user_players WHERE player_tag=?", (tag,)).fetchone()[0]
    conn.commit(); conn.close()
    if remaining == 0:
        remove_player(tag)  # nadie lo sigue: limpieza completa


def user_follows(user_id: int, tag: str) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM user_players WHERE user_id=? AND player_tag=?",
        (user_id, normalize_tag(tag))).fetchone()
    conn.close()
    return bool(row)


def list_players_for_user(user_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT p.tag, p.name, p.added_at, p.last_polled, p.active, p.icon_id, p.club_name,
                  (SELECT COUNT(*) FROM battles b WHERE b.player_tag = p.tag) AS battles
           FROM players p JOIN user_players up ON up.player_tag = p.tag
           WHERE up.user_id = ? ORDER BY up.added_at""",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def link_orphan_players_to(user_id: int) -> None:
    """Vincula al usuario los jugadores que no sigue nadie (migración: jugadores
    añadidos antes de existir las cuentas no quedan huérfanos ni se pierden)."""
    conn = get_conn()
    orphans = conn.execute(
        """SELECT tag FROM players WHERE tag NOT IN (SELECT player_tag FROM user_players)"""
    ).fetchall()
    now = datetime.now(timezone.utc).isoformat()
    for r in orphans:
        conn.execute(
            "INSERT OR IGNORE INTO user_players (user_id, player_tag, added_at) VALUES (?,?,?)",
            (user_id, r[0], now))
    conn.commit(); conn.close()


def reassign_players_for_personal_account(personal_id: int, tester_id: int) -> None:
    """Migración única al crear la cuenta personal: todos los jugadores existentes
    (los de las pruebas, que son tuyos) pasan a tu cuenta, y la cuenta `tester`
    se deja sin jugadores."""
    conn = get_conn(); cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    tags = [r[0] for r in cur.execute("SELECT tag FROM players").fetchall()]
    for t in tags:
        cur.execute(
            "INSERT OR IGNORE INTO user_players (user_id, player_tag, added_at) VALUES (?,?,?)",
            (personal_id, t, now))
    cur.execute("DELETE FROM user_players WHERE user_id=?", (tester_id,))  # tester vacío
    conn.commit(); conn.close()


def consume_report_credit(user_id: int, monthly_limit: int) -> bool:
    """Cuota mensual de informes. Si cambió el mes, recarga a `monthly_limit`
    (sin acumular sobrantes). Devuelve True si quedaba crédito y lo descuenta."""
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    conn = get_conn(); cur = conn.cursor()
    row = cur.execute(
        "SELECT reports_remaining, quota_period FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        conn.close(); return False
    remaining, stored_period = row["reports_remaining"], row["quota_period"]
    if stored_period != period or remaining is None:
        remaining = monthly_limit  # nuevo mes: se rellena hasta el tope, sin acumular
    if remaining <= 0:
        cur.execute("UPDATE users SET reports_remaining=?, quota_period=? WHERE id=?",
                    (remaining, period, user_id))
        conn.commit(); conn.close()
        return False
    remaining -= 1
    cur.execute("UPDATE users SET reports_remaining=?, quota_period=? WHERE id=?",
                (remaining, period, user_id))
    conn.commit(); conn.close()
    return True


def battle_player_tag(battle_id: str) -> str | None:
    conn = get_conn()
    row = conn.execute("SELECT player_tag FROM battles WHERE id=?", (battle_id,)).fetchone()
    conn.close()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Parseo
# ---------------------------------------------------------------------------

def _derive_is_win(result, rank, mode):
    if result == "victory": return 1
    if result == "defeat": return 0
    if result == "draw": return None
    if rank is not None:
        return (1 if rank <= 2 else 0) if "duo" in (mode or "").lower() else (1 if rank <= 4 else 0)
    return None


# La API da dos modos: battle.mode (mecánica base) y event.mode (modo del evento).
# Brawl Hockey llega como 'brawlBall' en battle.mode pero como 'airHockey' en
# event.mode, así que se confundía con Balón Brawl. Corregimos solo ese caso; el
# resto se queda con battle.mode como siempre. 'brawlHockey' casa con el icono de
# Brawlify (scHash). Añade aquí futuros casos mal etiquetados si aparecen.
EVENT_MODE_FIX = {"airHockey": "brawlHockey"}


def canonical_mode(event_mode, battle_mode) -> str:
    return EVENT_MODE_FIX.get(event_mode, battle_mode or event_mode or "unknown")


def parse_battle(raw: dict, player_tag: str) -> dict | None:
    battle = raw.get("battle") or {}
    event = raw.get("event") or {}
    battle_time = raw.get("battleTime")
    id_mode = battle.get("mode") or event.get("mode") or "unknown"  # estable: no rehace ids antiguos
    mode = canonical_mode(event.get("mode"), battle.get("mode"))
    bmap = event.get("map") or "unknown"
    btype = battle.get("type")
    duration = battle.get("duration")
    trophy_change = battle.get("trophyChange")
    result = battle.get("result")
    rank = battle.get("rank")
    star_tag = (battle.get("starPlayer") or {}).get("tag")

    norm_me = normalize_tag(player_tag)
    my_brawler = my_trophies = None
    my_team_idx = None
    opponents, allies = [], []

    def brawler_of(p):
        b = p.get("brawler") or {}
        return b.get("name"), b.get("trophies")

    teams = battle.get("teams")
    players = battle.get("players")

    if teams:
        for ti, team in enumerate(teams):
            for p in team:
                if normalize_tag(p.get("tag", "")) == norm_me:
                    my_team_idx = ti
                    my_brawler, my_trophies = brawler_of(p)
        for ti, team in enumerate(teams):
            for p in team:
                name, tr = brawler_of(p)
                if normalize_tag(p.get("tag", "")) == norm_me:
                    continue
                (allies if my_team_idx is not None and ti == my_team_idx else opponents).append((name, tr))
    elif players:
        for p in players:
            name, tr = brawler_of(p)
            if normalize_tag(p.get("tag", "")) == norm_me:
                my_brawler, my_trophies = name, tr
            else:
                opponents.append((name, tr))

    is_star = 1 if (star_tag and normalize_tag(star_tag) == norm_me) else 0
    is_win = _derive_is_win(result, rank, mode)
    battle_id = hashlib.sha1(f"{norm_me}|{battle_time}|{id_mode}|{bmap}|{my_brawler}".encode()).hexdigest()

    return {
        "id": battle_id, "player_tag": norm_me, "battle_time": battle_time,
        "mode": mode, "map": bmap, "battle_type": btype, "result": result, "rank": rank,
        "is_win": is_win, "my_brawler": my_brawler, "my_trophies": my_trophies,
        "trophy_change": trophy_change, "duration": duration, "is_star_player": is_star,
        "opponents": [(n, t) for n, t in opponents if n],
        "allies": [(n, t) for n, t in allies if n],
    }


def ingest_battles(items: list[dict], player_tag: str) -> int:
    conn = get_conn(); cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    new_count = 0
    for raw in items or []:
        b = parse_battle(raw, player_tag)
        if not b:
            continue
        existing = cur.execute("SELECT my_trophies, mode FROM battles WHERE id=?", (b["id"],)).fetchone()
        has_trophy_data = b["my_trophies"] is not None or any(
            t is not None for _, t in (b["opponents"] + b["allies"]))

        if existing is None:
            cur.execute(
                """INSERT INTO battles
                   (id, player_tag, battle_time, mode, map, battle_type, result, rank, is_win,
                    my_brawler, my_trophies, trophy_change, duration, is_star_player, ingested_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (b["id"], b["player_tag"], b["battle_time"], b["mode"], b["map"], b["battle_type"],
                 b["result"], b["rank"], b["is_win"], b["my_brawler"], b["my_trophies"],
                 b["trophy_change"], b["duration"], b["is_star_player"], now),
            )
            new_count += 1
            _insert_participants(cur, b)
        else:
            # Auto-corrige el modo si cambió la normalización (p.ej. Hockey antes
            # guardado como brawlBall), sin tocar el id (así no se duplica).
            if existing["mode"] != b["mode"]:
                cur.execute("UPDATE battles SET mode=? WHERE id=?", (b["mode"], b["id"]))
            if existing["my_trophies"] is None and has_trophy_data:
                # Partida ya guardada antes de tener copas: las rellenamos ahora.
                cur.execute("UPDATE battles SET my_trophies=? WHERE id=?", (b["my_trophies"], b["id"]))
                cur.execute("DELETE FROM opponents WHERE battle_id=?", (b["id"],))
                cur.execute("DELETE FROM allies WHERE battle_id=?", (b["id"],))
                _insert_participants(cur, b)
    conn.commit(); conn.close()
    return new_count


def _insert_participants(cur, b):
    for n, t in b["opponents"]:
        cur.execute("INSERT INTO opponents (battle_id, brawler, trophies) VALUES (?,?,?)", (b["id"], n, t))
    for n, t in b["allies"]:
        cur.execute("INSERT INTO allies (battle_id, brawler, trophies) VALUES (?,?,?)", (b["id"], n, t))


# ---------------------------------------------------------------------------
# Stats manuales (opcionales) por partida
# ---------------------------------------------------------------------------

def set_manual_stats(battle_id: str, kills=None, deaths=None, damage=None, healing=None, notes=None) -> None:
    conn = get_conn()
    conn.execute(
        """INSERT INTO manual_stats (battle_id, kills, deaths, damage, healing, notes, updated_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(battle_id) DO UPDATE SET
             kills=excluded.kills, deaths=excluded.deaths, damage=excluded.damage,
             healing=excluded.healing, notes=excluded.notes, updated_at=excluded.updated_at""",
        (battle_id, kills, deaths, damage, healing, notes, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit(); conn.close()


# ---------------------------------------------------------------------------
# Historial de partidas
# ---------------------------------------------------------------------------

def list_battles(filters: dict, limit: int = 25, offset: int = 0) -> dict:
    where_sql, params = _build_filters(filters)
    conn = get_conn()
    total = conn.execute(f"SELECT COUNT(*) FROM battles {where_sql}", params).fetchone()[0]
    rows = conn.execute(
        f"""SELECT id, battle_time, mode, map, battle_type, result, rank, is_win,
                   my_brawler, my_trophies, trophy_change, duration, is_star_player
            FROM battles {where_sql}
            ORDER BY battle_time DESC LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ).fetchall()
    ids = [r["id"] for r in rows]
    opp, ally, man = {}, {}, {}
    if ids:
        ph = ",".join("?" * len(ids))
        for r in conn.execute(f"SELECT battle_id, brawler, trophies FROM opponents WHERE battle_id IN ({ph})", ids):
            opp.setdefault(r["battle_id"], []).append({"brawler": r["brawler"], "trophies": r["trophies"]})
        for r in conn.execute(f"SELECT battle_id, brawler, trophies FROM allies WHERE battle_id IN ({ph})", ids):
            ally.setdefault(r["battle_id"], []).append({"brawler": r["brawler"], "trophies": r["trophies"]})
        for r in conn.execute(f"SELECT * FROM manual_stats WHERE battle_id IN ({ph})", ids):
            man[r["battle_id"]] = {"kills": r["kills"], "deaths": r["deaths"], "damage": r["damage"],
                                   "healing": r["healing"], "notes": r["notes"]}
    conn.close()
    battles = []
    for r in rows:
        d = dict(r)
        d["opponents"] = opp.get(r["id"], [])
        d["allies"] = ally.get(r["id"], [])
        d["manual"] = man.get(r["id"])
        battles.append(d)
    return {"battles": battles, "total": total, "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------
# Estadísticas
# ---------------------------------------------------------------------------

def _winrate(wins, losses):
    d = wins + losses
    return round(100 * wins / d, 1) if d else None


def _star_rate(sp, el):
    sp, el = sp or 0, el or 0
    return round(100 * sp / el, 1) if el else None


def _role_in(filters: dict, col: str = "my_brawler"):
    """Cláusula para filtrar partidas por rol (primario o secundario del brawler
    usado): col IN (brawlers con ese rol). (None, []) si no hay filtro de rol."""
    role = filters.get("role")
    if not role:
        return None, []
    names = brawler_extra.brawlers_with_role(role)
    if not names:
        return "1=0", []
    return f"{col} IN ({','.join('?' * len(names))})", list(names)


def _build_filters(filters: dict):
    where, params = [], []
    if filters.get("player"):
        where.append("player_tag = ?"); params.append(normalize_tag(filters["player"]))
    if filters.get("mode"):
        where.append("mode = ?"); params.append(filters["mode"])
    if filters.get("map"):
        where.append("map = ?"); params.append(filters["map"])
    if filters.get("brawler"):
        where.append("my_brawler = ?"); params.append(filters["brawler"])
    rsql, rparams = _role_in(filters, "my_brawler")
    if rsql:
        where.append(rsql); params.extend(rparams)
    if filters.get("vs"):
        where.append("id IN (SELECT battle_id FROM opponents WHERE brawler = ?)"); params.append(filters["vs"])
    return (("WHERE " + " AND ".join(where)) if where else ""), params


def overview(filters: dict | None = None) -> dict:
    filters = filters or {}
    where_sql, params = _build_filters(filters)
    cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y%m%dT%H%M%S.000Z")
    conn = get_conn()
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN is_win=0 THEN 1 ELSE 0 END) AS losses,
               SUM(CASE WHEN is_win IS NULL THEN 1 ELSE 0 END) AS undecided,
               SUM(CASE WHEN is_star_player=1 THEN 1 ELSE 0 END) AS star_players,
               SUM(CASE WHEN result IS NOT NULL THEN 1 ELSE 0 END) AS star_eligible,
               MAX(battle_time) AS last_battle, MAX(ingested_at) AS last_update,
               SUM(COALESCE(trophy_change,0)) AS trophy_delta,
               SUM(CASE WHEN battle_time >= ? THEN COALESCE(trophy_change,0) ELSE 0 END) AS trophy_delta_7d,
               AVG(m.kills) AS avg_kills, AVG(m.deaths) AS avg_deaths,
               AVG(m.damage) AS avg_damage, AVG(m.healing) AS avg_healing,
               SUM(CASE WHEN m.battle_id IS NOT NULL THEN 1 ELSE 0 END) AS annotated
        FROM battles LEFT JOIN manual_stats m ON m.battle_id = battles.id {where_sql}
        """,
        [cutoff_7d] + params,
    ).fetchone()
    conn.close()
    wins, losses = row["wins"] or 0, row["losses"] or 0

    def rnd(x):
        return round(x, 1) if x is not None else None

    return {
        "total": row["total"] or 0, "wins": wins, "losses": losses, "undecided": row["undecided"] or 0,
        "winrate": _winrate(wins, losses),
        "star_rate": _star_rate(row["star_players"], row["star_eligible"]),
        "star_players": row["star_players"] or 0,
        "last_battle": row["last_battle"], "last_update": row["last_update"],
        "trophy_delta": row["trophy_delta"] or 0,
        "trophy_delta_7d": row["trophy_delta_7d"] or 0,
        "annotated": row["annotated"] or 0,
        "avg_kills": rnd(row["avg_kills"]), "avg_deaths": rnd(row["avg_deaths"]),
        "avg_damage": rnd(row["avg_damage"]), "avg_healing": rnd(row["avg_healing"]),
    }


def winrate_by(dimension: str, filters: dict | None = None) -> list[dict]:
    col = GROUP_COLUMNS.get(dimension)
    if not col:
        raise ValueError(f"Dimensión no válida: {dimension}")
    filters = filters or {}
    where_sql, params = _build_filters(filters)
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT {col} AS label,
               SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN is_win=0 THEN 1 ELSE 0 END) AS losses,
               SUM(CASE WHEN is_win IS NULL THEN 1 ELSE 0 END) AS undecided,
               SUM(CASE WHEN is_star_player=1 THEN 1 ELSE 0 END) AS star_players,
               SUM(CASE WHEN result IS NOT NULL THEN 1 ELSE 0 END) AS star_eligible,
               COUNT(*) AS total, SUM(COALESCE(trophy_change,0)) AS trophy_delta
        FROM battles {where_sql}
        GROUP BY {col} ORDER BY total DESC
        """,
        params,
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        if r["label"] is None:
            continue
        out.append({"label": r["label"], "wins": r["wins"], "losses": r["losses"],
                    "undecided": r["undecided"], "total": r["total"],
                    "winrate": _winrate(r["wins"], r["losses"]),
                    "star_rate": _star_rate(r["star_players"], r["star_eligible"]),
                    "trophy_delta": r["trophy_delta"]})
    return out


def winrate_by_role(filters: dict | None = None) -> list[dict]:
    """Win rate agregado por ROL. Cada brawler aporta sus partidas a su rol primario
    Y secundario (p. ej. un brawler [Control, Lanzador] suma a ambos roles), igual que
    el filtro por rol. Devuelve además 'usage_pct' = peso de cada rol sobre el total."""
    rows = winrate_by("brawler", filters)
    agg: dict = {}
    for b in rows:
        for role in brawler_extra.roles_of(b["label"]):
            a = agg.setdefault(role, {"wins": 0, "losses": 0, "undecided": 0,
                                      "total": 0, "trophy_delta": 0})
            a["wins"] += b["wins"] or 0
            a["losses"] += b["losses"] or 0
            a["undecided"] += b.get("undecided") or 0
            a["total"] += b["total"] or 0
            a["trophy_delta"] += b.get("trophy_delta") or 0
    grand = sum(a["total"] for a in agg.values()) or 1  # cada partida cuenta 1 vez por rol
    out = [{"label": role, **a, "winrate": _winrate(a["wins"], a["losses"]),
            "usage_pct": round(100 * a["total"] / grand, 1)} for role, a in agg.items()]
    out.sort(key=lambda r: r["total"], reverse=True)
    return out


def winrate_vs(filters: dict | None = None) -> list[dict]:
    filters = filters or {}
    where, params = [], []
    if filters.get("player"):
        where.append("b.player_tag = ?"); params.append(normalize_tag(filters["player"]))
    if filters.get("mode"):
        where.append("b.mode = ?"); params.append(filters["mode"])
    if filters.get("map"):
        where.append("b.map = ?"); params.append(filters["map"])
    if filters.get("brawler"):
        where.append("b.my_brawler = ?"); params.append(filters["brawler"])
    rsql, rparams = _role_in(filters, "b.my_brawler")
    if rsql:
        where.append(rsql); params.extend(rparams)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT o.brawler AS label,
               SUM(CASE WHEN b.is_win=1 THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN b.is_win=0 THEN 1 ELSE 0 END) AS losses,
               COUNT(*) AS total,
               AVG(o.trophies) AS avg_enemy_trophies,
               AVG(b.my_trophies) AS avg_my_trophies
        FROM opponents o JOIN battles b ON b.id = o.battle_id
        {where_sql}
        GROUP BY o.brawler ORDER BY total DESC
        """,
        params,
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        if r["label"] is None:
            continue
        et = round(r["avg_enemy_trophies"]) if r["avg_enemy_trophies"] is not None else None
        mt = round(r["avg_my_trophies"]) if r["avg_my_trophies"] is not None else None
        out.append({"label": r["label"], "wins": r["wins"], "losses": r["losses"], "total": r["total"],
                    "winrate": _winrate(r["wins"], r["losses"]),
                    "avg_enemy_trophies": et, "avg_my_trophies": mt,
                    "trophy_delta": (et - mt) if (et is not None and mt is not None) else None})
    return out


def community_meta(mode: str | None = None, map_: str | None = None) -> dict:
    """Meta comunitario (BrawlSensei): uso y win rate por brawler agregando TODAS
    las partidas de TODOS los jugadores seguidos —un tier list propio, no el de
    otras webs—, opcionalmente filtrado por modo/mapa. Devuelve el agregado del
    modo (total + win rate medio) y la lista por brawler con pick rate."""
    where = ["my_brawler IS NOT NULL"]
    params: list = []
    if mode:
        where.append("mode = ?"); params.append(mode)
    if map_:
        where.append("map = ?"); params.append(map_)
    where_sql = "WHERE " + " AND ".join(where)
    conn = get_conn()
    rows = conn.execute(
        f"""SELECT my_brawler AS brawler, COUNT(*) AS games,
                   SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN is_win=0 THEN 1 ELSE 0 END) AS losses
            FROM battles {where_sql}
            GROUP BY my_brawler ORDER BY games DESC""",
        params,
    ).fetchall()
    conn.close()
    total = sum(r["games"] for r in rows)
    tw = sum(r["wins"] or 0 for r in rows)
    tl = sum(r["losses"] or 0 for r in rows)
    brawlers = [{"brawler": r["brawler"], "games": r["games"],
                 "pick_rate": round(100 * r["games"] / total, 1) if total else 0.0,
                 "winrate": _winrate(r["wins"], r["losses"]),
                 "wins": r["wins"], "losses": r["losses"]} for r in rows]
    return {"total": total, "winrate": _winrate(tw, tl), "brawlers": brawlers}


def distinct_values(player: str | None = None) -> dict:
    conn = get_conn()

    def col_distinct(col):
        if player:
            q = f"SELECT DISTINCT {col} FROM battles WHERE player_tag = ? AND {col} IS NOT NULL ORDER BY {col}"
            return [r[0] for r in conn.execute(q, (normalize_tag(player),))]
        return [r[0] for r in conn.execute(f"SELECT DISTINCT {col} FROM battles WHERE {col} IS NOT NULL ORDER BY {col}")]

    brawlers = col_distinct("my_brawler")
    roles = sorted({r for b in brawlers for r in brawler_extra.roles_of(b)})
    out = {"modes": col_distinct("mode"), "maps": col_distinct("map"),
           "brawlers": brawlers, "roles": roles}
    conn.close()
    return out


# ---------------------------------------------------------------------------
# Analítica para el Informe (cálculos derivados)
# ---------------------------------------------------------------------------

def trophy_series(filters: dict | None = None) -> list[dict]:
    """Serie acumulada de trofeos en orden cronológico (para la gráfica)."""
    filters = filters or {}
    where_sql, params = _build_filters(filters)
    conn = get_conn()
    rows = conn.execute(
        f"""SELECT battle_time, COALESCE(trophy_change,0) AS ch, my_brawler, mode, map
            FROM battles {where_sql} ORDER BY battle_time ASC""",
        params,
    ).fetchall()
    conn.close()
    cum, out = 0, []
    for i, r in enumerate(rows):
        cum += r["ch"]
        out.append({"i": i, "time": r["battle_time"], "change": r["ch"], "cumulative": cum})
    return out


def winrate_with_allies(filters: dict | None = None) -> list[dict]:
    """Win rate según el brawler aliado que te acompaña (datos cruzados de equipo)."""
    filters = filters or {}
    where, params = [], []
    if filters.get("player"):
        where.append("b.player_tag = ?"); params.append(normalize_tag(filters["player"]))
    if filters.get("mode"):
        where.append("b.mode = ?"); params.append(filters["mode"])
    if filters.get("map"):
        where.append("b.map = ?"); params.append(filters["map"])
    if filters.get("brawler"):
        where.append("b.my_brawler = ?"); params.append(filters["brawler"])
    rsql, rparams = _role_in(filters, "b.my_brawler")
    if rsql:
        where.append(rsql); params.extend(rparams)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    conn = get_conn()
    rows = conn.execute(
        f"""SELECT a.brawler AS label,
                   SUM(CASE WHEN b.is_win=1 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN b.is_win=0 THEN 1 ELSE 0 END) AS losses,
                   COUNT(*) AS total
            FROM allies a JOIN battles b ON b.id = a.battle_id
            {where_sql} GROUP BY a.brawler ORDER BY total DESC""",
        params,
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        if r["label"] is None:
            continue
        out.append({"label": r["label"], "wins": r["wins"], "losses": r["losses"],
                    "total": r["total"], "winrate": _winrate(r["wins"], r["losses"])})
    return out


def crosstab(filters: dict | None = None, top_brawlers: int = 8) -> dict:
    """Tabla cruzada brawler x modo con win rate (para el mapa de calor)."""
    filters = filters or {}
    where_sql, params = _build_filters(filters)
    conn = get_conn()
    rows = conn.execute(
        f"""SELECT my_brawler AS brawler, mode,
                   SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN is_win=0 THEN 1 ELSE 0 END) AS losses,
                   COUNT(*) AS total
            FROM battles {where_sql} GROUP BY my_brawler, mode""",
        params,
    ).fetchall()
    conn.close()
    btot, mset, cells = {}, {}, {}
    for r in rows:
        if not r["brawler"] or not r["mode"]:
            continue
        btot[r["brawler"]] = btot.get(r["brawler"], 0) + r["total"]
        mset[r["mode"]] = mset.get(r["mode"], 0) + r["total"]
        cells[f"{r['brawler']}|{r['mode']}"] = {"winrate": _winrate(r["wins"], r["losses"]), "total": r["total"]}
    brawlers = [b for b, _ in sorted(btot.items(), key=lambda kv: -kv[1])][:top_brawlers]
    modes = [m for m, _ in sorted(mset.items(), key=lambda kv: -kv[1])]
    return {"brawlers": brawlers, "modes": modes, "cells": cells}


def _pick(rows, key, reverse, min_total=3):
    elig = [r for r in rows if r.get("winrate") is not None and r["total"] >= min_total]
    if not elig:
        return None
    return sorted(elig, key=lambda r: r[key], reverse=reverse)[0]


def _iso(t: str) -> str:
    """Convierte el formato compacto de la API (20240101T120000.000Z) a ISO 8601."""
    if not t or len(t) < 15 or t[8] != "T":
        return t
    return f"{t[0:4]}-{t[4:6]}-{t[6:8]}T{t[9:11]}:{t[11:13]}:{t[13:15]}Z"


def current_streak(filters: dict | None = None) -> dict:
    """Racha actual de victorias o derrotas seguidas (partidas decididas más recientes)."""
    filters = filters or {}
    where_sql, params = _build_filters(filters)
    conn = get_conn()
    rows = conn.execute(f"SELECT is_win FROM battles {where_sql} ORDER BY battle_time DESC", params).fetchall()
    conn.close()
    stype, count = None, 0
    for r in rows:
        w = r["is_win"]
        if w is None:
            if count == 0:
                continue   # ignoramos empates al principio
            break           # un empate corta la racha
        if count == 0:
            stype, count = w, 1
        elif w == stype:
            count += 1
        else:
            break
    return {"type": "win" if stype == 1 else ("loss" if stype == 0 else None), "count": count}


def trophy_diff_performance(filters: dict | None = None) -> list[dict]:
    """Win rate según la diferencia de copas con el rival (tus copas - media del rival)."""
    filters = filters or {}
    where_sql, params = _build_filters(filters)
    conn = get_conn()
    rows = conn.execute(
        f"""SELECT b.is_win, b.my_trophies, AVG(o.trophies) AS enemy_avg
            FROM battles b LEFT JOIN opponents o ON o.battle_id = b.id
            {where_sql} GROUP BY b.id""",
        params,
    ).fetchall()
    conn.close()
    buckets = [
        {"label": "Rival mucho más fuerte (+150🏆)", "lo": None, "hi": -150, "w": 0, "t": 0},
        {"label": "Rival algo más fuerte", "lo": -150, "hi": -50, "w": 0, "t": 0},
        {"label": "Nivel parejo (±50🏆)", "lo": -50, "hi": 50, "w": 0, "t": 0},
        {"label": "Rival algo más débil", "lo": 50, "hi": 150, "w": 0, "t": 0},
        {"label": "Rival mucho más débil (-150🏆)", "lo": 150, "hi": None, "w": 0, "t": 0},
    ]
    for r in rows:
        if r["is_win"] is None or r["my_trophies"] is None or r["enemy_avg"] is None:
            continue
        diff = r["my_trophies"] - r["enemy_avg"]
        for b in buckets:
            if (b["lo"] is None or diff >= b["lo"]) and (b["hi"] is None or diff < b["hi"]):
                b["t"] += 1
                if r["is_win"] == 1:
                    b["w"] += 1
                break
    return [{"label": b["label"], "wins": b["w"], "losses": b["t"] - b["w"],
             "total": b["t"], "winrate": _winrate(b["w"], b["t"] - b["w"])} for b in buckets]


def winrate_evolution(filters: dict | None = None, window: int = 10) -> list[dict]:
    """Win rate en ventana móvil a lo largo de las partidas (forma reciente)."""
    filters = filters or {}
    where_sql, params = _build_filters(filters)
    conn = get_conn()
    rows = conn.execute(f"SELECT is_win FROM battles {where_sql} ORDER BY battle_time ASC", params).fetchall()
    conn.close()
    decisive = [r["is_win"] for r in rows if r["is_win"] is not None]
    out = []
    for i in range(len(decisive)):
        win = decisive[max(0, i - window + 1):i + 1]
        out.append({"i": i, "winrate": round(100 * sum(win) / len(win), 1)})
    return out


def battle_points(filters: dict | None = None) -> list[dict]:
    """Partidas en formato ligero (hora ISO + resultado) para la franja horaria local."""
    filters = filters or {}
    where_sql, params = _build_filters(filters)
    conn = get_conn()
    rows = conn.execute(f"SELECT battle_time, is_win FROM battles {where_sql} ORDER BY battle_time ASC", params).fetchall()
    conn.close()
    return [{"time": _iso(r["battle_time"]), "is_win": r["is_win"]} for r in rows]


def report_analytics(filters: dict | None = None) -> dict:
    """Reúne todos los cálculos del Informe en un solo objeto."""
    filters = filters or {}
    ov = overview(filters)
    by_brawler = winrate_by("brawler", filters)
    by_mode = winrate_by("mode", filters)
    by_map = winrate_by("map", filters)
    vs = winrate_vs(filters)
    allies = winrate_with_allies(filters)

    most_played = max(by_brawler, key=lambda r: r["total"]) if by_brawler else None
    highlights = {
        "most_played": most_played,
        "best_brawler": _pick(by_brawler, "winrate", True),
        "worst_brawler": _pick(by_brawler, "winrate", False),
        "best_mode": _pick(by_mode, "winrate", True, min_total=2),
        "worst_mode": _pick(by_mode, "winrate", False, min_total=2),
        "best_map": _pick(by_map, "winrate", True),
        "worst_map": _pick(by_map, "winrate", False),
        "hardest_vs": _pick(vs, "winrate", False),
        "easiest_vs": _pick(vs, "winrate", True),
        "best_ally": _pick(allies, "winrate", True, min_total=2),
    }
    return {
        "overview": ov, "highlights": highlights,
        "by_brawler": by_brawler, "by_mode": by_mode, "by_map": by_map,
        "vs": vs, "allies": allies,
        "trophy_series": trophy_series(filters), "crosstab": crosstab(filters),
        "streak": current_streak(filters),
        "trophy_diff": trophy_diff_performance(filters),
        "winrate_evolution": winrate_evolution(filters),
        "battle_points": battle_points(filters),
    }


# ---------------------------------------------------------------------------
# "Qué jugar ahora": cruza la rotación de eventos con tu historial
# ---------------------------------------------------------------------------

def rotation_analysis(player: str, events: list[dict], min_games: int = 1,
                      brawler_limit: int = 3) -> list[dict]:
    """Para cada evento en rotación (modo + mapa) calcula tu win rate y, de tus
    brawlers en ese mapa, propone los mejores. La 'eficiencia' usa win rate con
    suavizado (wins+1)/(games+2) para que 1-2 partidas no inflen el ranking, y se
    desempata por nº de partidas y por cambio medio de trofeos."""
    tag = normalize_tag(player)
    conn = get_conn()
    out = []
    for ev in events:
        mode, map_ = ev.get("mode"), ev.get("map")
        # Stats del jugador en ese mapa+modo (case-insensitive por seguridad).
        row = conn.execute(
            """SELECT COUNT(*) AS games,
                      SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END) AS wins,
                      SUM(CASE WHEN is_win=0 THEN 1 ELSE 0 END) AS losses,
                      AVG(trophy_change) AS avg_tc,
                      SUM(CASE WHEN is_star_player=1 THEN 1 ELSE 0 END) AS stars,
                      SUM(CASE WHEN result IS NOT NULL THEN 1 ELSE 0 END) AS star_elig
               FROM battles
               WHERE player_tag=? AND LOWER(map)=LOWER(?)
                 AND (? IS NULL OR LOWER(mode)=LOWER(?))""",
            (tag, map_, mode, mode),
        ).fetchone()
        wins, losses = row["wins"] or 0, row["losses"] or 0
        # Mejores brawlers en ese mapa.
        brows = conn.execute(
            """SELECT my_brawler AS brawler, COUNT(*) AS games,
                      SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END) AS wins,
                      SUM(CASE WHEN is_win=0 THEN 1 ELSE 0 END) AS losses,
                      AVG(trophy_change) AS avg_tc
               FROM battles
               WHERE player_tag=? AND LOWER(map)=LOWER(?)
                 AND (? IS NULL OR LOWER(mode)=LOWER(?)) AND my_brawler IS NOT NULL
               GROUP BY my_brawler""",
            (tag, map_, mode, mode),
        ).fetchall()
        brawlers = []
        for b in brows:
            bw, bl = b["wins"] or 0, b["losses"] or 0
            decided = bw + bl
            if b["games"] < min_games:
                continue
            score = (bw + 1) / (decided + 2) if decided else 0.0  # suavizado
            brawlers.append({
                "brawler": b["brawler"], "games": b["games"],
                "winrate": _winrate(bw, bl),
                "avg_trophy": round(b["avg_tc"], 1) if b["avg_tc"] is not None else None,
                "_score": score,
            })
        brawlers.sort(key=lambda x: (x["_score"], x["games"], x["avg_trophy"] or 0), reverse=True)
        for b in brawlers:
            b.pop("_score", None)
        out.append({
            "mode": mode, "map": map_,
            "category": ev.get("category"),
            "start_time": ev.get("startTime"), "end_time": ev.get("endTime"),
            "games": row["games"] or 0, "wins": wins, "losses": losses,
            "winrate": _winrate(wins, losses),
            "avg_trophy": round(row["avg_tc"], 1) if row["avg_tc"] is not None else None,
            "star_rate": _star_rate(row["stars"], row["star_elig"]),
            "best_brawlers": brawlers[:brawler_limit],
        })
    conn.close()
    # Ordena: primero los eventos donde tienes mejor win rate (con datos), luego sin datos.
    out.sort(key=lambda e: (e["games"] > 0, e["winrate"] if e["winrate"] is not None else -1,
                            e["games"]), reverse=True)
    return out


# ---------------------------------------------------------------------------
# Informes guardados (análisis de Claude persistidos)
# ---------------------------------------------------------------------------

def create_report(player_tag: str, filters_json: str, scope_label: str) -> int:
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """INSERT INTO reports (player_tag, name, filters_json, scope_label, status, created_at)
           VALUES (?,?,?,?, 'generating', ?)""",
        (normalize_tag(player_tag), None, filters_json, scope_label,
         datetime.now(timezone.utc).isoformat()),
    )
    rid = cur.lastrowid
    conn.commit(); conn.close()
    return rid


def set_report_result(report_id: int, name: str, content: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE reports SET name=?, content=?, status='ready', completed_at=? WHERE id=?",
        (name, content, datetime.now(timezone.utc).isoformat(), report_id),
    )
    conn.commit(); conn.close()


def set_report_error(report_id: int, error: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE reports SET error=?, status='error', completed_at=? WHERE id=?",
        (error, datetime.now(timezone.utc).isoformat(), report_id),
    )
    conn.commit(); conn.close()


def has_generating_report(player_tag: str) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM reports WHERE player_tag=? AND status='generating' LIMIT 1",
        (normalize_tag(player_tag),),
    ).fetchone()
    conn.close()
    return bool(row)


def list_reports(player_tag: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT id, name, scope_label, status, error, created_at, completed_at
           FROM reports WHERE player_tag=? ORDER BY created_at ASC""",
        (normalize_tag(player_tag),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_report(report_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ============================ EVENTOS (LIGAS Y TORNEOS) ============================

_EVENT_KINDS = {"league", "tournament"}
_EVENT_MODES = {"individual", "teams"}
_EVENT_VIS = {"public", "acceptance", "private"}
_MATCH_TYPES = {"bo1", "bo3", "bo5"}
_EVENT_FORMATS = {"swiss", "mcmahon", "roundrobin", "single_elim", "double_elim", "free"}


def _event_row(r) -> dict:
    d = dict(r)
    try:
        d["settings"] = json.loads(d.get("settings") or "{}")
    except Exception:
        d["settings"] = {}
    d["has_password"] = bool(d.pop("password_hash", None))
    return d


def _ev_counts(conn, eid):
    p = conn.execute("SELECT COUNT(*) FROM event_participants WHERE event_id=?", (eid,)).fetchone()[0]
    f = conn.execute("SELECT COUNT(*) FROM event_follows WHERE event_id=?", (eid,)).fetchone()[0]
    return p, f


def create_event(owner_user_id, name, kind, mode, visibility, language=None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO events (owner_user_id, name, kind, mode, visibility, language,
              max_participants, match_type, settings, require_confirmation, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (owner_user_id, (name or "").strip(), kind, mode, visibility, language,
         12, "bo1", "{}", 1, "open", now, now))
    conn.commit()
    eid = cur.lastrowid
    conn.close()
    return eid


def get_event(eid) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    conn.close()
    return _event_row(row) if row else None


def get_event_password_hash(eid) -> str | None:
    conn = get_conn()
    row = conn.execute("SELECT password_hash FROM events WHERE id=?", (eid,)).fetchone()
    conn.close()
    return (row["password_hash"] if row else None)


def event_owner(eid) -> int | None:
    conn = get_conn()
    row = conn.execute("SELECT owner_user_id FROM events WHERE id=?", (eid,)).fetchone()
    conn.close()
    return (row["owner_user_id"] if row else None)


def event_counts(eid) -> dict:
    conn = get_conn()
    p, f = _ev_counts(conn, eid)
    conn.close()
    return {"participants": p, "followers": f}


def update_event(eid, fields: dict) -> None:
    cols = ["name", "kind", "mode", "visibility", "language", "max_participants",
            "format", "match_type", "date_start", "date_end", "description",
            "poster_url", "password_hash", "require_confirmation", "hidden", "status"]
    sets, vals = [], []
    for c in cols:
        if c in fields:
            sets.append(f"{c}=?"); vals.append(fields[c])
    if "settings" in fields:
        sets.append("settings=?")
        vals.append(fields["settings"] if isinstance(fields["settings"], str) else json.dumps(fields["settings"]))
    if not sets:
        return
    sets.append("updated_at=?"); vals.append(datetime.now(timezone.utc).isoformat())
    vals.append(eid)
    conn = get_conn()
    conn.execute(f"UPDATE events SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit(); conn.close()


def delete_event(eid) -> None:
    conn = get_conn()
    for t in ("event_participants", "event_teams", "event_follows", "event_requests", "event_matches"):
        conn.execute(f"DELETE FROM {t} WHERE event_id=?", (eid,))
    conn.execute("DELETE FROM events WHERE id=?", (eid,))
    conn.commit(); conn.close()


# --- seguir ---
def follow_event(eid, user_id) -> None:
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO event_follows (event_id, user_id, followed_at) VALUES (?,?,?)",
                 (eid, user_id, datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()


def unfollow_event(eid, user_id) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM event_follows WHERE event_id=? AND user_id=?", (eid, user_id))
    conn.commit(); conn.close()


def is_following_event(eid, user_id) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM event_follows WHERE event_id=? AND user_id=?", (eid, user_id)).fetchone()
    conn.close()
    return bool(row)


# --- destinatarios para notificaciones ---
def event_follower_ids(eid) -> list:
    conn = get_conn()
    rows = conn.execute("SELECT user_id FROM event_follows WHERE event_id=?", (eid,)).fetchall()
    conn.close()
    return [r[0] for r in rows]


def event_participant_user_ids(eid) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT user_id FROM event_participants WHERE event_id=? AND user_id IS NOT NULL", (eid,)).fetchall()
    conn.close()
    return [r[0] for r in rows]


def users_following_player(tag) -> list:
    conn = get_conn()
    rows = conn.execute("SELECT user_id FROM user_players WHERE player_tag=?", (normalize_tag(tag),)).fetchall()
    conn.close()
    return [r[0] for r in rows]


# --- notificaciones ---
def create_notification(user_id, ntype, title, body="", event_id=None, data=None) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO notifications (user_id, type, title, body, event_id, data, read, created_at)
           VALUES (?,?,?,?,?,?,0,?)""",
        (user_id, ntype, title, body, event_id,
         json.dumps(data) if data is not None else None,
         datetime.now(timezone.utc).isoformat()))
    conn.commit(); nid = cur.lastrowid; conn.close()
    return nid


def notify_many(user_ids, ntype, title, body="", event_id=None, data=None, exclude=None) -> int:
    """Crea la MISMA notificación para varios usuarios (deduplicados). Devuelve cuántas."""
    seen, n = set(), 0
    ex = set(exclude or [])
    for uid in user_ids:
        if uid is None or uid in seen or uid in ex:
            continue
        seen.add(uid)
        create_notification(uid, ntype, title, body, event_id, data)
        n += 1
    return n


def list_notifications(user_id, limit=60) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
        (user_id, limit)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("data"):
            try: d["data"] = json.loads(d["data"])
            except Exception: d["data"] = None
        d["read"] = bool(d.get("read"))
        out.append(d)
    return out


def count_unread_notifications(user_id) -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read=0", (user_id,)).fetchone()[0]
    conn.close()
    return n


def mark_notification_read(user_id, nid) -> None:
    conn = get_conn()
    conn.execute("UPDATE notifications SET read=1 WHERE id=? AND user_id=?", (nid, user_id))
    conn.commit(); conn.close()


def mark_all_notifications_read(user_id) -> int:
    conn = get_conn()
    cur = conn.execute("UPDATE notifications SET read=1 WHERE user_id=? AND read=0", (user_id,))
    conn.commit(); n = cur.rowcount; conn.close()
    return n


def delete_notification(user_id, nid) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM notifications WHERE id=? AND user_id=?", (nid, user_id))
    conn.commit(); conn.close()


def delete_all_notifications(user_id) -> int:
    conn = get_conn()
    cur = conn.execute("DELETE FROM notifications WHERE user_id=?", (user_id,))
    conn.commit(); n = cur.rowcount; conn.close()
    return n


def get_player_name(tag) -> str | None:
    conn = get_conn()
    row = conn.execute("SELECT name FROM players WHERE tag=?", (normalize_tag(tag),)).fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def event_ids_with_start() -> list:
    """Eventos no finalizados que tienen fecha de inicio (para avisos de cercanía/inicio)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM events WHERE date_start IS NOT NULL AND date_start != '' AND status != 'finished'").fetchall()
    conn.close()
    return [r[0] for r in rows]


# --- participantes ---
def list_participants(eid) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT ep.id, ep.user_id, ep.player_tag, ep.team_id, ep.added_by_owner, ep.joined_at, ep.seed_cups,
                  p.name AS player_name, p.icon_id, t.name AS team_name, t.logo_url AS team_logo
           FROM event_participants ep
           LEFT JOIN players p ON p.tag = ep.player_tag
           LEFT JOIN event_teams t ON t.id = ep.team_id
           WHERE ep.event_id=? ORDER BY ep.joined_at""", (eid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def participant_count(eid) -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM event_participants WHERE event_id=?", (eid,)).fetchone()[0]
    conn.close()
    return n


def tag_in_event(eid, tag) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM event_participants WHERE event_id=? AND player_tag=?",
                       (eid, normalize_tag(tag))).fetchone()
    conn.close()
    return bool(row)


def add_participant(eid, user_id, tag, team_id=None, added_by_owner=0) -> int | None:
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO event_participants (event_id, user_id, player_tag, team_id, added_by_owner, joined_at)
               VALUES (?,?,?,?,?,?)""",
            (eid, user_id, normalize_tag(tag), team_id, added_by_owner,
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
        pid = cur.lastrowid
    except sqlite3.IntegrityError:
        pid = None
    conn.close()
    return pid


def set_participant_seed_cups(eid, tag, cups) -> None:
    conn = get_conn()
    conn.execute("UPDATE event_participants SET seed_cups=? WHERE event_id=? AND player_tag=?",
                 (cups, eid, normalize_tag(tag)))
    conn.commit(); conn.close()


def remove_participant(eid, participant_id) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM event_participants WHERE id=? AND event_id=?", (participant_id, eid))
    conn.commit(); conn.close()


# --- solicitudes de inscripción ---
def create_request(eid, user_id, tag, team_name=None, message=None) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO event_requests (event_id, user_id, player_tag, team_name, message, status, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (eid, user_id, normalize_tag(tag), team_name, message, "pending",
         datetime.now(timezone.utc).isoformat()))
    conn.commit(); rid = cur.lastrowid; conn.close()
    return rid


def list_requests(eid, status="pending") -> list[dict]:
    conn = get_conn()
    q = """SELECT er.*, u.username, p.name AS player_name
           FROM event_requests er LEFT JOIN users u ON u.id = er.user_id
           LEFT JOIN players p ON p.tag = er.player_tag WHERE er.event_id=?"""
    args = [eid]
    if status:
        q += " AND er.status=?"; args.append(status)
    q += " ORDER BY er.created_at"
    rows = conn.execute(q, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def pending_request_count(eid) -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM event_requests WHERE event_id=? AND status='pending'",
                     (eid,)).fetchone()[0]
    conn.close()
    return n


def get_request(rid) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM event_requests WHERE id=?", (rid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def user_pending_request(eid, user_id) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        """SELECT * FROM event_requests WHERE event_id=? AND user_id=? AND status='pending'
           ORDER BY created_at DESC LIMIT 1""", (eid, user_id)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_request_status(rid, status) -> None:
    conn = get_conn()
    conn.execute("UPDATE event_requests SET status=? WHERE id=?", (status, rid))
    conn.commit(); conn.close()


# --- equipos ---
def list_teams(eid) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM event_teams WHERE event_id=? ORDER BY created_at", (eid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_team(eid, name, logo_url=None, captain_user_id=None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO event_teams (event_id, name, logo_url, captain_user_id, created_at) VALUES (?,?,?,?,?)",
        (eid, (name or "").strip(), logo_url, captain_user_id, datetime.now(timezone.utc).isoformat()))
    conn.commit(); tid = cur.lastrowid; conn.close()
    return tid


def update_team(eid, tid, name=None, logo_url=None) -> None:
    conn = get_conn()
    conn.execute("UPDATE event_teams SET name=COALESCE(?,name), logo_url=COALESCE(?,logo_url) WHERE id=? AND event_id=?",
                 (name, logo_url, tid, eid))
    conn.commit(); conn.close()


def delete_team(eid, tid) -> None:
    conn = get_conn()
    conn.execute("UPDATE event_participants SET team_id=NULL WHERE event_id=? AND team_id=?", (eid, tid))
    conn.execute("DELETE FROM event_teams WHERE id=? AND event_id=?", (tid, eid))
    conn.commit(); conn.close()


def set_participant_team(eid, pid, team_id) -> None:
    conn = get_conn()
    conn.execute("UPDATE event_participants SET team_id=? WHERE id=? AND event_id=?", (team_id, pid, eid))
    conn.commit(); conn.close()


# --- listados ---
def list_my_events(user_id) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT DISTINCT e.* FROM events e
           WHERE e.owner_user_id=?
              OR e.id IN (SELECT event_id FROM event_participants WHERE user_id=?)
              OR e.id IN (SELECT event_id FROM event_follows WHERE user_id=?)
           ORDER BY e.updated_at DESC""",
        (user_id, user_id, user_id)).fetchall()
    out = []
    for r in rows:
        d = _event_row(r)
        p, f = _ev_counts(conn, d["id"])
        d["participants"] = p; d["followers"] = f
        if d["owner_user_id"] == user_id:
            d["relation"] = "owner"
            d["pending"] = conn.execute(
                "SELECT COUNT(*) FROM event_requests WHERE event_id=? AND status='pending'",
                (d["id"],)).fetchone()[0]
        elif conn.execute("SELECT 1 FROM event_participants WHERE event_id=? AND user_id=?",
                          (d["id"], user_id)).fetchone():
            d["relation"] = "participant"
        else:
            d["relation"] = "follower"
        out.append(d)
    conn.close()
    return out


def list_board_events(user_id, types=None, langs=None, acceptance=None) -> list[dict]:
    conn = get_conn()
    acc = set(acceptance) if acceptance else {"public", "acceptance"}
    vis_clauses, args = [], []
    if "public" in acc:
        vis_clauses.append("e.visibility='public'")
    if "acceptance" in acc:
        vis_clauses.append("e.visibility='acceptance'")
    if "private" in acc:
        # Los privados se listan para que cualquiera pueda seguirlos y decidir si
        # le interesan; solo el apuntarse está protegido (contraseña/validación).
        # Si el dueño los marca como ocultos (hidden=1), no aparecen en el tablón:
        # solo se accede a ellos con el enlace directo.
        vis_clauses.append("(e.visibility='private' AND e.hidden=0)")
    if not vis_clauses:
        conn.close(); return []
    q = "SELECT e.* FROM events e WHERE (" + " OR ".join(vis_clauses) + ")"
    if types:
        q += " AND e.kind IN (" + ",".join("?" * len(types)) + ")"; args += list(types)
    if langs:
        q += " AND e.language IN (" + ",".join("?" * len(langs)) + ")"; args += list(langs)
    q += " ORDER BY e.created_at DESC"
    rows = conn.execute(q, args).fetchall()
    out = []
    for r in rows:
        d = _event_row(r)
        p, f = _ev_counts(conn, d["id"])
        d["participants"] = p; d["followers"] = f
        out.append(d)
    conn.close()
    return out


# ---------------------------------------------------------------------------
# Enfrentamientos y resultados (Fase 1)
# ---------------------------------------------------------------------------

def list_matches(eid) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT m.*, pa.name AS a_name, pb.name AS b_name,
                  ta.name AS a_team_name, ta.logo_url AS a_team_logo,
                  tb.name AS b_team_name, tb.logo_url AS b_team_logo
           FROM event_matches m
           LEFT JOIN players pa ON pa.tag = m.a_tag
           LEFT JOIN players pb ON pb.tag = m.b_tag
           LEFT JOIN event_teams ta ON ta.id = m.a_team
           LEFT JOIN event_teams tb ON tb.id = m.b_team
           WHERE m.event_id=? ORDER BY m.round, m.id""", (eid,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("roster_a", "roster_b"):
            if d.get(k):
                try: d[k] = json.loads(d[k])
                except Exception: d[k] = None
        out.append(d)
    return out


def get_match(mid) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM event_matches WHERE id=?", (mid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_match(eid, round=1, a_tag=None, b_tag=None, a_team=None, b_team=None,
                 mode=None, map=None, scheduled_at=None, bracket_pos=None,
                 roster_a=None, roster_b=None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    ra = json.dumps(roster_a) if roster_a else None
    rb = json.dumps(roster_b) if roster_b else None
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO event_matches (event_id, round, bracket_pos, a_tag, b_tag, a_team, b_team,
              mode, map, status, roster_a, roster_b, scheduled_at, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?, 'pending', ?,?, ?, ?, ?)""",
        (eid, round or 1, bracket_pos, a_tag, b_tag, a_team, b_team, mode, map, ra, rb, scheduled_at, now, now))
    conn.commit(); mid = cur.lastrowid; conn.close()
    return mid


def update_match(mid, fields: dict) -> None:
    cols = ["round", "bracket_pos", "a_tag", "b_tag", "a_team", "b_team", "mode", "map", "status",
            "score_a", "score_b", "winner", "evidence_battle_id", "scheduled_at"]
    sets, vals = [], []
    for c in cols:
        if c in fields:
            sets.append(f"{c}=?"); vals.append(fields[c])
    if not sets:
        return
    sets.append("updated_at=?"); vals.append(datetime.now(timezone.utc).isoformat())
    vals.append(mid)
    conn = get_conn()
    conn.execute(f"UPDATE event_matches SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit(); conn.close()


def delete_match(eid, mid) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM event_matches WHERE id=? AND event_id=?", (mid, eid))
    conn.commit(); conn.close()


def clear_matches(eid) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM event_matches WHERE event_id=?", (eid,))
    conn.commit(); conn.close()
