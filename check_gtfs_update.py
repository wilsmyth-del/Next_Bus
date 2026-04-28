#!/usr/bin/env python3
"""
check_gtfs_update.py — standalone cron script
Calls the transit POC API to check for and download a new GTFS static zip.
Exit 0 on success (any expected result). Exit 1 on exception.
"""

import sys
import requests

API_URL = "http://localhost:5004/check-gtfs-update"
TIMEOUT = 120


def main():
    try:
        response = requests.post(API_URL, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
        action = data.get("action", "unknown")

        if action == "downloaded":
            print(f"GTFS update: new zip downloaded. date={data.get('new_zip_date', '')}")
        elif action == "already_current":
            print(f"GTFS update: already current. date={data.get('new_zip_date', '')}")
        elif action == "not_found":
            print(f"GTFS update: feed not found at source. url={data.get('checked_url', '')}")
        elif action == "error":
            print(f"GTFS update: error reported by service. detail={data.get('error', '')}")
        else:
            print(f"GTFS update: unexpected action '{action}'. Full response: {data}")

    except Exception as e:
        print(f"GTFS update: exception — {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()