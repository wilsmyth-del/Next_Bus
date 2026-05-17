# Next Bus

Checking the next bus used to take 15–20 taps — open the app, wait for it to load, find the stop, scroll through the schedule. This is one tap. You open it and your next buses are already there.

That's the whole pitch.

---

## Works with any GTFS agency

This app is built on [GTFS](https://gtfs.org/) — the open standard used by transit agencies worldwide. TransLink (Vancouver) is the default, but you can point it at any agency that publishes a GTFS Realtime feed. Change two lines in your `.env` and it works for your city.

---

## What It Does

- **Real-time next bus arrivals** by stop code — pulls live data from a GTFS Realtime feed
- **Starred stops** — save your regular stops for one-tap access from the home screen
- **Three-tier arrival data** — live GPS position, approximate from trip updates, or static schedule fallback when the feed is quiet
- **Camera scan** — point your phone camera at a bus stop sign to look up arrivals without typing
- **GTFS static schedule** — loaded from a GTFS zip, used as fallback for any trip not in the live feed
- **Auto GTFS update** — checks for a newer GTFS zip and downloads it when one is available
- **In-app settings** — configure your API keys and feed URL directly in the app, no server restart needed

---

## Requirements

- Python 3.9+
- A **GTFS Realtime feed URL** for your transit agency
- A **Transit API key** (if your agency requires one — e.g. TransLink: [developer.translink.ca](https://developer.translink.ca/))
- A **Google Gemini API key** for camera scan — free tier at [aistudio.google.com](https://aistudio.google.com) (optional, app works without it)

> **Note on API terms:** This app is intended for personal, self-hosted use. Before publishing or distributing it, verify that your use complies with your transit agency's API terms of use.

---

## Setup

**1. Clone the repo**

```bash
git clone <repo-url>
cd transit-server
```

**2. Create a virtual environment and install dependencies**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**3. Configure your environment**

```bash
cp .env.example .env
```

Open `.env` and fill in your values:

```
TRANSIT_API_KEY=your_transit_api_key_here
GTFS_REALTIME_URL=https://gtfsapi.translink.ca/v3/gtfsrealtime?apikey={key}
GEMINI_API_KEY=your_gemini_api_key_here
```

- **`TRANSIT_API_KEY`** — your agency's API key. Use `{key}` in the URL and the app substitutes it automatically. If your feed is open (no key required), leave this blank and omit `{key}` from the URL.
- **`GTFS_REALTIME_URL`** — your agency's GTFS Realtime feed URL. The TransLink URL is shown above as an example.
- **`GEMINI_API_KEY`** — needed only for camera scan. Get a free key at [aistudio.google.com](https://aistudio.google.com). Leave blank to disable the scan button.

You can also set all three directly in the app's **Settings** tab — no restart needed.

**4. Run the app**

```bash
python app.py
```

The server starts on `http://localhost:5004`.

**5. Load GTFS data on first run**

The static schedule (used as fallback when live data is thin) needs to be loaded before the app is useful:

```bash
curl -X POST http://localhost:5004/refresh
```

This reads `google_transit.zip` from the repo root and populates the schedule database. Run it again after any GTFS update. You'll also need a GTFS static zip — download one from your agency and place it at the repo root as `google_transit.zip`.

---

## Systemd Service (optional)

To run as a background service that starts on boot:

```ini
[Unit]
Description=Next Bus
After=network.target

[Service]
WorkingDirectory=/path/to/transit-server
ExecStart=/path/to/transit-server/.venv/bin/python app.py
Restart=on-failure
EnvironmentFile=/path/to/transit-server/.env

[Install]
WantedBy=multi-user.target
```

Save to `/etc/systemd/system/next-bus.service`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable next-bus
sudo systemctl start next-bus
```

---

## Usage

**Starred stops**

From the home screen, tap any stop to pull up arrivals. Use the star icon to save it. Starred stops appear on the home screen for one-tap access.

**Camera scan**

On the Search tab, tap **Scan stop sign** and point your camera at a bus stop. The app reads the stop number and looks it up automatically. Requires a Gemini API key in Settings.

**Updating GTFS data**

Open **Settings** and tap **Update GTFS Data** to check for a newer schedule zip and download it if one is available.

**Updating your API keys or feed URL**

Open **Settings**. All three fields (Transit API key, GTFS Realtime URL, Gemini API key) can be updated live — changes are written to `.env` and take effect immediately without a restart.

---

## GTFS auto-update notes

The auto-update feature (`Update GTFS Data` button and the `/check-gtfs-update` endpoint) uses TransLink's dated archive URL format. If you're using a different agency, you may need to adapt this to your agency's static GTFS download URL and update cadence. The rest of the app — arrivals, starred stops, static schedule fallback — works with any GTFS feed.

---

## Stack

- **Flask** — web framework and API layer
- **SQLite** — local storage for starred stops, stop map, route map, and static schedule
- **GTFS Realtime** — live and trip-update arrival data (any agency)
- **GTFS static data** — schedule fallback, loadable from any standard GTFS zip
- **protobuf / gtfs-realtime-bindings** — feed parsing
- **Google Gemini** — vision model powering camera scan (direct API, no intermediary)
