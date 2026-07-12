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
import threading
import time
from datetime import datetime, timezone, timedelta

from . import brawler_extra  # índice de roles (para filtrar/agregar por rol)

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "brawl_stats.db"))

GROUP_COLUMNS = {"brawler": "my_brawler", "mode": "mode", "map": "map"}


def get_conn():
    # timeout amplio + busy_timeout: bajo concurrencia (poller escribiendo mientras se leen
    # estadísticas) evita el error "database is locked" en vez de fallar al instante.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # PRAGMAs de rendimiento. WAL permite leer mientras se escribe (clave con el poller de fondo);
    # el resto reduce fsync y da más caché en RAM. Son baratos de fijar por conexión.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-16000")   # ~16 MB de caché de páginas
        conn.execute("PRAGMA busy_timeout=5000")
    except Exception:  # noqa: BLE001
        pass
    return conn


# --- Memoización en memoria con TTL para agregaciones COMUNITARIAS costosas -------------------
# Estas funciones agrupan partidas/colecciones de TODA la comunidad y se pedían en CADA request.
# La comunidad cambia despacio, así que un caché de pocos minutos elimina el recálculo repetido sin
# que el dato se note desfasado. Thread-safe (los handlers sync corren en el threadpool de FastAPI).
_agg_cache: dict = {}
_agg_lock = threading.Lock()


def _agg_get(key):
    with _agg_lock:
        hit = _agg_cache.get(key)
    return hit[1] if hit and hit[0] > time.time() else None


def _agg_put(key, value, ttl):
    with _agg_lock:
        _agg_cache[key] = (time.time() + ttl, value)
    return value


def invalidate_community_cache():
    """Vacía el caché de agregaciones comunitarias (útil tras un ingest grande o cambios de BD)."""
    with _agg_lock:
        _agg_cache.clear()


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
        -- Brawlers que un admin marca DISPONIBLES a mano (override si la app no lo detecta solo).
        CREATE TABLE IF NOT EXISTS brawler_available_override (brawler_id INTEGER PRIMARY KEY, at TEXT);
        -- Reflexiones del Sensei (IA) por brawler+jugador para la sección "Mejores Modos" (caché).
        CREATE TABLE IF NOT EXISTS brawler_insight (
            brawler_id INTEGER, player_tag TEXT, scope TEXT, data TEXT, at TEXT,
            PRIMARY KEY (brawler_id, player_tag, scope)
        );
        CREATE TABLE IF NOT EXISTS server_incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, started_at TEXT, ended_at TEXT
        );
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
        CREATE TABLE IF NOT EXISTS club_pages (
            club_tag TEXT PRIMARY KEY, name TEXT, description TEXT,
            edit_policy TEXT DEFAULT 'members', updated_at TEXT, updated_by INTEGER
        );
        CREATE TABLE IF NOT EXISTS club_editors (
            club_tag TEXT NOT NULL, player_tag TEXT NOT NULL,
            PRIMARY KEY (club_tag, player_tag)
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
        CREATE TABLE IF NOT EXISTS wiki_translations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER NOT NULL,
            lang TEXT NOT NULL, title TEXT NOT NULL, body TEXT,
            translator_user_id INTEGER, updated_at TEXT,
            UNIQUE(node_id, lang)
        );
        CREATE TABLE IF NOT EXISTS ui_translations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, lang TEXT NOT NULL,
            source TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'exact', target TEXT,
            updated_by INTEGER, updated_at TEXT,
            UNIQUE(lang, source)
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
        CREATE TABLE IF NOT EXISTS retos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER, source TEXT DEFAULT 'user', report_id INTEGER, target_user_id INTEGER,
            name TEXT, theme TEXT, description TEXT, conditions TEXT,
            difficulty_declared INTEGER, visibility TEXT DEFAULT 'public', time_limit_days INTEGER,
            status TEXT DEFAULT 'open', share_token TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS reto_participants (
            reto_id INTEGER NOT NULL, user_id INTEGER NOT NULL, player_tag TEXT,
            role TEXT DEFAULT 'participant', assigned_difficulty INTEGER,
            status TEXT DEFAULT 'active', joined_at TEXT, completed_at TEXT,
            PRIMARY KEY (reto_id, user_id)
        );
        -- Amistades entre usuarios de la plataforma (base social: co-organizadores, mensajes,
        -- perfiles públicos compartidos). Un par se guarda una sola vez con user_a < user_b.
        CREATE TABLE IF NOT EXISTS friendships (
            user_a INTEGER NOT NULL, user_b INTEGER NOT NULL, created_at TEXT,
            PRIMARY KEY (user_a, user_b)
        );
        CREATE TABLE IF NOT EXISTS friend_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER NOT NULL, to_user INTEGER NOT NULL,
            status TEXT DEFAULT 'pending', created_at TEXT,
            UNIQUE(from_user, to_user)
        );
        -- Co-organizadores de un evento (además del propietario). Pueden co-gestionar todo salvo
        -- borrar el evento o gestionar la propia lista de organizadores (eso es del propietario).
        CREATE TABLE IF NOT EXISTS event_organizers (
            event_id INTEGER NOT NULL, user_id INTEGER NOT NULL, added_at TEXT,
            PRIMARY KEY (event_id, user_id)
        );
        -- Redes sociales vinculadas por el usuario (fase F, publicación directa). Cada usuario vincula
        -- SUS cuentas voluntariamente vía OAuth; guardamos el token para publicar en su nombre. Los
        -- tokens son datos sensibles: solo se usan en el servidor, nunca se envían al cliente.
        CREATE TABLE IF NOT EXISTS social_accounts (
            user_id INTEGER NOT NULL, platform TEXT NOT NULL,
            external_id TEXT, external_name TEXT,
            access_token TEXT, refresh_token TEXT, expires_at TEXT, connected_at TEXT,
            PRIMARY KEY (user_id, platform)
        );
        -- Mensajería privada entre usuarios (fase E). El borrado es por lado (from/to_deleted): cada
        -- usuario oculta la conversación para sí sin afectar al otro. Aislamiento estricto por cuenta.
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER NOT NULL, to_user INTEGER NOT NULL,
            body TEXT NOT NULL, created_at TEXT, read_at TEXT,
            from_deleted INTEGER DEFAULT 0, to_deleted INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_userplayers_tag ON user_players(player_tag);
        CREATE INDEX IF NOT EXISTS idx_reports_player ON reports(player_tag);
        CREATE INDEX IF NOT EXISTS idx_battles_player  ON battles(player_tag);
        CREATE INDEX IF NOT EXISTS idx_battles_brawler ON battles(my_brawler);
        CREATE INDEX IF NOT EXISTS idx_battles_mode    ON battles(mode);
        CREATE INDEX IF NOT EXISTS idx_battles_map     ON battles(map);
        -- brawler_scene filtra por UPPER(my_brawler): sin índice de EXPRESIÓN hacía full scan de
        -- toda la tabla battles (cientos de miles de filas). Estos lo convierten en búsqueda indexada.
        CREATE INDEX IF NOT EXISTS idx_battles_brup_mode ON battles(UPPER(my_brawler), mode);
        CREATE INDEX IF NOT EXISTS idx_battles_brup_map  ON battles(UPPER(my_brawler), map);
        CREATE INDEX IF NOT EXISTS idx_battles_player_brawler ON battles(player_tag, my_brawler);
        -- rotation_analysis filtra por player_tag + LOWER(map)/LOWER(mode) (índice de expresión).
        CREATE INDEX IF NOT EXISTS idx_battles_pl_lmap_lmode ON battles(player_tag, LOWER(map), LOWER(mode));
        -- competitive_pool: LOWER(battle_type) + ventana temporal battle_time.
        CREATE INDEX IF NOT EXISTS idx_battles_lbtype_time ON battles(LOWER(battle_type), battle_time);
        -- winrate_by hace LEFT JOIN a una subconsulta AVG(trophies) de opponents por batalla:
        -- este índice de cobertura evita releer la tabla opponents para cada AVG.
        CREATE INDEX IF NOT EXISTS idx_opponents_battle_trophies ON opponents(battle_id, trophies);
        CREATE INDEX IF NOT EXISTS idx_opponents_brawler ON opponents(brawler);
        CREATE INDEX IF NOT EXISTS idx_opponents_battle  ON opponents(battle_id);
        CREATE INDEX IF NOT EXISTS idx_allies_battle     ON allies(battle_id);
        CREATE INDEX IF NOT EXISTS idx_brcoll_player     ON brawler_collection(player_tag);
        CREATE INDEX IF NOT EXISTS idx_ematches_event    ON event_matches(event_id);
        CREATE INDEX IF NOT EXISTS idx_eorg_event         ON event_organizers(event_id);
        CREATE INDEX IF NOT EXISTS idx_msg_to              ON messages(to_user);
        CREATE INDEX IF NOT EXISTS idx_msg_from            ON messages(from_user);
        CREATE INDEX IF NOT EXISTS idx_retopart_reto     ON reto_participants(reto_id);
        CREATE INDEX IF NOT EXISTS idx_retopart_user     ON reto_participants(user_id);
        CREATE INDEX IF NOT EXISTS idx_retos_status      ON retos(status);
        """
    )
    # Migración de bases antiguas: añade columnas nuevas si faltan.
    _ensure_column(cur, "players", "icon_id", "INTEGER")
    _ensure_column(cur, "players", "club_name", "TEXT")
    _ensure_column(cur, "players", "club_tag", "TEXT")    # tag del club (para páginas de club y descubrimiento)
    _ensure_column(cur, "players", "last_error", "TEXT")  # último fallo de sondeo (404 / tag inexistente)
    _ensure_column(cur, "players", "sensei_desc", "TEXT")     # descripción pública que genera el Sensei (IA)
    _ensure_column(cur, "players", "sensei_desc_at", "TEXT")  # cuándo se generó (para renovar cada semana)
    _ensure_column(cur, "users", "country", "TEXT")   # país declarado (priorización social; NO afecta a rankings)
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
    _ensure_column(cur, "wiki_nodes", "orig_lang", "TEXT DEFAULT 'es'")   # idioma del contenido original
    _ensure_column(cur, "wiki_history", "lang", "TEXT DEFAULT 'es'")       # idioma de la versión guardada
    _ensure_column(cur, "users", "is_translator", "INTEGER DEFAULT 0")    # colaborador de traducción (Rosetta)
    # --- RBAC (control de accesos por roles) ---
    _ensure_column(cur, "users", "role", "TEXT")            # root/admin/collaborator/translator/user (fuente de verdad)
    _ensure_column(cur, "users", "status", "TEXT DEFAULT 'active'")  # active / pending (a la espera de aprobación) / disabled
    _ensure_column(cur, "users", "email", "TEXT")          # email de contacto (registro; dato mínimo)
    _ensure_column(cur, "users", "is_croker", "INTEGER DEFAULT 0")   # rol de JUGADOR: miembro del club Crokers (bono de límites)
    _ensure_column(cur, "users", "ai_tokens_remaining", "INTEGER")   # "Pergaminos" restantes (sistema desactivado; base lista)
    _ensure_column(cur, "users", "ai_tokens_period", "TEXT")         # periodo (AAAA-MM) de la última recarga de Pergaminos
    _ensure_column(cur, "users", "hidden", "INTEGER DEFAULT 0")      # cuenta de sistema (p. ej. tester): oculta del descubrimiento público
    _ensure_column(cur, "users", "main_player_tag", "TEXT")         # jugador PRINCIPAL de la cuenta (identidad; def. del perfil público; base del rol Croker)
    # El usuario root por defecto (configurable con ROOT_USERNAME) es administrador root.
    import os as _os
    _root_username = _os.environ.get("ROOT_USERNAME", "itxialdiak")
    cur.execute("UPDATE users SET is_admin=1 WHERE username=?", (_root_username,))
    # El root designado por entorno SIEMPRE tiene rol root (idempotente: garantiza que
    # exista un root aunque su cuenta se creara con rol 'user').
    cur.execute("UPDATE users SET role='root' WHERE username=?", (_root_username,))
    # Backfill del rol para cuentas existentes (una sola vez): deriva de los flags antiguos.
    cur.execute("UPDATE users SET role='admin' WHERE is_admin=1 AND (role IS NULL OR role='')")
    cur.execute("UPDATE users SET role='translator' WHERE is_translator=1 AND (role IS NULL OR role='')")
    cur.execute("UPDATE users SET role='user' WHERE role IS NULL OR role=''")
    # Toda cuenta preexistente queda activa (el flujo de aprobación solo afecta a registros nuevos).
    cur.execute("UPDATE users SET status='active' WHERE status IS NULL OR status=''")
    conn.commit()
    conn.close()
    seed_wiki_if_empty()
    seed_wiki_translations()
    seed_ui_translations()
    seed_ui_translations_from_json()


# ---------------------------------------------------------------------------
# Jugadores
# ---------------------------------------------------------------------------

def normalize_tag(tag: str) -> str:
    t = (tag or "").strip().upper()
    return t if t.startswith("#") else "#" + t


def add_player(tag: str, name: str | None = None, icon_id: int | None = None,
               club_name: str | None = None, club_tag: str | None = None) -> bool:
    tag = normalize_tag(tag)
    conn = get_conn(); cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cur.execute("INSERT OR IGNORE INTO players (tag, name, added_at, active, icon_id, club_name, club_tag) VALUES (?,?,?,1,?,?,?)",
                (tag, name, now, icon_id, club_name, club_tag))
    added = cur.rowcount == 1
    if not added and (name or icon_id is not None or club_name is not None or club_tag is not None):
        cur.execute("UPDATE players SET name=COALESCE(?,name), icon_id=COALESCE(?,icon_id), "
                    "club_name=COALESCE(?,club_name), club_tag=COALESCE(?,club_tag) WHERE tag=?",
                    (name, icon_id, club_name, club_tag, tag))
    conn.commit(); conn.close()
    return added


def update_player_profile(tag: str, name: str | None, icon_id: int | None,
                          club_name: str | None = None, club_tag: str | None = None) -> None:
    ntag = normalize_tag(tag)
    conn = get_conn()
    # club_name/club_tag: si el jugador SALE del club la API los da como None; para reflejar la
    # salida (no solo entradas) los escribimos siempre que se refresca el perfil (club_name is not None
    # marca "hubo refresco de club"): usamos el valor tal cual (puede ser None) en esos dos campos.
    conn.execute("UPDATE players SET name=COALESCE(?,name), icon_id=COALESCE(?,icon_id), "
                 "club_name=?, club_tag=? WHERE tag=?",
                 (name, icon_id, club_name, club_tag, ntag))
    conn.commit(); conn.close()
    refresh_croker_for_player(ntag)   # el club puede haber cambiado → recalcula Croker del principal


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


def list_players_admin() -> list[dict]:
    """Todos los jugadores trackeados con nº de partidas y nº de seguidores (usuarios
    que lo siguen). Los huérfanos (followers=0) van primero. Para el panel de admin."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT p.tag, p.name, p.added_at, p.last_polled, p.active, p.icon_id, p.club_name, p.last_error,
               (SELECT COUNT(*) FROM battles b WHERE b.player_tag = p.tag) AS battles,
               (SELECT COUNT(*) FROM user_players up WHERE up.player_tag = p.tag) AS followers
        FROM players p ORDER BY (p.last_error IS NOT NULL) DESC, followers ASC, battles DESC, p.added_at DESC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_player_error(tag: str, error: str) -> None:
    """Marca un jugador con el fallo de su último sondeo (p. ej. tag inexistente / 404),
    para que el panel de administración lo resalte como 'necesita revisión'."""
    conn = get_conn()
    conn.execute("UPDATE players SET last_error=? WHERE tag=?", (error, normalize_tag(tag)))
    conn.commit(); conn.close()


