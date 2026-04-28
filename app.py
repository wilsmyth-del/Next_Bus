import csv
import io
import os
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from google.transit import gtfs_realtime_pb2

load_dotenv()

app = Flask(__name__)

TRANSLINK_API_KEY = os.getenv("TRANSLINK_API_KEY")
FEED_URL = "https://gtfsapi.translink.ca/v3/gtfsrealtime?apikey={key}"

DB_PATH = os.path.join(os.path.dirname(__file__), "stars.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS starred_stops (
                stop_id    TEXT PRIMARY KEY,
                label      TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stop_map (
                stop_code  TEXT PRIMARY KEY,
                gtfs_id    TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS route_map (
                route_id         TEXT PRIMARY KEY,
                route_short_name TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS static_schedule (
                gtfs_stop_id  TEXT NOT NULL,
                trip_id       TEXT NOT NULL,
                route_id      TEXT NOT NULL,
                arrival_secs  INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_static_schedule_stop
            ON static_schedule(gtfs_stop_id)
        """)


init_db()


def fetch_feed():
    api_key = os.getenv("TRANSLINK_API_KEY")
    if not api_key:
        raise RuntimeError("TRANSLINK_API_KEY is not set")
    url = FEED_URL.format(key=api_key)
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(response.content)
    return feed


def get_stop_map():
    with get_db() as conn:
        rows = conn.execute("SELECT stop_code, gtfs_id FROM stop_map").fetchall()
    return {r["stop_code"]: r["gtfs_id"] for r in rows}


def get_route_map():
    with get_db() as conn:
        rows = conn.execute("SELECT route_id, route_short_name FROM route_map").fetchall()
    return {r["route_id"]: r["route_short_name"] for r in rows}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    stop_map = get_stop_map()
    return jsonify({"status": "ok", "mapped_stops": len(stop_map)})


@app.route("/next/<stop_code>")
def next_buses(stop_code):
    stop_map = get_stop_map()
    gtfs_id = stop_map.get(stop_code, stop_code)

    try:
        feed = fetch_feed()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except requests.RequestException:
        return jsonify({"error": "Could not reach Translink API"}), 502

    route_map = get_route_map()
    now = time.time()
    arrivals = []

    # trip_ids with a VehiclePosition entity have a located vehicle (live GPS)
    live_trip_ids = {
        entity.vehicle.trip.trip_id
        for entity in feed.entity
        if entity.HasField("vehicle")
    }

    feed_trip_ids = set()

    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        trip_id = entity.trip_update.trip.trip_id
        route_id = entity.trip_update.trip.route_id
        route_name = route_map.get(route_id, route_id)
        for stu in entity.trip_update.stop_time_update:
            if stu.stop_id != gtfs_id:
                continue
            arrival_time = stu.arrival.time
            if arrival_time <= now:
                continue
            feed_trip_ids.add(trip_id)
            source = "live" if trip_id in live_trip_ids else "approx"
            arrivals.append({
                "route": route_name,
                "arrival_time": time.strftime("%H:%M", time.localtime(arrival_time)),
                "minutes_away": int((arrival_time - now) // 60),
                "delay_seconds": stu.arrival.delay,
                "source": source,
            })

    # --- tier 3: static schedule fallback ---
    t = time.localtime()
    seconds_since_midnight_now = t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec

    try:
        with get_db() as conn:
            static_rows = conn.execute(
                """
                SELECT trip_id, route_id, arrival_secs
                FROM static_schedule
                WHERE gtfs_stop_id = ?
                  AND arrival_secs > ?
                ORDER BY arrival_secs
                """,
                (gtfs_id, seconds_since_midnight_now),
            ).fetchall()
    except sqlite3.OperationalError:
        static_rows = []

    for row in static_rows:
        if row["trip_id"] in feed_trip_ids:
            continue
        minutes_away = int((row["arrival_secs"] - seconds_since_midnight_now) // 60)
        if minutes_away < 0:
            continue
        route_name = route_map.get(row["route_id"], row["route_id"])
        # Convert arrival_secs back to HH:MM (handles overnight times > 86400)
        arrival_secs = row["arrival_secs"]
        arrival_hh = (arrival_secs // 3600) % 24
        arrival_mm = (arrival_secs % 3600) // 60
        arrivals.append({
            "route": route_name,
            "arrival_time": f"{arrival_hh:02d}:{arrival_mm:02d}",
            "minutes_away": minutes_away,
            "delay_seconds": 0,
            "source": "scheduled",
        })

    arrivals.sort(key=lambda x: x["minutes_away"])
    return jsonify({"stop_code": stop_code, "buses": arrivals[:8]})


GTFS_ZIP_PATH = os.getenv(
    "GTFS_ZIP_PATH",
    os.path.join(os.path.dirname(__file__), "google_transit.zip"),
)
GTFS_BASE_URL = "https://gtfs-static.translink.ca/gtfs/History/{date}/google_transit.zip"


def _parse_arrival_secs(time_str):
    """Convert GTFS HH:MM:SS (may exceed 23:59:59) to integer seconds since midnight.
    Returns None if the string is malformed."""
    try:
        parts = time_str.strip().split(":")
        if len(parts) != 3:
            return None
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, AttributeError):
        return None


def _today_str():
    """Return today's date as YYYYMMDD string."""
    return datetime.now().strftime("%Y%m%d")


def check_gtfs_update():
    def candidate_dates():
        today = date.today()
        seen = set()
        # TransLink publishes new GTFS on Thursdays; check recent Thursdays first
        days_since_thursday = (today.weekday() - 3) % 7
        for i in range(4):
            d = today - timedelta(days=days_since_thursday + i * 7)
            seen.add(d)
            yield d
        for i in range(14):
            d = today - timedelta(days=i)
            if d not in seen:
                yield d

    zip_path = Path(GTFS_ZIP_PATH)
    current_mtime = zip_path.stat().st_mtime if zip_path.exists() else 0

    newest_url = None
    newest_date = None
    for d in candidate_dates():
        date_str = d.strftime("%Y-%m-%d")
        url = GTFS_BASE_URL.format(date=date_str)
        try:
            resp = requests.head(url, timeout=8, allow_redirects=True)
            if resp.status_code == 200:
                newest_url = url
                newest_date = date_str
                break
        except Exception:
            continue

    if not newest_url:
        return {"action": "not_found", "checked_url": None}

    newest_dt = datetime.strptime(newest_date, "%Y-%m-%d")
    if current_mtime and newest_dt.timestamp() <= current_mtime:
        return {"action": "already_current", "checked_url": newest_url, "new_zip_date": newest_date}

    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name
        resp = requests.get(newest_url, timeout=60, stream=True)
        resp.raise_for_status()
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
        if not zipfile.is_zipfile(tmp_path):
            os.unlink(tmp_path)
            return {"action": "error", "error": "Downloaded file is not a valid zip", "checked_url": newest_url}
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(tmp_path, str(zip_path))
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return {"action": "error", "error": str(e), "checked_url": newest_url}

    return {"action": "downloaded", "checked_url": newest_url, "new_zip_date": newest_date}


@app.route("/check-gtfs-update", methods=["POST"])
def api_check_gtfs_update():
    result = check_gtfs_update()
    if result.get("action") == "downloaded":
        refresh_stops()
    return jsonify(result)


@app.route("/refresh", methods=["POST"])
def refresh_stops():
    try:
        with zipfile.ZipFile(GTFS_ZIP_PATH) as z:
            with z.open("stops.txt") as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                stop_rows = [
                    (r["stop_code"].strip(), r["stop_id"].strip())
                    for r in reader
                    if r.get("stop_code") and r.get("stop_id")
                ]

            with z.open("routes.txt") as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                route_rows = [
                    (r["route_id"].strip(), r["route_short_name"].strip())
                    for r in reader
                    if r.get("route_id") and r.get("route_short_name")
                ]

            today = _today_str()
            dow_col = datetime.now().strftime("%A").lower()
            active_services = set()
            with z.open("calendar.txt") as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                for r in reader:
                    if (r.get(dow_col, "0").strip() == "1"
                            and r.get("start_date", "").strip() <= today
                            and r.get("end_date", "").strip() >= today):
                        active_services.add(r["service_id"].strip())

            with z.open("calendar_dates.txt") as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                for r in reader:
                    if r.get("date", "").strip() != today:
                        continue
                    sid = r["service_id"].strip()
                    etype = r.get("exception_type", "").strip()
                    if etype == "1":
                        active_services.add(sid)
                    elif etype == "2":
                        active_services.discard(sid)

            active_trips = {}
            with z.open("trips.txt") as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                for r in reader:
                    sid = r.get("service_id", "").strip()
                    if sid in active_services:
                        active_trips[r["trip_id"].strip()] = r["route_id"].strip()

            schedule_rows = []
            with z.open("stop_times.txt") as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                for r in reader:
                    tid = r.get("trip_id", "").strip()
                    if tid not in active_trips:
                        continue
                    arrival_raw = r.get("arrival_time", "").strip()
                    if not arrival_raw:
                        continue
                    arrival_secs = _parse_arrival_secs(arrival_raw)
                    if arrival_secs is None:
                        continue
                    schedule_rows.append((
                        r["stop_id"].strip(),
                        tid,
                        active_trips[tid],
                        arrival_secs,
                    ))

        with get_db() as conn:
            conn.execute("DELETE FROM stop_map")
            conn.executemany("INSERT INTO stop_map (stop_code, gtfs_id) VALUES (?, ?)", stop_rows)
            conn.execute("DELETE FROM route_map")
            conn.executemany("INSERT INTO route_map (route_id, route_short_name) VALUES (?, ?)", route_rows)
            conn.execute("DELETE FROM static_schedule")
            conn.executemany(
                "INSERT INTO static_schedule (gtfs_stop_id, trip_id, route_id, arrival_secs) VALUES (?, ?, ?, ?)",
                schedule_rows,
            )

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "status": "ok",
        "stops_loaded": len(stop_rows),
        "routes_loaded": len(route_rows),
        "static_trips_loaded": len(schedule_rows),
    })


@app.route("/search")
def search_stops():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "q parameter required"}), 400
    with get_db() as conn:
        rows = conn.execute(
            "SELECT stop_code, gtfs_id FROM stop_map WHERE stop_code LIKE ? ORDER BY stop_code LIMIT 20",
            (f"%{q}%",),
        ).fetchall()
    return jsonify({"query": q, "results": [dict(r) for r in rows]})


