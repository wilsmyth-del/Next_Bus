# Transit Server

Checking the next bus used to take 15–20 taps through the TransLink app — open it, wait for it to load, find the stop, scroll through the schedule. This is one tap. You open it and your next buses are already there.

That's the whole pitch.

---

## What It Does

- **Real-time next bus arrivals** by stop code — pulls live data from the TransLink GTFS Realtime feed
- **Starred stops** — save your regular stops so they're one tap from the home screen
- **Three-tier arrival data** — live GPS position, approximate from trip updates, or static schedule fallback when the feed is quiet
- **GTFS static schedule** — loaded from TransLink's published GTFS zip, used as fallback for any trip not in the live feed
- **Auto GTFS update** — checks TransLink's GTFS history archive and downloads the latest zip when a newer one is available

---

## Requirements

- Python 3.9+
- A **TransLink Open API key** — sign up at [developer.translink.ca](https://developer.translink.ca/)

> **Note on API terms:** This app is intended for personal, self-hosted use. Before publishing or distributing it, verify that your use complies with the [TransLink Open API terms of use](https://developer.translink.ca/).

---

## Setup

**1. Clone the repo**

```bash
git clone <repo-url>
cd transit_poc
```

**2. Create a virtual environment and install dependencies**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**3. Configure your API key**

```bash
cp .env.example .env
```

Open `.env` and set your TransLink API key:

```
TRANSLINK_API_KEY=your_key_here
```

**4. Run the app**

```bash
python app.py
```

The server starts on `http://localhost:5004`.

For always-on use, deploy as a systemd service — see the systemd section below.

**5. Load GTFS data on first run**

The static schedule (used as fallback when live data is thin) needs to be loaded before the app is useful:

```bash
curl -X POST http://localhost:5004/refresh
```

This reads the local `google_transit.zip` and populates the schedule database. Run it again after any GTFS update.

---

## Systemd Service (optional)

To run Transit Server as a background service that starts on boot:

```ini
[Unit]
Description=Transit Server
After=network.target

[Service]
WorkingDirectory=/path/to/transit_poc
ExecStart=/path/to/transit_poc/.venv/bin/python app.py
Restart=on-failure
EnvironmentFile=/path/to/transit_poc/.env

[Install]
WantedBy=multi-user.target
```

Save to `/etc/systemd/system/transit.service`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable transit
sudo systemctl start transit
```

---

## Usage

**Starring stops**

From the home screen, tap any stop to pull up arrivals. Use the star icon to save it. Starred stops appear on the home screen for one-tap access.

**Updating your API key**

Open **Settings** in the app. Enter your new TransLink API key and save. The key is written to your `.env` file and takes effect immediately — no restart required.

**Triggering a GTFS update**

To check for a newer GTFS zip and download it if one exists:

```bash
curl -X POST http://localhost:5004/check-gtfs-update
```

After a download, `/refresh` is called automatically to reload the static schedule.

To reload the schedule manually (e.g. after replacing `google_transit.zip` by hand):

```bash
curl -X POST http://localhost:5004/refresh
```

---

## Stack

- **Flask** — web framework and API layer
- **SQLite** — local storage for starred stops, stop map, route map, and static schedule
- **TransLink GTFS Realtime API** — live and trip-update arrival data
- **TransLink GTFS static data** — schedule fallback, auto-updated from the TransLink history archive
- **protobuf / gtfs-realtime-bindings** — feed parsing