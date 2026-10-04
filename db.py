import math
import os
import sqlite3
import uuid
from datetime import datetime, timezone


# Load .env here, BEFORE the settings below are read. app.py imports this
# module before it used to call load_dotenv(), so a local .env was ignored
# and the app silently fell back to SQLite. (Environment variables / Streamlit
# Cloud secrets that are already set are never overridden.)
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

DB_PATH = os.getenv("LOCAL_DB_PATH", "rescuelens.db")

USE_SUPABASE = bool(
    os.getenv("SUPABASE_URL", "").strip()
    and os.getenv("SUPABASE_KEY", "").strip()
)


if USE_SUPABASE:
    from supabase import create_client

    supabase = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_KEY"),
    )
else:
    supabase = None


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    if USE_SUPABASE:
        return

    conn = sqlite3.connect(DB_PATH)

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS emergencies (
            id TEXT PRIMARY KEY,
            display_number INTEGER UNIQUE,
            description TEXT NOT NULL,
            reporter_name TEXT,
            reporter_phone TEXT,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            location TEXT,
            emergency_type TEXT,
            priority TEXT,
            required_team TEXT,
            people_affected INTEGER DEFAULT 1,
            medical_emergency INTEGER DEFAULT 0,
            people_trapped INTEGER DEFAULT 0,
            critical_injury INTEGER DEFAULT 0,
            ai_summary TEXT,
            ai_reasoning TEXT,
            ai_generated INTEGER DEFAULT 0,
            status TEXT DEFAULT 'Waiting for responder',
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS responders (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            available INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS assignments (
            id TEXT PRIMARY KEY,
            emergency_id TEXT UNIQUE NOT NULL,
            responder_id TEXT NOT NULL,
            distance_km REAL,
            eta_minutes REAL,
            created_at TEXT
        );
        """
    )

    conn.commit()
    conn.close()


def _next_number(conn):
    row = conn.execute(
        "SELECT COALESCE(MAX(display_number), 1000) FROM emergencies"
    ).fetchone()
    return row[0] + 1


def create_emergency(
    description,
    location,
    latitude,
    longitude,
    people_affected,
    reporter_name,
    reporter_phone,
    triage,
):
    record = {
        "id": uuid.uuid4().hex[:10],
        "display_number": None,
        "description": description,
        "reporter_name": reporter_name,
        "reporter_phone": reporter_phone,
        "latitude": latitude,
        "longitude": longitude,
        "location": location,
        "emergency_type": triage["emergency_type"],
        "priority": triage["priority"],
        "required_team": triage["required_team"],
        "people_affected": triage["people_affected"],
        "medical_emergency": triage["medical_emergency"],
        "people_trapped": triage["people_trapped"],
        "critical_injury": triage["critical_injury"],
        "ai_summary": triage["summary"],
        "ai_reasoning": triage["reasoning"],
        "ai_generated": triage["ai_generated"],
        "status": "Waiting for responder",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    # display_number is "max + 1", which is not atomic. If two reports arrive
    # together the UNIQUE constraint rejects one of them, so retry a few times
    # with a fresh number instead of losing the report.
    if USE_SUPABASE:
        last_error = None

        for _attempt in range(3):
            row = (
                supabase
                .table("emergencies")
                .select("display_number")
                .order("display_number", desc=True)
                .limit(1)
                .execute()
            )

            record["display_number"] = (
                row.data[0]["display_number"] if row.data else 1000
            ) + 1

            try:
                result = supabase.table("emergencies").insert(record).execute()
                return result.data[0]
            except Exception as exc:
                last_error = exc

        raise last_error

    last_error = None

    for _attempt in range(3):
        conn = sqlite3.connect(DB_PATH)

        try:
            record["display_number"] = _next_number(conn)

            conn.execute(
                """
                INSERT INTO emergencies VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                """,
                tuple(record.values()),
            )

            conn.commit()
            return record
        except sqlite3.IntegrityError as exc:
            last_error = exc
        finally:
            conn.close()

    raise last_error


def list_emergencies():
    if USE_SUPABASE:
        result = (
            supabase
            .table("emergencies")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )

        rows = result.data or []

        assignments = (
            supabase.table("assignments").select("*").execute().data or []
        )

        responders = {
            r["id"]: r
            for r in (
                supabase.table("responders").select("*").execute().data or []
            )
        }

        amap = {a["emergency_id"]: a for a in assignments}

        for r in rows:
            a = amap.get(r["id"])
            r["assignment"] = None

            if a:
                rr = responders.get(a["responder_id"], {})
                r["assignment"] = {
                    "responder_id": a.get("responder_id"),
                    "responder_name": rr.get("name", "Responder"),
                    "distance_km": a.get("distance_km") or 0,
                    "eta_minutes": a.get("eta_minutes") or 0,
                }

        return rows

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = [
        dict(x)
        for x in conn.execute(
            "SELECT * FROM emergencies ORDER BY created_at DESC"
        ).fetchall()
    ]

    for r in rows:
        a = conn.execute(
            """
            SELECT a.*, r.name responder_name
            FROM assignments a
            JOIN responders r ON r.id = a.responder_id
            WHERE a.emergency_id = ?
            """,
            (r["id"],),
        ).fetchone()

        r["assignment"] = dict(a) if a else None

    conn.close()
    return rows


def get_stats():
    rows = list_emergencies()
    by_type = {}

    for r in rows:
        by_type[r["emergency_type"]] = by_type.get(r["emergency_type"], 0) + 1

    return {
        "total": len(rows),
        "critical": sum(r["priority"] == "Critical" for r in rows),
        "high": sum(r["priority"] == "High" for r in rows),
        "medium": sum(r["priority"] == "Medium" for r in rows),
        "low": sum(r["priority"] == "Low" for r in rows),
        "resolved": sum(r["status"] == "Resolved" for r in rows),
        "by_type": by_type,
    }


# =============================================================
# RESPONDER SEEDING
# Two units are created for every team type so one unit can remain
# available while the other is handling another emergency.
# =============================================================

def seed_responders():
    demo = [
        ("Rescue Team Alpha", "Rescue Team", 29.3956, 71.6836),
        ("Rescue Team Bravo", "Rescue Team", 29.3920, 71.6780),

        ("Medical Team 1", "Medical Team", 29.4000, 71.6750),
        ("Medical Team 2", "Medical Team", 29.3980, 71.6800),

        ("Fire Team North", "Fire Team", 29.4100, 71.6900),
        ("Fire Team South", "Fire Team", 29.3820, 71.6700),

        ("Food & Water Unit 1", "Food/Water Team", 29.3850, 71.7000),
        ("Food & Water Unit 2", "Food/Water Team", 29.3900, 71.6650),

        ("General Response Unit 1", "General Team", 29.4050, 71.6650),
        ("General Response Unit 2", "General Team", 29.3750, 71.6850),
    ]

    if USE_SUPABASE:
        existing = (
            supabase
            .table("responders")
            .select("name")
            .execute()
            .data
            or []
        )

        existing_names = {r["name"] for r in existing}

        for name, team_type, lat, lon in demo:
            if name not in existing_names:
                supabase.table("responders").insert(
                    {
                        "id": uuid.uuid4().hex[:10],
                        "name": name,
                        "type": team_type,
                        "latitude": lat,
                        "longitude": lon,
                        "available": 1,
                    }
                ).execute()

        sync_responder_availability()
        return

    conn = sqlite3.connect(DB_PATH)

    for name, team_type, lat, lon in demo:
        exists = conn.execute(
            "SELECT id FROM responders WHERE name = ?",
            (name,),
        ).fetchone()

        if not exists:
            conn.execute(
                """
                INSERT INTO responders
                VALUES (?,?,?,?,?,1)
                """,
                (
                    uuid.uuid4().hex[:10],
                    name,
                    team_type,
                    lat,
                    lon,
                ),
            )

    conn.commit()
    conn.close()

    sync_responder_availability()


def sync_responder_availability():
    """Keep responder availability consistent with active assignments.

    A responder is busy only when it has an assignment whose emergency
    is not Resolved. Once that emergency is Resolved, the responder is
    automatically available for the next task.
    """
    if USE_SUPABASE:
        responders = (
            supabase.table("responders").select("id").execute().data or []
        )
        emergencies = (
            supabase.table("emergencies").select("id,status").execute().data or []
        )
        assignments = (
            supabase.table("assignments").select("emergency_id,responder_id").execute().data
            or []
        )

        status_by_emergency = {e["id"]: e.get("status") for e in emergencies}
        busy_ids = {
            a["responder_id"]
            for a in assignments
            if status_by_emergency.get(a["emergency_id"]) != "Resolved"
        }

        for responder in responders:
            supabase.table("responders").update(
                {"available": 0 if responder["id"] in busy_ids else 1}
            ).eq("id", responder["id"]).execute()
        return

    conn = sqlite3.connect(DB_PATH)

    busy_ids = {
        row[0]
        for row in conn.execute(
            """
            SELECT a.responder_id
            FROM assignments a
            JOIN emergencies e ON e.id = a.emergency_id
            WHERE e.status != 'Resolved'
            """
        ).fetchall()
    }

    conn.execute("UPDATE responders SET available = 1")

    for responder_id in busy_ids:
        conn.execute(
            "UPDATE responders SET available = 0 WHERE id = ?",
            (responder_id,),
        )

    conn.commit()
    conn.close()


def list_responders():
    if USE_SUPABASE:
        return (
            supabase
            .table("responders")
            .select("*")
            .order("name")
            .execute()
            .data
            or []
        )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = [
        dict(x)
        for x in conn.execute(
            "SELECT * FROM responders ORDER BY name"
        ).fetchall()
    ]

    conn.close()
    return rows


def _distance_km(a_lat, a_lon, b_lat, b_lon):
    """Calculate straight-line distance between two coordinates in km."""
    radius = 6371.0
    p1 = math.radians(a_lat)
    p2 = math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)

    x = (
        math.sin(dp / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    )

    return 2 * radius * math.asin(math.sqrt(x))


# =============================================================
# RESPONDER ASSIGNMENT
# =============================================================

def assign_responder(emergency_id, responder_id=None):
    emergencies = list_emergencies()

    e = next(
        (x for x in emergencies if x["id"] == emergency_id),
        None,
    )

    if not e:
        return {"ok": False, "error": "Emergency not found."}

    responders = list_responders()

    available = [
        r for r in responders
        if int(r.get("available", 0)) == 1
    ]

    if responder_id:
        chosen = next(
            (r for r in available if r["id"] == responder_id),
            None,
        )
    else:
        # Only assign the team selected by AI. A Fire emergency must
        # receive a Fire Team, a Medical emergency a Medical Team, etc.
        matching = [
            r for r in available
            if r["type"] == e["required_team"]
        ]

        chosen = (
            min(
                matching,
                key=lambda r: _distance_km(
                    e["latitude"],
                    e["longitude"],
                    r["latitude"],
                    r["longitude"],
                ),
            )
            if matching
            else None
        )

    if not chosen:
        return {
            "ok": False,
            "error": f"No available {e['required_team']} found for this emergency.",
        }

    distance = _distance_km(
        e["latitude"],
        e["longitude"],
        chosen["latitude"],
        chosen["longitude"],
    )

    eta = max(3, distance / 35 * 60)

    if USE_SUPABASE:
        old = (
            supabase
            .table("assignments")
            .select("id,responder_id")
            .eq("emergency_id", emergency_id)
            .execute()
            .data
        )

        if old:
            # Make the old responder available again before assigning
            # the new responder.
            supabase.table("responders").update(
                {"available": 1}
            ).eq(
                "id", old[0]["responder_id"]
            ).execute()

            supabase.table("assignments").delete().eq(
                "emergency_id", emergency_id
            ).execute()

        supabase.table("assignments").insert(
            {
                "id": uuid.uuid4().hex[:10],
                "emergency_id": emergency_id,
                "responder_id": chosen["id"],
                "distance_km": distance,
                "eta_minutes": eta,
                "created_at": now_iso(),
            }
        ).execute()

        supabase.table("responders").update(
            {"available": 0}
        ).eq("id", chosen["id"]).execute()

        supabase.table("emergencies").update(
            {
                "status": "Dispatched",
                "updated_at": now_iso(),
            }
        ).eq("id", emergency_id).execute()

    else:
        conn = sqlite3.connect(DB_PATH)

        # If this emergency already had a responder, release that
        # responder before creating the new assignment.
        old = conn.execute(
            "SELECT responder_id FROM assignments WHERE emergency_id = ?",
            (emergency_id,),
        ).fetchone()

        if old:
            conn.execute(
                "UPDATE responders SET available = 1 WHERE id = ?",
                (old[0],),
            )
            conn.execute(
                "DELETE FROM assignments WHERE emergency_id = ?",
                (emergency_id,),
            )

        conn.execute(
            "UPDATE responders SET available = 0 WHERE id = ?",
            (chosen["id"],),
        )

        conn.execute(
            """
            INSERT INTO assignments
            VALUES (?,?,?,?,?,?)
            """,
            (
                uuid.uuid4().hex[:10],
                emergency_id,
                chosen["id"],
                distance,
                eta,
                now_iso(),
            ),
        )

        conn.execute(
            """
            UPDATE emergencies
            SET status = 'Dispatched', updated_at = ?
            WHERE id = ?
            """,
            (now_iso(), emergency_id),
        )

        conn.commit()
        conn.close()

    return {
        "ok": True,
        "responder": chosen,
        "distance_km": distance,
        "eta_minutes": eta,
    }


# =============================================================
# UPDATE STATUS
# Resolved -> assigned responder becomes available again.
# =============================================================

def update_status(emergency_id, status):
    if USE_SUPABASE:
        supabase.table("emergencies").update(
            {
                "status": status,
                "updated_at": now_iso(),
            }
        ).eq("id", emergency_id).execute()

        if status == "Resolved":
            ass = (
                supabase
                .table("assignments")
                .select("responder_id")
                .eq("emergency_id", emergency_id)
                .execute()
                .data
            )

            if ass:
                supabase.table("responders").update(
                    {"available": 1}
                ).eq(
                    "id", ass[0]["responder_id"]
                ).execute()

        return

    conn = sqlite3.connect(DB_PATH)

    if status == "Resolved":
        ass = conn.execute(
            """
            SELECT responder_id
            FROM assignments
            WHERE emergency_id = ?
            """,
            (emergency_id,),
        ).fetchone()

        if ass:
            conn.execute(
                """
                UPDATE responders
                SET available = 1
                WHERE id = ?
                """,
                (ass[0],),
            )

    conn.execute(
        """
        UPDATE emergencies
        SET status = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, now_iso(), emergency_id),
    )

    conn.commit()
    conn.close()