@app.route("/debug/search/<query>")
def debug_search(query):
    try:
        feed = fetch_feed()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except requests.RequestException:
        return jsonify({"error": "Could not reach Translink API"}), 502
    stop_ids = set()
    for entity in feed.entity:
        if entity.HasField("trip_update"):
            for stu in entity.trip_update.stop_time_update:
                stop_ids.add(stu.stop_id)
    matches = [s for s in stop_ids if query in s]
    return jsonify({"query": query, "matches": sorted(matches), "total_in_feed": len(stop_ids)})


@app.route("/stars", methods=["GET"])
def list_stars():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT stop_id, label, created_at FROM starred_stops ORDER BY label"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/stars", methods=["POST"])
def upsert_star():
    body = request.get_json(silent=True) or {}
    stop_id = body.get("stop_id", "").strip()
    label = body.get("label", "").strip()
    if not stop_id or not label:
        return jsonify({"error": "stop_id and label are required"}), 400
    created_at = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO starred_stops (stop_id, label, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(stop_id) DO UPDATE SET label = excluded.label
            """,
            (stop_id, label, created_at),
        )
    return jsonify({"status": "ok", "stop_id": stop_id})


@app.route("/stars/<stop_id>", methods=["DELETE"])
def delete_star(stop_id):
    with get_db() as conn:
        conn.execute("DELETE FROM starred_stops WHERE stop_id = ?", (stop_id,))
    return jsonify({"status": "ok"})


@app.route("/settings", methods=["GET"])
def get_settings():
    key = os.getenv("TRANSLINK_API_KEY", "")
    return jsonify({"key_set": bool(key.strip())})


@app.route("/settings", methods=["POST"])
def save_settings():
    global TRANSLINK_API_KEY

    body = request.get_json(silent=True) or {}
    new_key = body.get("translink_api_key", "")

    if not new_key or not new_key.strip():
        return jsonify({"error": "API key cannot be blank"}), 400

    new_key = new_key.strip()

    env_path = Path(os.path.dirname(__file__)) / ".env"
    new_line = f"TRANSLINK_API_KEY={new_key}\n"

    if env_path.exists():
        content = env_path.read_text()
        if re.search(r"^TRANSLINK_API_KEY=", content, re.MULTILINE):
            content = re.sub(r"^TRANSLINK_API_KEY=.*$", f"TRANSLINK_API_KEY={new_key}", content, flags=re.MULTILINE)
        else:
            content = content.rstrip("\n") + "\n" + new_line
        env_path.write_text(content)
    else:
        env_path.write_text(new_line)

    os.environ["TRANSLINK_API_KEY"] = new_key
    TRANSLINK_API_KEY = new_key

    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5004, debug=False)