def clear_player_error(tag: str) -> None:
    """Quita la marca de fallo cuando el jugador vuelve a sondearse bien."""
    conn = get_conn()
    conn.execute("UPDATE players SET last_error=NULL WHERE tag=? AND last_error IS NOT NULL",
                 (normalize_tag(tag),))
    conn.commit(); conn.close()


def admin_remove_player(tag: str, delete_battles: bool = False) -> None:
    """Deja de trackear un jugador. Por defecto conserva su historial de partidas
    (solo deja de recopilar más); con delete_battles=True borra también el registro."""
    tag = normalize_tag(tag)
    if delete_battles:
        remove_player(tag)  # limpieza completa (battles + cascadas + fila del jugador)
        return
    conn = get_conn()
    conn.execute("DELETE FROM user_players WHERE player_tag=?", (tag,))
    conn.execute("DELETE FROM players WHERE tag=?", (tag,))  # deja de sondearse; battles intactas
    conn.commit(); conn.close()


def active_player_tags() -> list[str]:
    """Jugadores a sondear: todos los activos —los que sigue algún usuario y también
    los que un admin haya añadido al trackeo sin seguidores (huérfanos)."""
    conn = get_conn()
    rows = conn.execute("SELECT tag FROM players WHERE active=1").fetchall()
    conn.close()
    return [r[0] for r in rows]


# --- Descripción pública del Sensei (IA) por jugador ---------------------------------------
def get_player_sensei_desc(tag: str) -> dict:
    """{desc, at} de la descripción pública del Sensei de un jugador (o {None, None})."""
    conn = get_conn()
    row = conn.execute("SELECT sensei_desc, sensei_desc_at FROM players WHERE tag=?",
                       (normalize_tag(tag),)).fetchone()
    conn.close()
    return {"desc": row["sensei_desc"] if row else None,
            "at": row["sensei_desc_at"] if row else None}


def set_player_sensei_desc(tag: str, desc: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE players SET sensei_desc=?, sensei_desc_at=? WHERE tag=?",
                 (desc, datetime.now(timezone.utc).isoformat(), normalize_tag(tag)))
    conn.commit(); conn.close()


def owned_brawler_ids() -> set:
    """IDs de brawlers que POSEE algún jugador trackeado (aparecen en alguna colección) → prueba
    de que ese brawler ya está lanzado y disponible en el juego."""
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT brawler_id FROM brawler_collection").fetchall()
    conn.close()
    return {r[0] for r in rows if r[0] is not None}


def brawler_available_overrides() -> set:
    """IDs de brawlers marcados DISPONIBLES a mano por un admin."""
    conn = get_conn()
    rows = conn.execute("SELECT brawler_id FROM brawler_available_override").fetchall()
    conn.close()
    return {r[0] for r in rows}


def set_brawler_available(bid: int, available: bool = True) -> None:
    conn = get_conn()
    if available:
        conn.execute("INSERT OR IGNORE INTO brawler_available_override (brawler_id, at) VALUES (?,?)",
                     (int(bid), datetime.now(timezone.utc).isoformat()))
    else:
        conn.execute("DELETE FROM brawler_available_override WHERE brawler_id=?", (int(bid),))
    conn.commit(); conn.close()


def get_brawler_insight(brawler_id: int, player_tag: str, scope: str = "community") -> dict:
    """{data, at} de las reflexiones IA cacheadas de un brawler+jugador (o {None, None})."""
    conn = get_conn()
    row = conn.execute("SELECT data, at FROM brawler_insight WHERE brawler_id=? AND player_tag=? AND scope=?",
                       (int(brawler_id), normalize_tag(player_tag), scope)).fetchone()
    conn.close()
    return {"data": row["data"] if row else None, "at": row["at"] if row else None}


def set_brawler_insight(brawler_id: int, player_tag: str, scope: str, data: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO brawler_insight (brawler_id,player_tag,scope,data,at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(brawler_id,player_tag,scope) DO UPDATE SET data=excluded.data, at=excluded.at",
        (int(brawler_id), normalize_tag(player_tag), scope, data, datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()


def public_player_tags() -> list[str]:
    """Jugadores VISIBLES en perfiles públicos (seguidos por algún usuario no oculto) — el
    conjunto a renovar semanalmente. Prioriza los que son 'principal' de alguien."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT DISTINCT p.tag FROM players p
           JOIN user_players up ON up.player_tag = p.tag
           JOIN users u ON u.id = up.user_id AND COALESCE(u.hidden,0)=0
           ORDER BY (SELECT 1 FROM users um WHERE um.main_player_tag=p.tag LIMIT 1) DESC""").fetchall()
    conn.close()
    return [r[0] for r in rows]


def mark_polled(tag: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE players SET last_polled=? WHERE tag=?",
                 (datetime.now(timezone.utc).isoformat(), normalize_tag(tag)))
    conn.commit(); conn.close()


# ---------------------------------------------------------------------------
# Estado del servidor de Supercell (incidencias: mantenimiento / caída)
# ---------------------------------------------------------------------------
def set_server_state(state: str, now: str) -> None:
    """Máquina de estados del servidor de Supercell. 'online' CIERRA cualquier incidencia
    abierta (registra su fin -> duración). 'maintenance'/'down' ABREN una incidencia si no hay
    ya una del mismo tipo (la hora de inicio queda fija mientras dura)."""
    conn = get_conn()
    cur = conn.execute("SELECT id, kind FROM server_incidents WHERE ended_at IS NULL "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    if state == "online":
        if cur:
            conn.execute("UPDATE server_incidents SET ended_at=? WHERE id=?", (now, cur["id"]))
            conn.commit()
        conn.close(); return
    if cur and cur["kind"] == state:
        conn.close(); return                       # ya abierta del mismo tipo: no cambia
    if cur:                                         # estado distinto: cierra la anterior
        conn.execute("UPDATE server_incidents SET ended_at=? WHERE id=?", (now, cur["id"]))
    conn.execute("INSERT INTO server_incidents (kind, started_at, ended_at) VALUES (?,?,NULL)",
                 (state, now))
    conn.commit(); conn.close()


def current_incident() -> dict | None:
    conn = get_conn()
    r = conn.execute("SELECT id, kind, started_at, ended_at FROM server_incidents "
                     "WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(r) if r else None


def incident_history(limit: int = 60) -> list:
    conn = get_conn()
    rows = conn.execute("SELECT kind, started_at, ended_at FROM server_incidents "
                        "WHERE ended_at IS NOT NULL ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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

def create_user(username: str, password_hash: str, email: str | None = None,
                status: str = "active") -> int | None:
    """Crea un usuario con rol 'user'. Devuelve su id, o None si el nombre ya existe.

    `status`: 'active' (por defecto, p. ej. altas manuales del admin) o 'pending'
    (registro público a la espera de aprobación por un administrador)."""
    if status not in ("active", "pending", "disabled"):
        status = "active"
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (username, password_hash, created_at, email, role, status) "
            "VALUES (?,?,?,?, 'user', ?)",
            (username, password_hash, datetime.now(timezone.utc).isoformat(), email, status),
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


# ---------------------------------------------------------------------------
# Amigos (base social): solicitudes + amistades. Un par se guarda una sola vez
# ordenado (a < b). Todas las consultas son por usuario (aislamiento de cuenta).
# ---------------------------------------------------------------------------

def _pair(a: int, b: int) -> tuple:
    a, b = int(a), int(b)
    return (a, b) if a < b else (b, a)


_ICON_CDN = "https://cdn.brawlify.com/profile-icons/regular/{id}.png"


def _pub_user(row) -> dict:
    return {"id": row["id"], "username": row["username"], "country": row["country"]}


def search_users(query: str, exclude_id: int, limit: int = 12) -> list[dict]:
    """Busca usuarios por prefijo/substring de nombre (para añadir amigos). Excluye a uno mismo."""
    q = (query or "").strip()
    if not q:
        return []
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, username, country FROM users WHERE username LIKE ? AND id != ? "
        "AND COALESCE(hidden,0)=0 "
        "ORDER BY (username = ?) DESC, username LIMIT ?",
        (f"%{q}%", exclude_id, q, limit)).fetchall()
    conn.close()
    return [_pub_user(r) for r in rows]


def suggested_users(uid: int, limit: int = 40) -> list[dict]:
    """Usuarios ordenados por relevancia social (estilo red social): 1) amigos de tus amigos,
    2) cercanía por club (algún jugador tuyo comparte club con alguno suyo), 3) mismo país
    declarado, 4) por contribución a la comunidad (partidas aportadas). Excluye a ti y a tus amigos."""
    conn = get_conn()
    row = conn.execute("SELECT country FROM users WHERE id=?", (uid,)).fetchone()
    me_country = row["country"] if row else None
    # amigos actuales (para excluir)
    my_friends = set()
    for r in conn.execute("SELECT user_a, user_b FROM friendships WHERE user_a=? OR user_b=?", (uid, uid)):
        my_friends.add(r["user_a"] if r["user_b"] == uid else r["user_b"])
    # amigos de tus amigos (nº de amigos en común)
    fof = {}
    if my_friends:
        qs = ",".join("?" * len(my_friends))
        fl = list(my_friends)
        for r in conn.execute(
            f"SELECT user_a, user_b FROM friendships WHERE user_a IN ({qs}) OR user_b IN ({qs})", fl + fl):
            for other in (r["user_a"], r["user_b"]):
                if other != uid and other not in my_friends:
                    fof[other] = fof.get(other, 0) + 1
    # usuarios que comparten club con alguno de tus jugadores
    my_clubs = [r["club_name"] for r in conn.execute(
        "SELECT DISTINCT p.club_name FROM user_players up JOIN players p ON p.tag=up.player_tag "
        "WHERE up.user_id=? AND p.club_name IS NOT NULL AND p.club_name<>''", (uid,))]
    club_users = set()
    if my_clubs:
        qs = ",".join("?" * len(my_clubs))
        for r in conn.execute(
            f"SELECT DISTINCT up.user_id FROM user_players up JOIN players p ON p.tag=up.player_tag "
            f"WHERE p.club_name IN ({qs})", my_clubs):
            if r["user_id"] != uid:
                club_users.add(r["user_id"])
    # contribución (partidas aportadas) y nº de jugadores por usuario
    contrib = {r["user_id"]: (r["n_players"], r["n_battles"] or 0) for r in conn.execute(
        "SELECT up.user_id, COUNT(DISTINCT up.player_tag) AS n_players, "
        "COUNT(b.id) AS n_battles FROM user_players up "
        "LEFT JOIN battles b ON b.player_tag = up.player_tag GROUP BY up.user_id")}
    rows = conn.execute(
        "SELECT id, username, country FROM users WHERE id<>? AND COALESCE(hidden,0)=0", (uid,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        if r["id"] in my_friends:
            continue
        common = fof.get(r["id"], 0)
        n_players, n_battles = contrib.get(r["id"], (0, 0))
        score = 0.0
        if common:
            score += 1000 + common * 25
        if r["id"] in club_users:
            score += 400
        if me_country and r["country"] and r["country"] == me_country:
            score += 120                        # mismo país declarado: prioriza jugadores de tu país
        score += min(n_battles / 50.0, 100)   # contribución (tope 100)
        out.append({"id": r["id"], "username": r["username"], "country": r["country"],
                    "mutual_friends": common, "same_club": r["id"] in club_users,
                    "n_players": n_players, "n_battles": n_battles, "_score": score})
    out.sort(key=lambda x: (x["_score"], x["n_battles"]), reverse=True)
    for o in out:
        o.pop("_score", None)
    return out[:limit]


def public_users(limit: int = 60, q: str = None) -> list[dict]:
    """Lista PÚBLICA de usuarios (para invitados sin cuenta), ordenada por relevancia de la cuenta:
    contribución a la comunidad (partidas aportadas) y nº de jugadores. Filtro opcional por nombre."""
    conn = get_conn()
    contrib = {r["user_id"]: (r["n_players"], r["n_battles"] or 0) for r in conn.execute(
        "SELECT up.user_id, COUNT(DISTINCT up.player_tag) AS n_players, COUNT(b.id) AS n_battles "
        "FROM user_players up LEFT JOIN battles b ON b.player_tag = up.player_tag GROUP BY up.user_id")}
    qq = (q or "").strip()
    if qq:
        rows = conn.execute("SELECT id, username, country FROM users WHERE username LIKE ? "
                            "AND COALESCE(hidden,0)=0", (f"%{qq}%",)).fetchall()
    else:
        rows = conn.execute("SELECT id, username, country FROM users "
                            "WHERE COALESCE(hidden,0)=0").fetchall()
    out = []
    for r in rows:
        n_players, n_battles = contrib.get(r["id"], (0, 0))
        out.append({"id": r["id"], "username": r["username"], "country": r["country"],
                    "n_players": n_players, "n_battles": n_battles})
    out.sort(key=lambda x: (x["n_battles"], x["n_players"]), reverse=True)
    out = out[:limit]
    _enrich_public_mains(conn, out)   # miniatura del jugador principal (para las tarjetas)
    conn.close()
    return out


def _enrich_public_mains(conn, users: list) -> None:
    """Añade a cada usuario un `main` compacto (su jugador PRINCIPAL, o el de más partidas si no
    hay principal): nombre, icono, win rate, partidas, copas y su brawler más jugado. Para las
    miniaturas de la comunidad en la landing. Consultas en bloque (sin N+1)."""
    if not users:
        return
    tag_of = {}
    for u in users:
        row = conn.execute("SELECT main_player_tag FROM users WHERE id=?", (u["id"],)).fetchone()
        t = (row["main_player_tag"] if row else None) or None
        if not t:
            row = conn.execute(
                "SELECT up.player_tag FROM user_players up LEFT JOIN battles b ON b.player_tag=up.player_tag "
                "WHERE up.user_id=? GROUP BY up.player_tag ORDER BY COUNT(b.id) DESC LIMIT 1",
                (u["id"],)).fetchone()
            t = row["player_tag"] if row else None
        tag_of[u["id"]] = t
    tags = sorted({t for t in tag_of.values() if t})
    if not tags:
        for u in users:
            u["main"] = None
        return
    ph = ",".join("?" * len(tags))
    pinfo = {r["tag"]: r for r in conn.execute(
        f"SELECT tag, name, icon_id, club_name FROM players WHERE tag IN ({ph})", tags)}
    wr = {r["player_tag"]: r for r in conn.execute(
        f"SELECT player_tag, SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END) w, "
        f"SUM(CASE WHEN is_win=0 THEN 1 ELSE 0 END) l, COUNT(*) t "
        f"FROM battles WHERE player_tag IN ({ph}) GROUP BY player_tag", tags)}
    tro = {r["player_tag"]: r["s"] for r in conn.execute(
        f"SELECT player_tag, COALESCE(SUM(trophies),0) s FROM brawler_collection "
        f"WHERE player_tag IN ({ph}) GROUP BY player_tag", tags)}
    topb = {}
    for r in conn.execute(
            f"SELECT player_tag, my_brawler, COUNT(*) c FROM battles "
            f"WHERE player_tag IN ({ph}) AND my_brawler IS NOT NULL GROUP BY player_tag, my_brawler", tags):
        cur = topb.get(r["player_tag"])
        if not cur or r["c"] > cur[1]:
            topb[r["player_tag"]] = (r["my_brawler"], r["c"])
    for u in users:
        t = tag_of.get(u["id"])
        p = pinfo.get(t) if t else None
        if not p:
            u["main"] = None
            continue
        b = wr.get(t)
        u["main"] = {
            "tag": t, "name": p["name"], "icon_id": p["icon_id"], "club_name": p["club_name"],
            "icon_url": (_ICON_CDN.format(id=p["icon_id"]) if p["icon_id"] else None),
            "winrate": _winrate(b["w"], b["l"]) if b else None,
            "battles": (b["t"] if b else 0) or 0, "trophies": tro.get(t, 0),
            "top_brawler": topb.get(t, (None,))[0],
        }


def user_contribution(uid: int) -> tuple:
    """(nº de jugadores trackeados, nº de partidas aportadas) por el usuario."""
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(DISTINCT up.player_tag) AS n_players, COUNT(b.id) AS n_battles "
        "FROM user_players up LEFT JOIN battles b ON b.player_tag = up.player_tag WHERE up.user_id=?",
        (uid,)).fetchone()
    conn.close()
    return (row["n_players"] if row else 0, (row["n_battles"] if row else 0) or 0)


def are_friends(a: int, b: int) -> bool:
    ua, ub = _pair(a, b)
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM friendships WHERE user_a=? AND user_b=?", (ua, ub)).fetchone()
    conn.close()
    return row is not None


def _add_friendship(cur, a: int, b: int) -> None:
    ua, ub = _pair(a, b)
    cur.execute("INSERT OR IGNORE INTO friendships (user_a, user_b, created_at) VALUES (?,?,?)",
                (ua, ub, datetime.now(timezone.utc).isoformat()))


def friend_request_status(from_id: int, to_id: int) -> str | None:
    """Estado de la solicitud from->to si existe ('pending'…), o None."""
    conn = get_conn()
    row = conn.execute("SELECT status FROM friend_requests WHERE from_user=? AND to_user=?",
                       (from_id, to_id)).fetchone()
    conn.close()
    return row["status"] if row else None


def send_friend_request(from_id: int, to_id: int) -> dict:
    """Envía una solicitud. Si ya existe la inversa pendiente, se aceptan mutuamente.
    Devuelve {status: 'friends'|'pending'|'exists'|'self'}."""
    if int(from_id) == int(to_id):
        return {"status": "self"}
    if are_friends(from_id, to_id):
        return {"status": "friends"}
    conn = get_conn(); cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    # ¿Hay una solicitud inversa pendiente? -> aceptar y hacerse amigos.
    rev = cur.execute("SELECT id FROM friend_requests WHERE from_user=? AND to_user=? AND status='pending'",
                      (to_id, from_id)).fetchone()
    if rev:
        cur.execute("UPDATE friend_requests SET status='accepted' WHERE id=?", (rev["id"],))
        _add_friendship(cur, from_id, to_id)
        conn.commit(); conn.close()
        return {"status": "friends"}
    existing = cur.execute("SELECT status FROM friend_requests WHERE from_user=? AND to_user=?",
                           (from_id, to_id)).fetchone()
    if existing and existing["status"] == "pending":
        conn.close(); return {"status": "exists"}
    # crea o reactiva la solicitud (si estaba rechazada, vuelve a pendiente)
    cur.execute("INSERT INTO friend_requests (from_user, to_user, status, created_at) VALUES (?,?,'pending',?) "
                "ON CONFLICT(from_user, to_user) DO UPDATE SET status='pending', created_at=excluded.created_at",
                (from_id, to_id, now))
    conn.commit(); conn.close()
    return {"status": "pending"}


def accept_friend_request(req_id: int, user_id: int) -> bool:
    """Acepta una solicitud dirigida a user_id. Crea la amistad."""
    conn = get_conn(); cur = conn.cursor()
    row = cur.execute("SELECT from_user, to_user, status FROM friend_requests WHERE id=?", (req_id,)).fetchone()
    if not row or row["to_user"] != user_id or row["status"] != "pending":
        conn.close(); return False
    cur.execute("UPDATE friend_requests SET status='accepted' WHERE id=?", (req_id,))
    _add_friendship(cur, row["from_user"], row["to_user"])
    conn.commit(); conn.close()
    return True


def reject_friend_request(req_id: int, user_id: int) -> bool:
    """Rechaza (si eres el destinatario) o cancela (si eres el emisor) una solicitud pendiente."""
    conn = get_conn(); cur = conn.cursor()
    row = cur.execute("SELECT from_user, to_user, status FROM friend_requests WHERE id=?", (req_id,)).fetchone()
    if not row or user_id not in (row["from_user"], row["to_user"]) or row["status"] != "pending":
        conn.close(); return False
    # el emisor cancela (borra), el destinatario rechaza (marca)
    if user_id == row["from_user"]:
        cur.execute("DELETE FROM friend_requests WHERE id=?", (req_id,))
    else:
        cur.execute("UPDATE friend_requests SET status='rejected' WHERE id=?", (req_id,))
    conn.commit(); conn.close()
    return True


def remove_friend(a: int, b: int) -> None:
    ua, ub = _pair(a, b)
    conn = get_conn()
    conn.execute("DELETE FROM friendships WHERE user_a=? AND user_b=?", (ua, ub))
    # limpia cualquier solicitud entre ambos para poder re-solicitar en el futuro
    conn.execute("DELETE FROM friend_requests WHERE (from_user=? AND to_user=?) OR (from_user=? AND to_user=?)",
                 (a, b, b, a))
    conn.commit(); conn.close()


def list_friends(user_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT u.id, u.username, u.country FROM friendships f "
        "JOIN users u ON u.id = CASE WHEN f.user_a=? THEN f.user_b ELSE f.user_a END "
        "WHERE f.user_a=? OR f.user_b=? ORDER BY u.username",
        (user_id, user_id, user_id)).fetchall()
    conn.close()
    return [_pub_user(r) for r in rows]


def list_incoming_requests(user_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT r.id AS req_id, u.id, u.username, u.country, r.created_at FROM friend_requests r "
        "JOIN users u ON u.id = r.from_user WHERE r.to_user=? AND r.status='pending' ORDER BY r.created_at DESC",
        (user_id,)).fetchall()
    conn.close()
    return [{**_pub_user(r), "req_id": r["req_id"], "created_at": r["created_at"]} for r in rows]


def list_outgoing_requests(user_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT r.id AS req_id, u.id, u.username, u.country, r.created_at FROM friend_requests r "
        "JOIN users u ON u.id = r.to_user WHERE r.from_user=? AND r.status='pending' ORDER BY r.created_at DESC",
        (user_id,)).fetchall()
    conn.close()
    return [{**_pub_user(r), "req_id": r["req_id"], "created_at": r["created_at"]} for r in rows]


def count_incoming_requests(user_id: int) -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM friend_requests WHERE to_user=? AND status='pending'",
                     (user_id,)).fetchone()[0]
    conn.close()
    return n


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


WIKI_TR_SEED_PATH = os.path.join(os.path.dirname(__file__), "data", "wiki_translations_seed.json")


def seed_wiki_translations() -> None:
    """Carga traducciones sembradas (p.ej. inglés) desde data/wiki_translations_seed.json,
    casando por el TÍTULO ORIGINAL del nodo (los ids no son estables entre BDs). No pisa
    traducciones ya existentes (para no machacar mejoras de la comunidad)."""
    try:
        seed = json.load(open(WIKI_TR_SEED_PATH, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    for e in seed or []:
        orig, lang = e.get("orig_title"), e.get("lang")
        if not orig or not lang or lang == "es":
            continue
        row = conn.execute("SELECT id FROM wiki_nodes WHERE title=? ORDER BY id LIMIT 1", (orig,)).fetchone()
        if not row:
            continue
        if conn.execute("SELECT 1 FROM wiki_translations WHERE node_id=? AND lang=?", (row["id"], lang)).fetchone():
            continue
        conn.execute(
            "INSERT INTO wiki_translations (node_id,lang,title,body,translator_user_id,updated_at) "
            "VALUES (?,?,?,?,?,?)", (row["id"], lang, e.get("title") or orig, e.get("body"), None, now))
    conn.commit(); conn.close()


# --------------------------- Traducción de la interfaz (Rosetta) ---------------------------

UI_SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "frontend", "i18n", "_seed.json")


def ui_upsert_translation(lang: str, source: str, kind: str, target, user_id) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO ui_translations (lang,source,kind,target,updated_by,updated_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(lang,source) DO UPDATE SET kind=excluded.kind, target=excluded.target, "
        "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
        (lang, source, kind or "exact", target, user_id, datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()


def ui_translations_rows(lang: str) -> list:
    """Filas {source, kind, target} de un idioma (para servir y para el editor)."""
    conn = get_conn()
    rows = conn.execute("SELECT source, kind, target FROM ui_translations WHERE lang=?", (lang,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def ui_translated_langs() -> list:
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT lang FROM ui_translations ORDER BY lang").fetchall()
    conn.close()
    return [r["lang"] for r in rows]


def set_user_translator(user_id: int, val: bool) -> None:
    # Compatibilidad: alternar traductor solo mueve entre user<->translator; no toca
    # roles superiores (admin/collaborator ya tienen is_translator por su rol).
    u = get_user_by_id(user_id)
    role = (u or {}).get("role") or "user"
    if val and role == "user":
        set_user_role(user_id, "translator")
    elif not val and role == "translator":
        set_user_role(user_id, "user")
    else:
        conn = get_conn()
        conn.execute("UPDATE users SET is_translator=? WHERE id=?", (1 if val else 0, user_id))
        conn.commit(); conn.close()


def seed_ui_translations() -> None:
    """Precarga traducciones sembradas (p.ej. inglés) en ui_translations desde
    frontend/i18n/_seed.json, para que sean editables y sirvan de referencia. No pisa lo ya
    guardado (respeta mejoras de la comunidad). Estructura: {lang: {exact:{}, patterns:{}}}."""
    try:
        seed = json.load(open(UI_SEED_PATH, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    have = {(r["lang"], r["source"]) for r in conn.execute("SELECT lang, source FROM ui_translations").fetchall()}
    for lang, data in (seed or {}).items():
        for kind, key in (("exact", "exact"), ("pattern", "patterns")):
            for src, tgt in (data.get(key) or {}).items():
                if (lang, src) in have:
                    continue
                conn.execute(
                    "INSERT INTO ui_translations (lang,source,kind,target,updated_by,updated_at) "
                    "VALUES (?,?,?,?,?,?)", (lang, src, kind, tgt, None, now))
    conn.commit(); conn.close()


UI_I18N_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "i18n")


def _ui_pattern_template_map() -> dict:
    """{regex_compilado: (plantilla_es, [orden de marcadores 'n'/'s'])} desde _sources.json.
    El regex de un patrón se deriva del ESPAÑOL, así que es idéntico en todos los .json de
    idioma; nos sirve para recuperar a qué plantilla española corresponde cada patrón."""
    import re
    from . import i18n_tools
    try:
        src = json.load(open(os.path.join(UI_I18N_DIR, "_sources.json"), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for tpl in (src.get("patterns") or []):
        rule = i18n_tools.compile_pattern(tpl, tpl)
        if rule:
            order = [p[1] for p in re.split(r"(\{[ns]\})", tpl) if p in ("{n}", "{s}")]
            out[rule[0]] = (tpl, order)
    return out


def _ui_reverse_pattern(sub: str, order: list) -> str:
    """Convierte la sustitución compilada ($1,$2…) de vuelta a plantilla con {n}/{s}, usando el
    orden de marcadores del origen español (compile_pattern numera $i en orden de aparición)."""
    import re
    def rep(m):
        i = int(m.group(1)) - 1
        return "{" + order[i] + "}" if 0 <= i < len(order) else m.group(0)
    return re.sub(r"\$(\d+)", rep, sub)


def seed_ui_translations_from_json() -> None:
    """Siembra en `ui_translations` las traducciones de los ficheros estáticos
    `frontend/i18n/<lang>.json` para que TODOS los idiomas (no solo el inglés) sean editables y
    mejorables desde Rosetta. Textos exactos directos; los patrones (guardados compilados en el
    .json) se reconstruyen a plantilla {n}/{s}. NO pisa lo ya guardado (respeta mejoras previas)."""
    import glob
    pat_map = _ui_pattern_template_map()
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    have = {(r["lang"], r["source"]) for r in
            conn.execute("SELECT lang, source FROM ui_translations").fetchall()}
    for path in glob.glob(os.path.join(UI_I18N_DIR, "*.json")):
        lang = os.path.splitext(os.path.basename(path))[0]
        # 'en' ya viene de _seed.json; _seed/_sources y cualquier '_*' no son idiomas.
        if lang.startswith("_") or lang == "en":
            continue
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for src, tgt in data.items():
            if src == "__patterns__":
                for rule in (tgt or []):
                    if not (isinstance(rule, list) and len(rule) == 2):
                        continue
                    info = pat_map.get(rule[0])
                    if not info or (lang, info[0]) in have:
                        continue
                    es_tpl, order = info
                    conn.execute(
                        "INSERT INTO ui_translations (lang,source,kind,target,updated_by,updated_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (lang, es_tpl, "pattern", _ui_reverse_pattern(rule[1], order), None, now))
                    have.add((lang, es_tpl))
                continue
            if isinstance(tgt, str) and (lang, src) not in have:
                conn.execute(
                    "INSERT INTO ui_translations (lang,source,kind,target,updated_by,updated_at) "
                    "VALUES (?,?,?,?,?,?)", (lang, src, "exact", tgt, None, now))
                have.add((lang, src))
    conn.commit(); conn.close()


def get_wiki_tree(lang: str | None = None) -> list:
    """Árbol del índice. Si `lang` no es el original (es), los títulos se muestran en ese
    idioma con fallback: idioma pedido → inglés → original."""
    conn = get_conn()
    tmap, emap = {}, {}
    if lang and lang != "es":
        for r in conn.execute("SELECT node_id,title FROM wiki_translations WHERE lang=?", (lang,)).fetchall():
            tmap[r["node_id"]] = r["title"]
        if lang != "en":
            for r in conn.execute("SELECT node_id,title FROM wiki_translations WHERE lang='en'").fetchall():
                emap[r["node_id"]] = r["title"]

    def title_of(nid, orig):
        return tmap.get(nid) or emap.get(nid) or orig

    tops = conn.execute(
        "SELECT id,type,title FROM wiki_nodes WHERE parent_id IS NULL ORDER BY sort_order, id").fetchall()
    result, secnum = [], 0
    for t in tops:
        d = {"id": t["id"], "type": t["type"], "title": title_of(t["id"], t["title"])}
        if t["type"] == "section":
            secnum += 1
            d["number"] = secnum
            subs = conn.execute(
                "SELECT id,title FROM wiki_nodes WHERE parent_id=? ORDER BY sort_order, id", (t["id"],)).fetchall()
            d["subs"] = [{"id": s["id"], "title": title_of(s["id"], s["title"]), "number": f"{secnum}.{i + 1}"}
                         for i, s in enumerate(subs)]
        result.append(d)
    conn.close()
    return result


# --------------------------- Traducciones de la wiki (comunidad) ---------------------------

def wiki_translations_for(node_id: int) -> list:
    """Idiomas con traducción disponible para un nodo (para el selector de versiones)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT lang, title, updated_at FROM wiki_translations WHERE node_id=? ORDER BY lang", (node_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_wiki_translation(node_id: int, lang: str) -> dict | None:
    conn = get_conn()
    r = conn.execute("SELECT * FROM wiki_translations WHERE node_id=? AND lang=?", (node_id, lang)).fetchone()
    conn.close()
    return dict(r) if r else None


def wiki_upsert_translation(node_id: int, lang: str, title: str, body, user_id) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO wiki_translations (node_id,lang,title,body,translator_user_id,updated_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(node_id,lang) DO UPDATE SET "
        "title=excluded.title, body=excluded.body, translator_user_id=excluded.translator_user_id, "
        "updated_at=excluded.updated_at",
        (node_id, lang, title, body, user_id, datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()


def _wiki_tr_snapshot(tr: dict, lang: str, change_kind: str, by_user_id) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO wiki_history (node_id,type,title,body,parent_id,change_kind,by_user_id,changed_at,lang) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (tr["node_id"], "translation", tr["title"], tr.get("body"), None, change_kind, by_user_id,
         datetime.now(timezone.utc).isoformat(), lang))
    conn.commit(); conn.close()


def wiki_node_localized(nid: int, lang: str | None = None, view: str | None = None) -> dict | None:
    """Nodo listo para mostrar con fallback de idioma (o una versión concreta si `view`).
    `view`='orig' → original; `view`=<lang> → esa traducción; sin `view` → idioma→en→original."""
    node = get_wiki_node(nid)
    if not node:
        return None
    orig_lang = node.get("orig_lang") or "es"
    avail = [t["lang"] for t in wiki_translations_for(nid)]
    base = {"id": node["id"], "type": node["type"], "parent_id": node["parent_id"],
            "orig_lang": orig_lang, "available_langs": avail}

    def as_orig():
        return {**base, "title": node["title"], "body": node.get("body"),
                "shown_lang": orig_lang, "is_translation": False}

    def as_tr(code):
        tr = get_wiki_translation(nid, code)
        return {**base, "title": tr["title"], "body": tr.get("body"),
                "shown_lang": code, "is_translation": True}

    if view == "orig" or (view and view == orig_lang):
        return as_orig()
    if view and view in avail:
        return as_tr(view)
    lang = lang or orig_lang
    if lang != orig_lang:
        if lang in avail:
            return as_tr(lang)
        if lang != "en" and "en" in avail:
            return as_tr("en")
    return as_orig()


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
    conn.execute("DELETE FROM wiki_translations WHERE node_id=?", (nid,))  # y sus traducciones
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
    elif kind == "translate":
        lang = payload.get("lang")
        if p["node_id"] and lang and lang != "es":
            existing = get_wiki_translation(p["node_id"], lang)
            if existing:
                _wiki_tr_snapshot(existing, lang, "edit", p["author_user_id"])
            wiki_upsert_translation(p["node_id"], lang, payload.get("title", ""),
                                    payload.get("body", ""), p["author_user_id"])
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
    # Entrada de una traducción: restaura en wiki_translations (no en el nodo original).
    if h.get("type") == "translation" and (h.get("lang") or "es") != "es":
        lang = h["lang"]
        cur = get_wiki_translation(h["node_id"], lang)
        if cur:
            _wiki_tr_snapshot(cur, lang, "revert", by_user_id)
        wiki_upsert_translation(h["node_id"], lang, h["title"], h["body"], by_user_id)
        return True
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
        "SELECT id, username, is_admin, is_translator, role, status, email, is_croker, "
        "hidden, country, created_at FROM users ORDER BY username").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def user_orphan_player_tags(uid: int) -> list[str]:
    """Tags que este usuario trackea y que NO trackea ningún otro (quedarían huérfanos al borrarlo)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT player_tag FROM user_players up WHERE user_id=? AND NOT EXISTS "
        "(SELECT 1 FROM user_players o WHERE o.player_tag = up.player_tag AND o.user_id <> ?)",
        (uid, uid)).fetchall()
    conn.close()
    return [r["player_tag"] for r in rows]


def delete_player_data(tag: str) -> None:
    """Elimina POR COMPLETO del tracking a un jugador: su ficha, partidas, colección e informes.
    Usarlo con cuidado (acción destructiva). Los partidos de eventos no se tocan (son del evento)."""
    ntag = normalize_tag(tag)
    conn = get_conn()
    bids = [r["id"] for r in conn.execute("SELECT id FROM battles WHERE player_tag=?", (ntag,)).fetchall()]
    if bids:
        qs = ",".join("?" * len(bids))
        conn.execute(f"DELETE FROM opponents WHERE battle_id IN ({qs})", bids)
        conn.execute(f"DELETE FROM allies WHERE battle_id IN ({qs})", bids)
    for t in ("battles", "brawler_collection", "reports", "user_players", "players"):
        conn.execute(f"DELETE FROM {t} WHERE {'tag' if t == 'players' else 'player_tag'}=?", (ntag,))
    conn.commit(); conn.close()


def delete_user(uid: int, delete_players: bool = False) -> None:
    """Borra la cuenta. Por defecto CONSERVA los jugadores asociados en el tracking (solo se quita
    la asociación usuario↔jugador). Si `delete_players`, elimina además los que queden huérfanos."""
    orphans = user_orphan_player_tags(uid) if delete_players else []
    conn = get_conn()
    conn.execute("DELETE FROM user_players WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM user_custom_rankings WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM custom_rankings WHERE owner_user_id=?", (uid,))
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit(); conn.close()
    for tag in orphans:
        delete_player_data(tag)


def set_user_admin(uid: int, is_admin: bool) -> None:
    # Compatibilidad: alternar admin equivale a poner rol admin o degradar a user.
    set_user_role(uid, "admin" if is_admin else "user")


# Roles válidos y qué espejos (is_admin/is_translator) implica cada uno. Mantener
# alineado con app/rbac.py (que es la fuente de verdad de la lógica de permisos).
_ROLE_MIRRORS = {
    "root":         (1, 0),
    "admin":        (1, 0),
    "collaborator": (0, 1),  # colabora en traducciones → is_translator=1
    "translator":   (0, 1),
    "user":         (0, 0),
}


def set_user_role(uid: int, role: str) -> None:
    """Asigna el rol RBAC y sincroniza los flags espejo is_admin/is_translator.

    No aplica reglas de autorización (quién puede asignar qué): eso lo decide la
    capa de rbac.can_assign_role en el router antes de llamar aquí."""
    if role not in _ROLE_MIRRORS:
        role = "user"
    is_admin, is_translator = _ROLE_MIRRORS[role]
    conn = get_conn()
    conn.execute("UPDATE users SET role=?, is_admin=?, is_translator=? WHERE id=?",
                 (role, is_admin, is_translator, uid))
    conn.commit(); conn.close()


def set_user_status(uid: int, status: str) -> None:
    """active / pending / disabled."""
    if status not in ("active", "pending", "disabled"):
        return
    conn = get_conn()
    conn.execute("UPDATE users SET status=? WHERE id=?", (status, uid))
    conn.commit(); conn.close()


def set_user_croker(uid: int, val: bool) -> None:
    conn = get_conn()
    conn.execute("UPDATE users SET is_croker=? WHERE id=?", (1 if val else 0, uid))
    conn.commit(); conn.close()


# --- Jugador principal (identidad de la cuenta) + rol Croker automático -----------------
# El club que otorga el rol Croker (configurable). Se compara normalizado (sin mayúsculas
# ni símbolos), así aguanta decoraciones del nombre del club.
_CROKERS_MARK = "".join(ch for ch in os.environ.get("CROKERS_CLUB", "Crokers").lower() if ch.isalnum()) or "crokers"


def _is_crokers_club(club_name: str | None) -> bool:
    if not club_name:
        return False
    norm = "".join(ch for ch in club_name.lower() if ch.isalnum())
    return _CROKERS_MARK in norm


def get_main_player(uid: int) -> str | None:
    conn = get_conn()
    row = conn.execute("SELECT main_player_tag FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return row["main_player_tag"] if row and row["main_player_tag"] else None


def recompute_croker(uid: int) -> bool:
    """Recalcula is_croker del usuario a partir del CLUB de su jugador principal. Automático:
    si el principal está en el club Crokers → is_croker=1; si no (o no hay principal) → 0.
    Devuelve el nuevo valor booleano."""
    conn = get_conn()
    row = conn.execute(
        "SELECT p.club_name FROM users u LEFT JOIN players p ON p.tag = u.main_player_tag "
        "WHERE u.id=?", (uid,)).fetchone()
    val = 1 if (row and _is_crokers_club(row["club_name"])) else 0
    conn.execute("UPDATE users SET is_croker=? WHERE id=?", (val, uid))
    conn.commit(); conn.close()
    return bool(val)


def set_main_player(uid: int, tag: str) -> bool:
    """Declara el jugador principal de la cuenta. Debe ser un jugador que el usuario sigue.
    Recalcula el rol Croker según el club de ese jugador. Devuelve True si se aplicó."""
    ntag = normalize_tag(tag)
    conn = get_conn()
    owned = conn.execute(
        "SELECT 1 FROM user_players WHERE user_id=? AND player_tag=?", (uid, ntag)).fetchone()
    if not owned:
        conn.close()
        return False
    conn.execute("UPDATE users SET main_player_tag=? WHERE id=?", (ntag, uid))
    conn.commit(); conn.close()
    recompute_croker(uid)
    return True


def ensure_main_player(uid: int) -> str | None:
    """Mantiene coherente el jugador principal: (a) limpia uno OBSOLETO (que la cuenta ya no
    sigue), (b) si la cuenta tiene EXACTAMENTE un jugador y ningún principal válido, lo asigna
    solo (el primer jugador es el principal por defecto). Devuelve el principal resultante."""
    conn = get_conn()
    row = conn.execute("SELECT main_player_tag FROM users WHERE id=?", (uid,)).fetchone()
    if row is None:
        conn.close(); return None
    tags = [r["player_tag"] for r in conn.execute(
        "SELECT player_tag FROM user_players WHERE user_id=?", (uid,)).fetchall()]
    main = row["main_player_tag"]
    changed = False
    if main and main not in tags:          # el principal ya no está en la cuenta
        main = None; changed = True
    if not main and len(tags) == 1:        # un único jugador → es el principal
        main = tags[0]; changed = True
    if changed:
        conn.execute("UPDATE users SET main_player_tag=? WHERE id=?", (main, uid))
        conn.commit()
    conn.close()
    if changed and main:
        recompute_croker(uid)
    return main


def main_player_status(uid: int) -> dict:
    """Estado del jugador principal para la UI. `needs_main` es True si la cuenta tiene
    jugadores pero ninguno marcado como principal (y no se pudo autoasignar)."""
    main = ensure_main_player(uid)
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM user_players WHERE user_id=?", (uid,)).fetchone()[0]
    conn.close()
    return {"main_player_tag": main, "n_players": n, "needs_main": bool(main is None and n >= 1)}


def refresh_croker_for_player(tag: str) -> None:
    """Cuando cambia el club de un jugador (sondeo), recalcula el Croker de todo usuario cuyo
    jugador PRINCIPAL sea ese (efecto inmediato en el flag; los límites lo leerán de aquí)."""
    ntag = normalize_tag(tag)
    conn = get_conn()
    uids = [r["id"] for r in conn.execute(
        "SELECT id FROM users WHERE main_player_tag=?", (ntag,)).fetchall()]
    conn.close()
    for uid in uids:
        recompute_croker(uid)


def ensure_root(username: str) -> None:
    """Garantiza que el usuario dado sea root (control total). Idempotente. Se llama en el
    arranque DESPUÉS de crear la cuenta personal (init_db corre antes de que exista)."""
    if not username:
        return
    conn = get_conn()
    conn.execute("UPDATE users SET role='root', is_admin=1, is_translator=0, status='active' "
                 "WHERE username=?", (username,))
    conn.commit(); conn.close()


def set_user_hidden(uid: int, val: bool) -> None:
    """Cuenta de sistema: oculta del descubrimiento público (buscador/comunidad), pero
    sigue visible para los administradores en el panel."""
    conn = get_conn()
    conn.execute("UPDATE users SET hidden=? WHERE id=?", (1 if val else 0, uid))
    conn.commit(); conn.close()


def _ensure_ai_usage(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS ai_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, kind TEXT,
        input_tokens INTEGER, output_tokens INTEGER)""")
    # Modelo usado (Sensei multi-modelo): permite calcular el coste por modelo (Opus cuesta más).
    try:
        conn.execute("ALTER TABLE ai_usage ADD COLUMN model TEXT")
    except Exception:  # noqa: BLE001
        pass  # ya existe


def log_ai_usage(kind: str, input_tokens: int, output_tokens: int, model: str | None = None) -> None:
    """Registra el consumo de tokens de una llamada a la IA (para métricas de admin). Guarda
    el modelo para poder valorar el coste real (Sonnet vs Opus)."""
    conn = get_conn()
    _ensure_ai_usage(conn)
    conn.execute("INSERT INTO ai_usage (at, kind, input_tokens, output_tokens, model) VALUES (?,?,?,?,?)",
                 (datetime.now(timezone.utc).isoformat(), kind, int(input_tokens or 0), int(output_tokens or 0), model))
    conn.commit(); conn.close()


# Tarifas de la API de IA por MILLÓN de tokens (USD): (entrada, salida). La salida = 5× la entrada.
_AI_PRICES = {"haiku": (1.0, 5.0), "sonnet": (3.0, 15.0), "opus": (5.0, 25.0)}
_USD_TO_EUR = 0.92      # conversión aproximada USD -> EUR


def _ai_price(model: str):
    """(precio_entrada, precio_salida) por millón de tokens según el modelo."""
    m = (model or "").lower()
    for k, v in _AI_PRICES.items():
        if k in m:
            return v
    return _AI_PRICES["sonnet"]


def admin_metrics(model: str = "claude-sonnet-4-6") -> dict:
    """Métricas globales del panel de admin: usuarios, jugadores, partidas, informes
    y consumo de IA (tokens entrada/salida + coste estimado en € según el modelo)."""
    conn = get_conn()
    _ensure_ai_usage(conn)

    def one(q, p=()):
        return conn.execute(q, p).fetchone()

    users = one("SELECT COUNT(*) FROM users")[0]
    players = one("SELECT COUNT(*) FROM players")[0]
    active = one("SELECT COUNT(*) FROM players WHERE active=1")[0]
    orphans = one("SELECT COUNT(*) FROM players p WHERE NOT EXISTS "
                  "(SELECT 1 FROM user_players up WHERE up.player_tag=p.tag)")[0]
    battles = one("SELECT COUNT(*) FROM battles")[0]
    try:
        reports = one("SELECT COUNT(*) FROM reports")[0]
    except Exception:  # noqa: BLE001
        reports = 0
    now = datetime.now(timezone.utc)

    p_in, p_out = _ai_price(model)

    def toks(cutoff=None):
        # Coste POR MODELO: cada familia (sonnet/opus/haiku) tiene su precio; filas antiguas sin
        # modelo se valoran con el modelo por defecto. Así el coste es correcto con multi-modelo.
        where = " WHERE at>=?" if cutoff is not None else ""
        params = (cutoff.isoformat(),) if cutoff is not None else ()
        rows = conn.execute(
            "SELECT model, COALESCE(SUM(input_tokens),0) AS inp, COALESCE(SUM(output_tokens),0) AS out, "
            "COUNT(*) AS n FROM ai_usage" + where + " GROUP BY model", params).fetchall()
        inp = out = n = 0
        cost_usd = 0.0
        for r in rows:
            pi, po = _ai_price(r["model"] or model)
            cost_usd += r["inp"] / 1_000_000 * pi + r["out"] / 1_000_000 * po
            inp += r["inp"]; out += r["out"]; n += r["n"]
        return {"input": inp, "output": out, "tokens": inp + out, "requests": n,
                "cost_eur": round(cost_usd * _USD_TO_EUR, 4)}

    ai = {"total": toks(), "month": toks(now - timedelta(days=30)),
          "week": toks(now - timedelta(days=7)), "day": toks(now - timedelta(days=1)),
          "model": model, "price_in_eur": round(p_in * _USD_TO_EUR, 2),
          "price_out_eur": round(p_out * _USD_TO_EUR, 2)}
    conn.close()
    return {"users": users, "players": players, "active_players": active, "orphans": orphans,
            "battles": battles, "reports": reports, "ai": ai}


def follow_player(user_id: int, tag: str) -> None:
    ntag = normalize_tag(tag)
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO user_players (user_id, player_tag, added_at) VALUES (?,?,?)",
        (user_id, ntag, datetime.now(timezone.utc).isoformat()),
    )
    # Si el usuario aún no tiene jugador principal, este pasa a serlo (su identidad).
    row = conn.execute("SELECT main_player_tag FROM users WHERE id=?", (user_id,)).fetchone()
    set_main = row is not None and not (row["main_player_tag"])
    if set_main:
        conn.execute("UPDATE users SET main_player_tag=? WHERE id=?", (ntag, user_id))
    conn.commit(); conn.close()
    if set_main:
        recompute_croker(user_id)


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
    main = conn.execute("SELECT main_player_tag FROM users WHERE id=?", (user_id,)).fetchone()
    main_tag = main["main_player_tag"] if main else None
    rows = conn.execute(
        """SELECT p.tag, p.name, p.added_at, p.last_polled, p.active, p.icon_id, p.club_name, p.club_tag,
                  (SELECT COUNT(*) FROM battles b WHERE b.player_tag = p.tag) AS battles
           FROM players p JOIN user_players up ON up.player_tag = p.tag
           WHERE up.user_id = ? ORDER BY up.added_at""",
        (user_id,),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["is_main"] = (main_tag is not None and d["tag"] == main_tag)
        out.append(d)
    # El jugador principal primero (es la identidad; el perfil público lo muestra por defecto).
    out.sort(key=lambda x: (not x["is_main"],))
    return out


def _rank_community(players: list, limit: int) -> list:
    """Selección + orden del ranking comunitario en DOS fases:
    1) INCLUSIÓN: entran TODOS los jugadores principales; si no llenan el tope, se rellenan los
       huecos con los MEJORES secundarios (por trofeos). Con principales de sobra, no entran
       secundarios aunque tengan más trofeos.
    2) ORDEN: los incluidos se ordenan TODOS por trofeos (NO en bloques principal/secundario:
       un secundario con más trofeos que un principal aparece por encima)."""
    players = [p for p in players if (p.get("trophies") or 0) > 0]
    mains = sorted([p for p in players if p["is_main"]], key=lambda x: -x["trophies"])
    secs = sorted([p for p in players if not p["is_main"]], key=lambda x: -x["trophies"])
    pop = mains[:limit] if len(mains) >= limit else mains + secs[:max(0, limit - len(mains))]
    pop.sort(key=lambda x: -x["trophies"])
    return pop


def _community_out(pop: list) -> list[dict]:
    return [{"rank": i + 1, "tag": p["tag"], "name": p["name"], "icon_id": p["icon_id"],
             "trophies": p["trophies"], "club": p["club_name"],
             "is_secondary": not bool(p["is_main"])} for i, p in enumerate(pop)]


def community_ranking(limit: int = 200) -> list[dict]:
    """Ranking COMUNITARIO por trofeos totales (suma de la colección). Los principales llenan
    la lista; los huecos se rellenan con los mejores secundarios. NUNCA huérfanos. El orden
    final es por trofeos (cada uno en su sitio, no en bloques)."""
    ck = ("community_ranking", limit)
    cached = _agg_get(ck)
    if cached is not None:
        return cached
    conn = get_conn()
    rows = conn.execute(
        """SELECT p.tag, p.name, p.icon_id, p.club_name,
                  (SELECT COALESCE(SUM(bc.trophies),0) FROM brawler_collection bc WHERE bc.player_tag=p.tag) AS trophies,
                  EXISTS(SELECT 1 FROM users u WHERE u.main_player_tag=p.tag AND COALESCE(u.hidden,0)=0) AS is_main
           FROM players p
           WHERE EXISTS(SELECT 1 FROM user_players up JOIN users u ON u.id=up.user_id
                        WHERE up.player_tag=p.tag AND COALESCE(u.hidden,0)=0)""").fetchall()
    conn.close()
    return _agg_put(ck, _community_out(_rank_community([dict(r) for r in rows], limit)), 300)


def community_brawler_ranking(brawler_id: int, limit: int = 200) -> list[dict]:
    """Ranking COMUNITARIO de un brawler concreto por sus trofeos EN ESE brawler. Misma regla:
    principales llenan la lista, huecos con los mejores secundarios, y orden final por trofeos."""
    ck = ("community_brawler_ranking", brawler_id, limit)
    cached = _agg_get(ck)
    if cached is not None:
        return cached
    conn = get_conn()
    rows = conn.execute(
        """SELECT p.tag, p.name, p.icon_id, p.club_name, bc.trophies AS trophies,
                  EXISTS(SELECT 1 FROM users u WHERE u.main_player_tag=p.tag AND COALESCE(u.hidden,0)=0) AS is_main
           FROM players p JOIN brawler_collection bc ON bc.player_tag=p.tag
           WHERE bc.brawler_id=? AND bc.trophies>0
             AND EXISTS(SELECT 1 FROM user_players up JOIN users u ON u.id=up.user_id
                        WHERE up.player_tag=p.tag AND COALESCE(u.hidden,0)=0)""",
        (brawler_id,)).fetchall()
    conn.close()
    return _agg_put(ck, _community_out(_rank_community([dict(r) for r in rows], limit)), 300)


def community_clubs_ranking(limit: int = 200) -> list[dict]:
    """Ranking COMUNITARIO de clubs: agrupa por club a los jugadores de la comunidad (no
    huérfanos) y los ordena por trofeos totales de sus miembros de la plataforma."""
    ck = ("community_clubs_ranking", limit)
    cached = _agg_get(ck)
    if cached is not None:
        return cached
    conn = get_conn()
    rows = conn.execute(
        """SELECT club_name AS name, MAX(club_tag) AS tag, COUNT(*) AS members, SUM(trophies) AS trophies FROM (
             SELECT p.club_name, p.club_tag,
                    (SELECT COALESCE(SUM(bc.trophies),0) FROM brawler_collection bc WHERE bc.player_tag=p.tag) AS trophies
             FROM players p
             WHERE EXISTS(SELECT 1 FROM user_players up JOIN users u ON u.id=up.user_id
                          WHERE up.player_tag=p.tag AND COALESCE(u.hidden,0)=0)
               AND p.club_name IS NOT NULL AND p.club_name<>''
           ) WHERE trophies > 0
           GROUP BY name ORDER BY trophies DESC LIMIT ?""", (limit,)).fetchall()
    conn.close()
    return _agg_put(ck, [{"rank": i + 1, "name": r["name"], "tag": r["tag"], "members": r["members"],
                          "trophies": r["trophies"]} for i, r in enumerate(rows)], 300)


# --- Páginas de club (descripción editable por miembros) + descubrimiento ---------------

def _is_meaningful_description(text) -> bool:
    """Heurística: una descripción 'de verdad' (no vacía ni una cadena sin sentido)."""
    t = (text or "").strip()
    if len(t) < 20:
        return False
    if len([w for w in t.split() if w]) < 3:
        return False
    return len(set(t.replace(" ", "").lower())) >= 5   # variedad de caracteres


def player_club_tag(tag: str) -> str | None:
    conn = get_conn()
    row = conn.execute("SELECT club_tag FROM players WHERE tag=?", (normalize_tag(tag),)).fetchone()
    conn.close()
    return row["club_tag"] if row and row["club_tag"] else None


def get_club_page(club_tag: str) -> dict:
    ctag = normalize_tag(club_tag)
    conn = get_conn()
    row = conn.execute("SELECT * FROM club_pages WHERE club_tag=?", (ctag,)).fetchone()
    editors = [r["player_tag"] for r in conn.execute(
        "SELECT player_tag FROM club_editors WHERE club_tag=?", (ctag,)).fetchall()]
    conn.close()
    if not row:
        return {"club_tag": ctag, "name": None, "description": "", "edit_policy": "members",
                "updated_at": None, "editors": editors}
    d = dict(row); d["editors"] = editors; d["description"] = d.get("description") or ""
    return d


def set_club_description(club_tag: str, name: str | None, description: str, user_id: int) -> None:
    ctag = normalize_tag(club_tag)
    conn = get_conn()
    conn.execute(
        "INSERT INTO club_pages (club_tag, name, description, updated_at, updated_by) VALUES (?,?,?,?,?) "
        "ON CONFLICT(club_tag) DO UPDATE SET description=excluded.description, "
        "name=COALESCE(excluded.name, club_pages.name), updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (ctag, name, (description or "").strip(), datetime.now(timezone.utc).isoformat(), user_id))
    conn.commit(); conn.close()


def set_club_edit_policy(club_tag: str, name: str | None, policy: str) -> None:
    if policy not in ("members", "managers"):
        policy = "members"
    ctag = normalize_tag(club_tag)
    conn = get_conn()
    conn.execute(
        "INSERT INTO club_pages (club_tag, name, edit_policy, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(club_tag) DO UPDATE SET edit_policy=excluded.edit_policy, "
        "name=COALESCE(excluded.name, club_pages.name)",
        (ctag, name, policy, datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()


def set_club_editor(club_tag: str, player_tag: str, granted: bool) -> None:
    ctag, ptag = normalize_tag(club_tag), normalize_tag(player_tag)
    conn = get_conn()
    if granted:
        conn.execute("INSERT OR IGNORE INTO club_editors (club_tag, player_tag) VALUES (?,?)", (ctag, ptag))
    else:
        conn.execute("DELETE FROM club_editors WHERE club_tag=? AND player_tag=?", (ctag, ptag))
    conn.commit(); conn.close()


def list_community_clubs(q: str | None = None, limit: int = 60) -> list[dict]:
    """Clubs de los usuarios de la plataforma (por club de sus jugadores no huérfanos),
    con prioridad a los que tienen una descripción REAL editada."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT p.club_tag AS tag, MAX(p.club_name) AS name, COUNT(DISTINCT p.tag) AS members
           FROM players p
           WHERE EXISTS(SELECT 1 FROM user_players up WHERE up.player_tag=p.tag)
             AND p.club_tag IS NOT NULL AND p.club_tag<>''
           GROUP BY p.club_tag""").fetchall()
    pages = {r["club_tag"]: r["description"] for r in conn.execute(
        "SELECT club_tag, description FROM club_pages").fetchall()}
    conn.close()
    qq = (q or "").strip().lower()
    out = []
    for r in rows:
        name = r["name"] or ""
        if qq and qq not in name.lower():
            continue
        desc = pages.get(r["tag"])
        meaningful = _is_meaningful_description(desc)
        out.append({"tag": r["tag"], "name": name, "members": r["members"],
                    "has_description": meaningful,
                    "description": (desc.strip()[:160] if meaningful else "")})
    out.sort(key=lambda c: (not c["has_description"], -c["members"], c["name"].lower()))
    return out[:limit]


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


def clear_user_players(user_id: int) -> int:
    """Deja a un usuario SIN jugadores seguidos y devuelve cuántos vínculos borró. Se usa con
    la cuenta de sistema `tester`: no debe 'adoptar' jugadores huérfanos (los que crea la
    búsqueda pública de invitados), porque entonces saldrían en el ranking de la comunidad."""
    conn = get_conn()
    cur = conn.execute("DELETE FROM user_players WHERE user_id=?", (user_id,))
    n = cur.rowcount
    conn.commit(); conn.close()
    return n


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


def player_friendly_battles(tag: str, since: str = None, until: str = None) -> list[dict]:
    """Amistosas ALMACENADAS de un jugador en la ventana [since, until] (formato battle_time).
    Para la detección automática de resultados de eventos: se cruzan por hora los registros de
    los participantes. Deduplica por hora (una fila por partida)."""
    conn = get_conn()
    clauses = ["player_tag = ?", "battle_type = 'friendly'"]
    params = [normalize_tag(tag)]
    if since:
        clauses.append("battle_time >= ?"); params.append(since)
    if until:
        clauses.append("battle_time <= ?"); params.append(until)
    rows = conn.execute(
        f"SELECT battle_time, mode, map, result FROM battles WHERE {' AND '.join(clauses)} "
        f"ORDER BY battle_time", params).fetchall()
    conn.close()
    seen, out = set(), []
    for r in rows:
        if r["battle_time"] in seen:
            continue
        seen.add(r["battle_time"])
        out.append({"time": r["battle_time"], "mode": r["mode"], "map": r["map"],
                    "result": (r["result"] or "").lower()})
    return out


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

    def brawlers_of(p):
        """(nombre, trofeos) de los brawlers del jugador. En DUELOS cada jugador usa VARIOS
        (campo 'brawlers'); en el resto, uno solo ('brawler')."""
        b = p.get("brawler")
        if b:
            return [(b.get("name"), b.get("trophies"))]
        bl = p.get("brawlers")
        if isinstance(bl, list):
            return [(x.get("name"), x.get("trophies")) for x in bl if isinstance(x, dict)]
        return []

    teams = battle.get("teams")
    players = battle.get("players")

    if teams:
        for ti, team in enumerate(teams):
            for p in team:
                if normalize_tag(p.get("tag", "")) == norm_me:
                    my_team_idx = ti
        for ti, team in enumerate(teams):
            for p in team:
                bl = brawlers_of(p)
                if normalize_tag(p.get("tag", "")) == norm_me:
                    if bl:
                        my_brawler, my_trophies = bl[0]
                        allies.extend(bl[1:])   # Duelos: mis otros brawlers a "mi equipo"
                else:
                    dest = allies if (my_team_idx is not None and ti == my_team_idx) else opponents
                    dest.extend(bl)
    elif players:   # Duelos y modos sin bandos explícitos: lista plana de jugadores
        for p in players:
            bl = brawlers_of(p)
            if normalize_tag(p.get("tag", "")) == norm_me:
                if bl:
                    my_brawler, my_trophies = bl[0]
                    allies.extend(bl[1:])
            else:
                opponents.extend(bl)

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
            if b["my_brawler"] is not None:
                # Sustituye una versión previa ROTA (sin brawler) de esta misma batalla:
                # p. ej. un Duelos guardado antes de arreglar el parseo tenía my_brawler
                # NULL -> otro id. Mismo jugador y momento => la borramos para no duplicar.
                for s in cur.execute(
                    "SELECT id FROM battles WHERE player_tag=? AND battle_time=? AND my_brawler IS NULL",
                    (b["player_tag"], b["battle_time"])).fetchall():
                    cur.execute("DELETE FROM opponents WHERE battle_id=?", (s["id"],))
                    cur.execute("DELETE FROM allies WHERE battle_id=?", (s["id"],))
                    cur.execute("DELETE FROM manual_stats WHERE battle_id=?", (s["id"],))
                    cur.execute("DELETE FROM battles WHERE id=?", (s["id"],))
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


def _multi(val) -> list:
    """Normaliza un filtro multi-valor (None | 'a,b' | ['a','b']) a lista de strings."""
    if val is None:
        return []
    if isinstance(val, str):
        return [v.strip() for v in val.split(",") if v.strip()]
    return [str(v).strip() for v in val if str(v).strip()]


def _in(col: str, vals: list):
    """(sql, params) para `col IN (...)`. ('', []) si no hay valores."""
    if not vals:
        return "", []
    return f"{col} IN ({','.join('?' * len(vals))})", list(vals)


def _role_in(filters: dict, col: str = "my_brawler"):
    """Cláusula para filtrar por uno o varios roles: col IN (unión de brawlers que
    tienen alguno de esos roles, primario o secundario)."""
    roles = _multi(filters.get("role"))
    if not roles:
        return None, []
    names = set()
    for r in roles:
        names.update(brawler_extra.brawlers_with_role(r))
    if not names:
        return "1=0", []
    names = sorted(names)
    return f"{col} IN ({','.join('?' * len(names))})", names


def _build_filters(filters: dict):
    where, params = [], []
    if filters.get("player"):
        where.append("player_tag = ?"); params.append(normalize_tag(filters["player"]))
    for col, key in (("mode", "mode"), ("map", "map"), ("my_brawler", "brawler")):
        sql, p = _in(col, _multi(filters.get(key)))
        if sql:
            where.append(sql); params.extend(p)
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


def _adjusted_score(wins, losses, avg_trophies, avg_rival, base,
                    bmax=15.0, scale=400.0, rival_w=0.4, k=10.0):
    """Rendimiento AJUSTADO (modelo ADITIVO, estable):
    1. **Win rate ENCOGIDO** hacia 50% según el nº de partidas (k = partidas 'a priori'): pocas
       partidas tiran al 50% (un 3-0 NO es un 100% real) sin ser barrera de entrada.
    2. **+ ajuste por DIFICULTAD** (±bmax pts, suave con tanh): el NIVEL del brawler (sus copas
       vs tu media, `(avg_trophies-base)/scale`) MANDA, y el desnivel frente a los rivales
       (`(avg_rival-avg_trophies)/scale`) es un ajuste MENOR (peso `rival_w`).
    Así rendir a copas altas / contra rivales fuertes SUBE el dato (aunque el win rate baje por
    la dificultad), y a copas bajas baja. El win rate sigue mandando."""
    decided = (wins or 0) + (losses or 0)
    if decided <= 0:
        return None
    import math
    shrunk = 100.0 * (wins + k * 0.5) / (decided + k)
    diff = 0.0
    if avg_trophies and base:
        diff = (avg_trophies - base) / scale
    if avg_trophies and avg_rival:
        diff += rival_w * (avg_rival - avg_trophies) / scale
    return round(max(0.0, min(100.0, shrunk + bmax * math.tanh(diff))), 1)


def _reliability(wins, losses, k=10.0):
    """Fiabilidad del dato (0-100) según el tamaño de muestra: decided/(decided+k). Es el peso
    que tiene tu win rate real frente al 50% a priori del encogimiento (más partidas = más fiable)."""
    decided = (wins or 0) + (losses or 0)
    return round(100.0 * decided / (decided + k)) if decided else 0


def _shrunk_winrate(wins, losses, k=10.0):
    """Win rate ENCOGIDO hacia 50% según el nº de partidas (misma idea que `_adjusted_score`
    pero SIN el ajuste por dificultad de trofeos, que solo tiene sentido por brawler). Sirve para
    modos/mapas/roles: un 75% con 3 partidas se acerca al 50% (poco fiable) mientras que con 20
    partidas apenas se mueve (tendencia real). k = partidas 'a priori' (peso del 50%)."""
    decided = (wins or 0) + (losses or 0)
    if decided <= 0:
        return None
    return round(100.0 * (wins + k * 0.5) / (decided + k), 1)


def winrate_by(dimension: str, filters: dict | None = None) -> list[dict]:
    col = GROUP_COLUMNS.get(dimension)
    if not col:
        raise ValueError(f"Dimensión no válida: {dimension}")
    filters = filters or {}
    where_sql, params = _build_filters(filters)
    conn = get_conn()
    # Nivel de referencia del jugador: media de copas de brawler (= base para la dificultad).
    base = conn.execute(f"SELECT AVG(CAST(my_trophies AS REAL)) FROM battles {where_sql}",
                        params).fetchone()[0] or 0
    rows = conn.execute(
        f"""
        SELECT {col} AS label,
               SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN is_win=0 THEN 1 ELSE 0 END) AS losses,
               SUM(CASE WHEN is_win IS NULL THEN 1 ELSE 0 END) AS undecided,
               SUM(CASE WHEN is_star_player=1 THEN 1 ELSE 0 END) AS star_players,
               SUM(CASE WHEN result IS NOT NULL THEN 1 ELSE 0 END) AS star_eligible,
               COUNT(*) AS total, SUM(COALESCE(trophy_change,0)) AS trophy_delta,
               AVG(CAST(my_trophies AS REAL)) AS avg_trophies,
               AVG(o.rt) AS avg_rival
        FROM battles
        LEFT JOIN (SELECT battle_id, AVG(CAST(trophies AS REAL)) AS rt
                   FROM opponents GROUP BY battle_id) o ON o.battle_id = battles.id
        {where_sql}
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
                    "trophy_delta": r["trophy_delta"],
                    "avg_trophies": round(r["avg_trophies"]) if r["avg_trophies"] else None,
                    "adj_score": _adjusted_score(r["wins"], r["losses"], r["avg_trophies"],
                                                 r["avg_rival"], base),
                    "shrunk_score": _shrunk_winrate(r["wins"], r["losses"]),
                    "reliability": _reliability(r["wins"], r["losses"])})
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
            "shrunk_score": _shrunk_winrate(a["wins"], a["losses"]),
            "reliability": _reliability(a["wins"], a["losses"]),
            "usage_pct": round(100 * a["total"] / grand, 1)} for role, a in agg.items()]
    out.sort(key=lambda r: r["total"], reverse=True)
    return out


def _summarize_reliability(key: str, label: str, unit: str, rows: list) -> dict:
    """Resume la fiabilidad de una dimensión (brawler/modo/mapa/rol) a partir de sus segmentos.

    Fiabilidad de la dimensión = MEDIA SIMPLE de la fiabilidad de cada segmento (cada área cuenta
    IGUAL). A propósito NO se pondera por nº de partidas: si se ponderara, tus segmentos con
    muchas partidas (tus mains) dominarían la media y ocultarían que muchas áreas tienen muestra
    pobre (p. ej. 6 modos pobres de 13 no deben quedar tapados por 3 muy jugados). Así, cuantos
    más segmentos pobres haya, más baja la fiabilidad de la dimensión, que es lo honesto.

    La fiabilidad POR SEGMENTO (`_reliability`) ya es absoluta —`decided/(decided+k)`, según el
    nº real de partidas de ESE segmento, no su fracción sobre el total del jugador—: 5 partidas
    son muestra pobre aunque sean casi todo tu registro; ~30-40 empiezan a ser tendencia real."""
    segs = [r for r in rows if (r.get("total") or 0) > 0 and r.get("reliability") is not None]
    if not segs:
        return {"key": key, "label": label, "unit": unit, "reliability": 0, "segments": 0,
                "green": 0, "yellow": 0, "red": 0, "weak": [], "strong": []}
    avg = sum(r["reliability"] for r in segs) / len(segs)   # media simple (sin ponderar)
    green = sum(1 for r in segs if r["reliability"] > 75)
    yellow = sum(1 for r in segs if 40 <= r["reliability"] <= 75)
    red = sum(1 for r in segs if r["reliability"] < 40)

    def item(r):
        return {"name": r["label"], "total": r["total"],
                "reliability": r["reliability"], "winrate": r.get("winrate")}
    weak = [item(r) for r in sorted(segs, key=lambda r: (r["reliability"], -r["total"]))[:6]]
    strong = [item(r) for r in sorted(segs, key=lambda r: (-r["reliability"], -r["total"]))[:6]]
    return {"key": key, "label": label, "unit": unit, "reliability": round(avg),
            "segments": len(segs), "green": green, "yellow": yellow, "red": red,
            "weak": weak, "strong": strong}


def _reliability_tips(dims: list, overall: int) -> list:
    active = [d for d in dims if d["segments"] > 0]
    if not active:
        return ["Aún no hay partidas registradas. Deja el tracker corriendo mientras juegas para "
                "empezar a acumular datos."]
    weakest = min(active, key=lambda d: d["reliability"])
    strongest = max(active, key=lambda d: d["reliability"])
    tips = []
    if overall >= 75:
        tips.append(f"Tus datos globales son sólidos ({overall}% de fiabilidad media): las "
                    f"tendencias que ves son representativas de cómo juegas.")
    elif overall >= 40:
        tips.append(f"Fiabilidad media ({overall}%): las tendencias generales son orientativas, "
                    f"pero afina antes de sacar conclusiones en las áreas con pocos datos.")
    else:
        tips.append(f"Fiabilidad baja ({overall}%): todavía hay pocas partidas, así que muchos "
                    f"porcentajes pueden cambiar bastante. Juega más para consolidarlos.")
    tips.append(f"El área con menos fiabilidad son los {weakest['label'].lower()} "
                f"({weakest['reliability']}%): {weakest['red']} con muy pocas partidas. Sus "
                f"estadísticas son las más sensibles a errores o sesgo por falta de evidencia.")
    br = next((d for d in dims if d["key"] == "brawler"), None)
    if br and br["red"]:
        tips.append(f"Tienes {br['red']} brawler(s) con datos flojos (menos de ~7 partidas "
                    f"decididas): juega algunas más con ellos para que su win rate sea fiable.")
    tips.append(f"Donde más puedes confiar es en {strongest['label'].lower()} "
                f"({strongest['reliability']}%): ahí tienes muestra de sobra.")
    return tips[:4]


def reliability_report(filters: dict | None = None) -> dict:
    """Informe de FIABILIDAD de los datos del jugador (según el tamaño de muestra). Devuelve una
    fiabilidad GLOBAL (0-100) y el desglose por dimensiones (brawlers, modos, mapas, roles) con
    qué áreas están bien cubiertas y cuáles son pobres en datos, más consejos para mejorarla."""
    filters = filters or {}
    dims = [
        _summarize_reliability("brawler", "Brawlers", "brawler", winrate_by("brawler", filters)),
        _summarize_reliability("mode", "Modos de juego", "modo", winrate_by("mode", filters)),
        _summarize_reliability("map", "Mapas", "mapa", winrate_by("map", filters)),
        _summarize_reliability("role", "Roles", "rol", winrate_by_role(filters)),
    ]
    active = [d for d in dims if d["segments"] > 0]
    overall = round(sum(d["reliability"] for d in active) / len(active)) if active else 0
    return {"overall": overall, "dimensions": dims, "tips": _reliability_tips(dims, overall)}


def role_winrates_by_map(mode: str) -> dict:
    """Para cada mapa del modo, win rate por ROL con TODAS las partidas (meta comunitario).
    Una sola consulta (agrupa por mapa+brawler) y roll-up a roles en Python (cada brawler
    aporta a su rol primario y secundario). Devuelve {mapa_en_minúsculas: [{role, winrate,
    total}, ...]} ordenado por win rate desc. Pensado para poner el mejor rol en cada mapa."""
    where_sql, params = _build_filters({"mode": mode})
    conn = get_conn()
    rows = conn.execute(
        f"""SELECT map AS mp, my_brawler AS brawler,
                   SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN is_win=0 THEN 1 ELSE 0 END) AS losses,
                   COUNT(*) AS total
            FROM battles {where_sql}
            GROUP BY map, my_brawler""",
        params).fetchall()
    conn.close()
    per_map: dict = {}
    for r in rows:
        if not r["mp"] or not r["brawler"]:
            continue
        for role in brawler_extra.roles_of(r["brawler"]):
            a = per_map.setdefault(r["mp"].lower(), {}).setdefault(
                role, {"wins": 0, "losses": 0, "total": 0})
            a["wins"] += r["wins"] or 0
            a["losses"] += r["losses"] or 0
            a["total"] += r["total"] or 0
    out: dict = {}
    for mp, roles in per_map.items():
        lst = [{"role": role, "winrate": _winrate(a["wins"], a["losses"]), "total": a["total"]}
               for role, a in roles.items() if a["total"] >= 2]
        lst.sort(key=lambda x: ((x["winrate"] if x["winrate"] is not None else -1), x["total"]), reverse=True)
        out[mp] = lst
    return out


def winrate_vs(filters: dict | None = None) -> list[dict]:
    filters = filters or {}
    where, params = [], []
    if filters.get("player"):
        where.append("b.player_tag = ?"); params.append(normalize_tag(filters["player"]))
    for col, key in (("b.mode", "mode"), ("b.map", "map"), ("b.my_brawler", "brawler")):
        sql, p = _in(col, _multi(filters.get(key)))
        if sql:
            where.append(sql); params.extend(p)
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
    ck = ("community_meta", mode, map_)
    cached = _agg_get(ck)
    if cached is not None:
        return cached
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
    return _agg_put(ck, {"total": total, "winrate": _winrate(tw, tl), "brawlers": brawlers}, 600)


def brawler_scene(brawler_name: str, player_tag: str | None = None) -> dict:
    """Para un brawler: su rendimiento por MODO y por MAPA en la COMUNIDAD (todas las partidas de
    todos con ese brawler) + el rendimiento del JUGADOR con ese brawler en cada modo/mapa, con
    win rate encogido (shrinkage) y fiabilidad. Base de datos de 'Mejores Modos/Mapas' de la ficha."""
    name = (brawler_name or "").upper()
    conn = get_conn()

    def agg(sql, params):
        return {r[0]: {"wins": r[1] or 0, "losses": r[2] or 0, "games": r[3] or 0,
                       "modes": (r[4] if len(r.keys()) > 4 else None)}
                for r in conn.execute(sql, params).fetchall() if r[0]}

    comm_mode = agg("SELECT mode, SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN is_win=0 THEN 1 ELSE 0 END), COUNT(*) FROM battles "
                    "WHERE UPPER(my_brawler)=? AND mode IS NOT NULL AND mode<>'unknown' GROUP BY mode", (name,))
    your_mode = agg("SELECT mode, SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN is_win=0 THEN 1 ELSE 0 END), COUNT(*) FROM battles "
                    "WHERE player_tag=? AND UPPER(my_brawler)=? AND mode IS NOT NULL AND mode<>'unknown' "
                    "GROUP BY mode", (normalize_tag(player_tag or ""), name)) if player_tag else {}
    # Mapas: agregamos por (mapa) y recogemos el conjunto de modos en los que aparece.
    comm_map, map_modes = {}, {}
    for r in conn.execute(
            "SELECT map, mode, SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN is_win=0 THEN 1 ELSE 0 END), COUNT(*) FROM battles "
            "WHERE UPPER(my_brawler)=? AND map IS NOT NULL AND map<>'unknown' GROUP BY map, mode", (name,)):
        mp = r[0]
        d = comm_map.setdefault(mp, {"wins": 0, "losses": 0, "games": 0})
        d["wins"] += r[2] or 0; d["losses"] += r[3] or 0; d["games"] += r[4] or 0
        if r[1] and r[1] != "unknown":
            map_modes.setdefault(mp, set()).add(r[1])
    your_map = {}
    if player_tag:
        for r in conn.execute(
                "SELECT map, SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN is_win=0 THEN 1 ELSE 0 END), COUNT(*) FROM battles "
                "WHERE player_tag=? AND UPPER(my_brawler)=? AND map IS NOT NULL AND map<>'unknown' GROUP BY map",
                (normalize_tag(player_tag), name)):
            your_map[r[0]] = {"wins": r[1] or 0, "losses": r[2] or 0, "games": r[3] or 0}
    conn.close()

    def stat(d):
        if not d:
            return {"winrate": None, "shrunk": None, "reliability": 0, "games": 0}
        return {"winrate": _winrate(d["wins"], d["losses"]),
                "shrunk": _shrunk_winrate(d["wins"], d["losses"]),
                "reliability": _reliability(d["wins"], d["losses"]), "games": d["games"]}

    modes = []
    for m, cd in comm_mode.items():
        cs, ys = stat(cd), stat(your_mode.get(m))
        modes.append({"mode": m, "community": cs, "your": ys})
    # Mejores modos del brawler (comunidad): por win rate encogido (mín. muestra), top 3.
    best_modes = sorted([x for x in modes if x["community"]["shrunk"] is not None and x["community"]["games"] >= 8],
                        key=lambda x: x["community"]["shrunk"], reverse=True)[:3]
    # Inesperados: el brawler rinde flojo en la comunidad en ese modo (<48 encogido) pero TÚ rindes
    # bien (>=58 encogido) con muestra decente (fiab>=40). Top 3 por diferencia a tu favor.
    unexpected = [x for x in modes if x["community"]["shrunk"] is not None and x["community"]["shrunk"] < 48
                  and x["your"]["shrunk"] is not None and x["your"]["shrunk"] >= 58 and x["your"]["reliability"] >= 40]
    unexpected.sort(key=lambda x: (x["your"]["shrunk"] - x["community"]["shrunk"]), reverse=True)
    unexpected = unexpected[:3]

    maps = []
    for mp, cd in comm_map.items():
        cs, ys = stat(cd), stat(your_map.get(mp))
        maps.append({"map": mp, "modes": sorted(map_modes.get(mp, [])),
                     "community": cs, "your": ys})
    # Top 20 mapas: prioriza rendimiento comunitario (encogido) y, a igualdad, tu rendimiento.
    maps = [x for x in maps if x["community"]["shrunk"] is not None and x["community"]["games"] >= 5]
    maps.sort(key=lambda x: (x["community"]["shrunk"], x["your"]["shrunk"] or 0), reverse=True)
    maps = maps[:20]

    your_by_mode = sorted([{"mode": m, **stat(d)} for m, d in your_mode.items()],
                          key=lambda x: -x["games"]) if your_mode else []
    return {"best_modes": best_modes, "unexpected_modes": unexpected,
            "maps": maps, "your_by_mode": your_by_mode}


def brawler_mode_maps(brawler_name: str, player_tag: str, mode: str, top: int = 4) -> list[dict]:
    """Mapas del JUGADOR con ese brawler en un modo concreto, con su win rate — para que la IA
    analice si ciertos mapas explican un buen rendimiento 'inesperado'."""
    name = (brawler_name or "").upper()
    conn = get_conn()
    rows = conn.execute(
        "SELECT map, SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END), SUM(CASE WHEN is_win=0 THEN 1 ELSE 0 END), "
        "COUNT(*) FROM battles WHERE player_tag=? AND UPPER(my_brawler)=? AND mode=? "
        "AND map IS NOT NULL AND map<>'unknown' GROUP BY map",
        (normalize_tag(player_tag or ""), name, mode)).fetchall()
    conn.close()
    out = [{"map": r[0], "winrate": _winrate(r[1] or 0, r[2] or 0), "games": r[3] or 0}
           for r in rows if r[0]]
    out.sort(key=lambda x: (x["games"], x["winrate"] or 0), reverse=True)
    return out[:top]


def all_equipped_skins() -> list:
    """(skin_id, brawler_name, skin_name) distintos equipados por cualquier jugador
    seguido. Para precachear las imágenes a cuerpo entero de todas las skins en uso."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT DISTINCT skin_id, brawler_name, skin_name FROM brawler_collection
           WHERE skin_id IS NOT NULL AND skin_name IS NOT NULL"""
    ).fetchall()
    conn.close()
    return [(r["skin_id"], r["brawler_name"], r["skin_name"]) for r in rows]


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
    for col, key in (("b.mode", "mode"), ("b.map", "map"), ("b.my_brawler", "brawler")):
        sql, p = _in(col, _multi(filters.get(key)))
        if sql:
            where.append(sql); params.extend(p)
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


def crosstab(filters: dict | None = None, top_brawlers: int | None = None) -> dict:
    """Tabla cruzada brawler x modo con win rate (para el mapa de calor). Por defecto
    incluye TODOS los brawlers con alguna partida (ordenados por nº de partidas); la tabla
    crece lo necesario. `top_brawlers` opcional para acotar."""
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
    ranked = [b for b, _ in sorted(btot.items(), key=lambda kv: -kv[1])]
    brawlers = ranked[:top_brawlers] if top_brawlers else ranked
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
        # "Mejor/Peor win rate" (Analíticas): win rate PURO a propósito (así se llaman las tarjetas).
        "best_brawler": _pick(by_brawler, "winrate", True),
        "worst_brawler": _pick(by_brawler, "winrate", False),
        # "Mejor brawler" por RENDIMIENTO ajustado (perfil público): no lo gana un 100% de 2 partidas.
        "best_brawler_perf": _pick(by_brawler, "adj_score", True),
        # Mejor/Peor modo y mapa por win rate ENCOGIDO (shrinkage): pondera el tamaño de muestra, así
        # un Duelos amistoso al 100% con 1-2 partidas no supera a un modo muy jugado con WR realista.
        "best_mode": _pick(by_mode, "shrunk_score", True, min_total=2),
        "worst_mode": _pick(by_mode, "shrunk_score", False, min_total=2),
        "best_map": _pick(by_map, "shrunk_score", True),
        "worst_map": _pick(by_map, "shrunk_score", False),
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


def competitive_pool(window_days: int = 30, min_seen: int = 1) -> list[dict]:
    """Pool de mapas del modo COMPETITIVO (Ranked), derivado de las partidas que el poller ya
    acumula. En el battlelog oficial las partidas de Ranked llegan con `type` 'soloRanked' o
    'teamRanked' (las de trofeos son 'ranked'), así que el pool vigente se COSECHA de ahí sin
    ninguna fuente externa: es el mismo método que usan Brawlify/Brawl Time Ninja.

    Ventana móvil (`window_days`): solo cuenta lo visto en los últimos N días, para reflejar el
    pool de la temporada vigente y purgar el de la anterior. Cuantos más jugadores de Ranked
    sigas, antes converge; con pocos, sube la ventana. `min_seen` filtra ruido (mapas vistos
    una sola vez si se quisiera). Devuelve [{mode, map, games, last_time}]."""
    ck = ("competitive_pool", window_days, min_seen)
    cached = _agg_get(ck)
    if cached is not None:
        return cached
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).strftime("%Y%m%dT%H%M%S.000Z")
    conn = get_conn()
    rows = conn.execute(
        """SELECT mode, map, COUNT(*) AS games, MAX(battle_time) AS last_time
           FROM battles
           WHERE LOWER(battle_type) IN ('soloranked', 'teamranked')
             AND map IS NOT NULL AND map != '' AND map != 'unknown'
             AND mode IS NOT NULL AND mode != 'unknown'
             AND battle_time >= ?
           GROUP BY mode, map
           HAVING COUNT(*) >= ?
           ORDER BY mode ASC, games DESC, map ASC""",
        (cutoff, min_seen)).fetchall()
    conn.close()
    return _agg_put(ck, [{"mode": r["mode"], "map": r["map"], "games": r["games"],
                          "last_time": r["last_time"]} for r in rows], 600)


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


def fail_stale_reports(player_tag: str | None = None, older_than_minutes: int | None = None,
                       reason: str = "El informe se canceló (el servidor se reinició o se agotó el tiempo).") -> int:
    """Marca como 'error' los informes ATASCADOS en 'generating'. Sin `older_than_minutes`
    marca TODOS los que sigan generándose (uso en el ARRANQUE: sus tareas murieron con el
    proceso anterior). Con `older_than_minutes` solo los más viejos que ese margen (uso al
    LISTAR: limpia cuelgues). Devuelve cuántos se han limpiado."""
    q = "UPDATE reports SET status='error', error=?, completed_at=? WHERE status='generating'"
    params: list = [reason, datetime.now(timezone.utc).isoformat()]
    if player_tag:
        q += " AND player_tag=?"; params.append(normalize_tag(player_tag))
    if older_than_minutes is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)).isoformat()
        q += " AND created_at < ?"; params.append(cutoff)
    conn = get_conn(); cur = conn.cursor()
    cur.execute(q, params)
    n = cur.rowcount
    conn.commit(); conn.close()
    return n


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


def is_event_owner(eid, uid) -> bool:
    """True solo si `uid` es el propietario principal (para acciones exclusivas: borrar, gestionar
    la lista de co-organizadores)."""
    return event_owner(eid) == uid


def is_event_organizer(eid, uid) -> bool:
    """True si `uid` es co-organizador (NO incluye al propietario)."""
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM event_organizers WHERE event_id=? AND user_id=?",
                       (eid, uid)).fetchone()
    conn.close()
    return bool(row)


def can_manage_event(eid, uid) -> bool:
    """True si `uid` puede co-gestionar el evento: propietario o co-organizador."""
    return is_event_owner(eid, uid) or is_event_organizer(eid, uid)


def add_event_organizer(eid, uid) -> None:
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO event_organizers (event_id, user_id, added_at) VALUES (?,?,?)",
                 (eid, uid, datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()


def remove_event_organizer(eid, uid) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM event_organizers WHERE event_id=? AND user_id=?", (eid, uid))
    conn.commit(); conn.close()


def list_event_organizers(eid) -> list[dict]:
    """Co-organizadores del evento (id, username, country), en orden de alta."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT u.id, u.username, u.country FROM event_organizers o "
        "JOIN users u ON u.id = o.user_id WHERE o.event_id=? ORDER BY o.added_at",
        (eid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --------------------------- mensajería (fase E) ---------------------------

def send_message(from_id: int, to_id: int, body: str) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO messages (from_user, to_user, body, created_at) VALUES (?,?,?,?)",
        (from_id, to_id, body, datetime.now(timezone.utc).isoformat()))
    conn.commit(); mid = cur.lastrowid; conn.close()
    return mid


def count_unread_messages(uid: int) -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) c FROM messages WHERE to_user=? AND read_at IS NULL AND to_deleted=0",
                     (uid,)).fetchone()["c"]
    conn.close()
    return n


def list_conversations(uid: int) -> list[dict]:
    """Una entrada por interlocutor: último mensaje + nº de no leídos, ordenadas por recencia.
    Solo cuenta lo que el usuario no ha borrado por su lado."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE (from_user=? AND from_deleted=0) OR (to_user=? AND to_deleted=0) "
        "ORDER BY id DESC", (uid, uid)).fetchall()
    conn.close()
    convos = {}
    for r in rows:
        other = r["to_user"] if r["from_user"] == uid else r["from_user"]
        c = convos.get(other)
        if c is None:  # primer mensaje visto por interlocutor = el más reciente (orden DESC)
            c = convos[other] = {"other_id": other, "last": r["body"], "last_at": r["created_at"],
                                 "last_from_me": bool(r["from_user"] == uid), "unread": 0}
        if r["to_user"] == uid and r["read_at"] is None and r["to_deleted"] == 0:
            c["unread"] += 1
    out = list(convos.values())
    for c in out:
        u = get_user_by_id(c["other_id"]) or {}
        c["username"] = u.get("username", "?")
    return out


def get_conversation(uid: int, other_id: int, limit: int = 300) -> list[dict]:
    """Mensajes entre `uid` y `other_id` que `uid` no ha borrado, en orden cronológico."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE "
        "(from_user=? AND to_user=? AND from_deleted=0) OR "
        "(from_user=? AND to_user=? AND to_deleted=0) "
        "ORDER BY id ASC LIMIT ?",
        (uid, other_id, other_id, uid, limit)).fetchall()
    conn.close()
    return [{"id": r["id"], "from_me": bool(r["from_user"] == uid), "body": r["body"],
             "created_at": r["created_at"], "read": r["read_at"] is not None} for r in rows]


def mark_conversation_read(uid: int, other_id: int) -> None:
    conn = get_conn()
    conn.execute("UPDATE messages SET read_at=? WHERE to_user=? AND from_user=? AND read_at IS NULL",
                 (datetime.now(timezone.utc).isoformat(), uid, other_id))
    conn.commit(); conn.close()


def mark_conversation_unread(uid: int, other_id: int) -> None:
    """Marca la conversación como no leída volviendo a dejar sin leer el último mensaje recibido."""
    conn = get_conn()
    row = conn.execute("SELECT id FROM messages WHERE to_user=? AND from_user=? AND to_deleted=0 "
                       "ORDER BY id DESC LIMIT 1", (uid, other_id)).fetchone()
    if row:
        conn.execute("UPDATE messages SET read_at=NULL WHERE id=?", (row["id"],))
        conn.commit()
    conn.close()


def mark_all_messages_read(uid: int) -> None:
    conn = get_conn()
    conn.execute("UPDATE messages SET read_at=? WHERE to_user=? AND read_at IS NULL",
                 (datetime.now(timezone.utc).isoformat(), uid))
    conn.commit(); conn.close()


def delete_conversation(uid: int, other_id: int) -> None:
    """Oculta la conversación para `uid` (no afecta al otro lado)."""
    conn = get_conn()
    conn.execute("UPDATE messages SET from_deleted=1 WHERE from_user=? AND to_user=?", (uid, other_id))
    conn.execute("UPDATE messages SET to_deleted=1 WHERE to_user=? AND from_user=?", (uid, other_id))
    conn.commit(); conn.close()


# --------------------------- redes sociales vinculadas (fase F) ---------------------------

def link_social_account(uid: int, platform: str, external_id: str = None, external_name: str = None,
                        access_token: str = None, refresh_token: str = None, expires_at: str = None) -> None:
    """Guarda (o actualiza) la cuenta de una red social vinculada por el usuario tras el OAuth."""
    conn = get_conn()
    conn.execute(
        "INSERT INTO social_accounts (user_id, platform, external_id, external_name, access_token, "
        "refresh_token, expires_at, connected_at) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(user_id, platform) DO UPDATE SET external_id=excluded.external_id, "
        "external_name=excluded.external_name, access_token=excluded.access_token, "
        "refresh_token=excluded.refresh_token, expires_at=excluded.expires_at, connected_at=excluded.connected_at",
        (uid, platform, external_id, external_name, access_token, refresh_token, expires_at,
         datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()


def unlink_social_account(uid: int, platform: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM social_accounts WHERE user_id=? AND platform=?", (uid, platform))
    conn.commit(); conn.close()


def list_social_accounts(uid: int) -> list[dict]:
    """Plataformas vinculadas por el usuario (SIN exponer tokens): platform, external_name, fecha."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT platform, external_name, connected_at FROM social_accounts WHERE user_id=?", (uid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_social_token(uid: int, platform: str) -> dict | None:
    """Token guardado de una plataforma (uso interno del servidor para publicar)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT external_id, external_name, access_token, refresh_token, expires_at "
        "FROM social_accounts WHERE user_id=? AND platform=?", (uid, platform)).fetchone()
    conn.close()
    return dict(row) if row else None


def has_social_accounts(uid: int) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM social_accounts WHERE user_id=? LIMIT 1", (uid,)).fetchone()
    conn.close()
    return row is not None


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
    for t in ("event_participants", "event_teams", "event_follows", "event_requests",
              "event_matches", "event_organizers"):
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


def pollable_event_ids() -> list:
    """Eventos no finalizados (para la detección automática de resultados). Incluye los que
    NO tienen fechas: se consideran en curso hasta marcarse como finalizados (el poller decide
    con la ventana de fechas si las hay)."""
    conn = get_conn()
    rows = conn.execute("SELECT id FROM events WHERE status != 'finished'").fetchall()
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


# =============================== Retos (sección social) ===============================
# Las CONDICIONES de un reto se guardan como JSON (lista). Cada condición es medible
# SOLO con datos que la app ya recoge (battles): {metric, target, scope?, min_games?}.
# El progreso se calcula sobre las partidas del jugador DESDE que se apuntó (joined_at).
# El motor de cálculo (progreso, dificultad) vive en app/retos.py; aquí solo BD + queries.

def _reto_row(r) -> dict | None:
    if not r:
        return None
    d = dict(r)
    try:
        d["conditions"] = json.loads(d.get("conditions") or "[]")
    except Exception:
        d["conditions"] = []
    return d


def create_reto(creator_id, name, theme, description, conditions, difficulty_declared,
                visibility="public", time_limit_days=None, source="user",
                report_id=None, target_user_id=None) -> int:
    token = secrets.token_urlsafe(9)
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO retos (creator_id, source, report_id, target_user_id, name, theme,
              description, conditions, difficulty_declared, visibility, time_limit_days,
              status, share_token, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (creator_id, source, report_id, target_user_id, (name or "Reto").strip()[:80],
         (theme or "").strip()[:30], (description or "").strip(),
         json.dumps(conditions or []), difficulty_declared,
         visibility if visibility in ("public", "invite") else "public",
         time_limit_days, "open", token, datetime.now(timezone.utc).isoformat()))
    rid = cur.lastrowid
    conn.commit(); conn.close()
    return rid


def get_reto(rid: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM retos WHERE id=?", (rid,)).fetchone()
    conn.close()
    return _reto_row(row)


def get_reto_by_token(token: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM retos WHERE share_token=?", (token,)).fetchone()
    conn.close()
    return _reto_row(row)


def reto_creator(rid: int):
    conn = get_conn()
    row = conn.execute("SELECT creator_id FROM retos WHERE id=?", (rid,)).fetchone()
    conn.close()
    return row["creator_id"] if row else None


def update_reto(rid: int, fields: dict) -> None:
    allowed = {"name", "theme", "description", "conditions", "difficulty_declared",
               "visibility", "time_limit_days", "status"}
    sets, params = [], []
    for k, v in (fields or {}).items():
        if k not in allowed:
            continue
        sets.append(f"{k}=?")
        params.append(json.dumps(v) if k == "conditions" else v)
    if not sets:
        return
    params.append(rid)
    conn = get_conn()
    conn.execute(f"UPDATE retos SET {', '.join(sets)} WHERE id=?", params)
    conn.commit(); conn.close()


def delete_reto(rid: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM retos WHERE id=?", (rid,))
    conn.execute("DELETE FROM reto_participants WHERE reto_id=?", (rid,))
    conn.commit(); conn.close()


def join_reto(rid, user_id, player_tag, role="participant", assigned_difficulty=None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    conn.execute(
        """INSERT INTO reto_participants (reto_id, user_id, player_tag, role, assigned_difficulty, status, joined_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(reto_id, user_id) DO UPDATE SET
             player_tag=excluded.player_tag, role=excluded.role,
             assigned_difficulty=excluded.assigned_difficulty""",
        (rid, user_id, normalize_tag(player_tag) if player_tag else None, role,
         assigned_difficulty, "active", now))
    conn.commit(); conn.close()


def leave_reto(rid, user_id) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM reto_participants WHERE reto_id=? AND user_id=?", (rid, user_id))
    conn.commit(); conn.close()


def reto_participant(rid, user_id) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM reto_participants WHERE reto_id=? AND user_id=?", (rid, user_id)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_reto_participants(rid) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT rp.*, p.name AS player_name FROM reto_participants rp "
        "LEFT JOIN players p ON p.tag = rp.player_tag WHERE rp.reto_id=? ORDER BY rp.joined_at", (rid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_reto_status(rid, user_id, status, completed_at=None) -> None:
    conn = get_conn()
    conn.execute("UPDATE reto_participants SET status=?, completed_at=? WHERE reto_id=? AND user_id=?",
                 (status, completed_at, rid, user_id))
    conn.commit(); conn.close()


def count_completed_retos(user_id, source=None) -> int:
    conn = get_conn()
    q = ("SELECT COUNT(*) FROM reto_participants rp JOIN retos r ON r.id=rp.reto_id "
         "WHERE rp.user_id=? AND rp.status='completed'")
    params = [user_id]
    if source:
        q += " AND r.source=?"; params.append(source)
    n = conn.execute(q, params).fetchone()[0]
    conn.close()
    return n


def list_completed_retos(user_id, source=None) -> list:
    conn = get_conn()
    q = ("SELECT r.*, rp.completed_at AS my_completed FROM reto_participants rp "
         "JOIN retos r ON r.id=rp.reto_id WHERE rp.user_id=? AND rp.status='completed'")
    params = [user_id]
    if source:
        q += " AND r.source=?"; params.append(source)
    q += " ORDER BY rp.completed_at DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [_reto_row(r) for r in rows]


def list_my_retos(user_id) -> dict:
    """Los retos del usuario en 3 grupos: asignados por el Sensei, creados por él,
    y seguidos/apuntados (de otros)."""
    conn = get_conn()
    sensei = conn.execute(
        "SELECT r.*, rp.status AS my_status, rp.role AS my_role, rp.joined_at AS my_joined, "
        "rp.player_tag AS my_player FROM retos r JOIN reto_participants rp ON rp.reto_id=r.id "
        "WHERE rp.user_id=? AND r.source='sensei' ORDER BY r.id DESC", (user_id,)).fetchall()
    created = conn.execute(
        "SELECT * FROM retos WHERE creator_id=? AND source='user' ORDER BY id DESC", (user_id,)).fetchall()
    joined = conn.execute(
        "SELECT r.*, rp.status AS my_status, rp.role AS my_role, rp.joined_at AS my_joined, "
        "rp.player_tag AS my_player FROM retos r JOIN reto_participants rp ON rp.reto_id=r.id "
        "WHERE rp.user_id=? AND r.source='user' AND (r.creator_id IS NULL OR r.creator_id != ?) ORDER BY r.id DESC",
        (user_id, user_id)).fetchall()
    conn.close()
    return {"sensei": [_reto_row(r) for r in sensei],
            "created": [_reto_row(r) for r in created],
            "joined": [_reto_row(r) for r in joined]}


def list_board_retos(user_id, status=None, theme=None) -> list:
    """Tablón: retos comunitarios (source='user'), ordenados por dificultad y tema.
    Adjunta nº de participantes y el estado del usuario si ya participa."""
    conn = get_conn()
    q = ["SELECT r.*, "
         "(SELECT COUNT(*) FROM reto_participants rp WHERE rp.reto_id=r.id AND rp.role='participant') AS participants, "
         "(SELECT COUNT(*) FROM reto_participants rp WHERE rp.reto_id=r.id AND rp.role='follower') AS followers, "
         "(SELECT status FROM reto_participants rp WHERE rp.reto_id=r.id AND rp.user_id=?) AS my_status "
         "FROM retos r WHERE r.source='user'"]
    params = [user_id]
    if status in ("open", "closed"):
        q.append("AND r.status=?"); params.append(status)
    if theme:
        q.append("AND r.theme=?"); params.append(theme)
    q.append("ORDER BY r.difficulty_declared ASC, r.theme COLLATE NOCASE, r.id DESC")
    rows = conn.execute(" ".join(q), params).fetchall()
    conn.close()
    return [_reto_row(r) for r in rows]


def reto_metric(player_tag, since, until, metric, scope) -> float:
    """Valor actual de una MÉTRICA para un jugador, sobre sus partidas en la ventana
    [since, until] (formato battle_time) y el ámbito (mode/map/brawler/role). Todo se
    calcula desde `battles`, reutilizando _build_filters para el ámbito."""
    f = dict(scope or {})
    f["player"] = player_tag
    where_sql, params = _build_filters(f)
    clauses = [where_sql[len("WHERE "):]] if where_sql else []
    if since:
        clauses.append("battle_time >= ?"); params.append(since)
    if until:
        clauses.append("battle_time <= ?"); params.append(until)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = get_conn()
    try:
        if metric == "wins":
            q = f"SELECT SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END) FROM battles {where}"
        elif metric == "games":
            q = f"SELECT SUM(CASE WHEN result IS NOT NULL THEN 1 ELSE 0 END) FROM battles {where}"
        elif metric == "star_player":
            q = f"SELECT SUM(CASE WHEN is_star_player=1 THEN 1 ELSE 0 END) FROM battles {where}"
        elif metric == "trophies":
            q = f"SELECT SUM(COALESCE(trophy_change,0)) FROM battles {where}"
        elif metric == "distinct_brawlers":
            extra = "AND" if where else "WHERE"
            q = f"SELECT COUNT(DISTINCT my_brawler) FROM battles {where} {extra} is_win=1"
        elif metric == "distinct_played":
            extra = "AND" if where else "WHERE"
            q = f"SELECT COUNT(DISTINCT my_brawler) FROM battles {where} {extra} my_brawler IS NOT NULL"
        elif metric == "winrate":
            row = conn.execute(
                f"SELECT SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END) AS w, "
                f"SUM(CASE WHEN result IS NOT NULL THEN 1 ELSE 0 END) AS g FROM battles {where}", params).fetchone()
            w, g = (row["w"] or 0), (row["g"] or 0)
            return round(100.0 * w / g, 1) if g else 0.0
        elif metric == "win_streak":
            rows = conn.execute(f"SELECT is_win FROM battles {where} ORDER BY battle_time ASC", params).fetchall()
            best = run = 0
            for r in rows:
                if r["is_win"] == 1:
                    run += 1; best = max(best, run)
                elif r["is_win"] == 0:
                    run = 0
            return best
        elif metric in ("losses", "max_losses"):   # derrotas acumuladas (para el tope 'no pases de X')
            q = f"SELECT SUM(CASE WHEN is_win=0 THEN 1 ELSE 0 END) FROM battles {where}"
        elif metric == "active_days":               # días distintos con al menos una partida (constancia)
            q = f"SELECT COUNT(DISTINCT substr(battle_time,1,8)) FROM battles {where}"
        elif metric == "performance":               # rendimiento AJUSTADO sobre el ámbito (win rate encogido + dificultad)
            row = conn.execute(
                f"SELECT SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END) AS w, "
                f"SUM(CASE WHEN is_win=0 THEN 1 ELSE 0 END) AS l, "
                f"AVG(CAST(my_trophies AS REAL)) AS avgt, AVG(o.rt) AS avgr "
                f"FROM battles LEFT JOIN (SELECT battle_id, AVG(CAST(trophies AS REAL)) AS rt "
                f"FROM opponents GROUP BY battle_id) o ON o.battle_id = battles.id {where}", params).fetchone()
            base = conn.execute(
                "SELECT AVG(CAST(my_trophies AS REAL)) FROM battles WHERE player_tag=?",
                (normalize_tag(player_tag),)).fetchone()[0] or 0
            adj = _adjusted_score(row["w"] or 0, row["l"] or 0, row["avgt"], row["avgr"], base)
            return adj if adj is not None else 0.0
        else:
            return 0
        val = conn.execute(q, params).fetchone()[0]
        return val or 0
    finally:
        conn.close()


def count_active_sensei_retos(user_id) -> int:
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM reto_participants rp JOIN retos r ON r.id=rp.reto_id "
        "WHERE rp.user_id=? AND rp.status='active' AND r.source='sensei'", (user_id,)).fetchone()[0]
    conn.close()
    return n


def last_sensei_reto_at(user_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT MAX(created_at) AS t FROM retos WHERE source='sensei' AND target_user_id=?", (user_id,)).fetchone()
    conn.close()
    return row["t"] if row else None


def reset_sensei_training(user_id) -> int:
    """Abandona los retos del Sensei activos del usuario (reinicia el entrenamiento)."""
    conn = get_conn()
    cur = conn.execute(
        "UPDATE reto_participants SET status='abandoned' WHERE user_id=? AND status='active' "
        "AND reto_id IN (SELECT id FROM retos WHERE source='sensei')", (user_id,))
    n = cur.rowcount
    conn.commit(); conn.close()
    return n


def delete_report(report_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM reports WHERE id=?", (report_id,))
    conn.commit(); conn.close()


def versatile_brawlers(filters, limit=13):
    """Top brawlers por VERSATILIDAD: rendimiento medio repartido entre TODOS los modos que
    juega la cuenta, contando los modos NO jugados como 0. Así un brawler que solo se usa en
    un modo (aunque sea al 100%) puntúa bajo (100/nº_modos), y suben los que rinden en varios.
    `avg_winrate` = versatilidad ajustada a dificultad; `avg_raw` = igual pero con win rate
    puro. El router lo enriquece con retrato/cuerpo entero."""
    where_sql, params = _build_filters(filters or {})
    extra = "AND" if where_sql else "WHERE"
    conn = get_conn()
    # Nivel de referencia (copas medias) para la dificultad del rendimiento ajustado.
    base = conn.execute(
        f"SELECT AVG(CAST(my_trophies AS REAL)) FROM battles {where_sql} {extra} "
        f"my_brawler IS NOT NULL AND mode IS NOT NULL", params).fetchone()[0] or 0
    rows = conn.execute(
        f"SELECT my_brawler AS brawler, mode, "
        f"SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END) AS wins, "
        f"SUM(CASE WHEN is_win=0 THEN 1 ELSE 0 END) AS losses, COUNT(*) AS total, "
        f"AVG(CAST(my_trophies AS REAL)) AS avg_tr, AVG(o.rt) AS avg_rival "
        f"FROM battles "
        f"LEFT JOIN (SELECT battle_id, AVG(CAST(trophies AS REAL)) AS rt FROM opponents "
        f"GROUP BY battle_id) o ON o.battle_id = battles.id "
        f"{where_sql} {extra} my_brawler IS NOT NULL AND mode IS NOT NULL "
        f"GROUP BY my_brawler, mode", params).fetchall()
    conn.close()
    # Denominador de la versatilidad = nº de modos DISTINTOS que juega la cuenta (las columnas
    # de la tabla Brawler x Modo). Los modos que un brawler no juega cuentan como 0 en su media.
    all_modes = {r["mode"] for r in rows if r["mode"]}
    n_modes = len(all_modes) or 1
    agg = {}
    for r in rows:
        wr = _winrate(r["wins"], r["losses"])
        if wr is None:
            continue
        adj = _adjusted_score(r["wins"], r["losses"], r["avg_tr"], r["avg_rival"], base)
        d = agg.setdefault(r["brawler"], {"adjs": [], "wrs": [], "total": 0})
        d["adjs"].append(adj if adj is not None else wr)
        d["wrs"].append(wr)
        d["total"] += r["total"]
    out = [{"name": b, "avg_winrate": round(sum(d["adjs"]) / n_modes, 1),
            "avg_raw": round(sum(d["wrs"]) / n_modes, 1),
            "modes_played": len(d["adjs"]), "n_modes": n_modes, "total": d["total"]}
           for b, d in agg.items() if d["adjs"]]
    out.sort(key=lambda x: (-x["avg_winrate"], -x["modes_played"], -x["total"], x["name"]))
    return out[:limit]
