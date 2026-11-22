#!/usr/bin/env python3
"""Dump raw Google Health API responses so the collector can be written against
real payloads instead of guessed ones.

The REST reference documents the rollup *value* messages (countSum,
millimetersSum, kcalSum, ...) but not the JSON key each one hangs off inside a
DailyRollupDataPoint, nor whether paired devices expose a battery level, nor the
shape of the sleep-temperature derivation. This probe answers all of that from
one account in a couple of dozen requests.

Usage:
    python scripts/probe_google_health.py                 # uses stored tokens
    python scripts/probe_google_health.py --authorize     # run consent first
    python scripts/probe_google_health.py --authorize --manual
    python scripts/probe_google_health.py --days 7 --out probe.json

Nothing is written to Victoria Metrics and no state file is touched.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Config  # noqa: E402
from src.google_auth import GoogleAuth  # noqa: E402

# Data types queried through dailyRollUp, with the range ceiling each one
# allows. The four capped types are documented as 14-day maximum.
ROLLUP_TYPES = {
    "steps": 90,
    "distance": 90,
    "total-calories": 14,
    "active-minutes": 14,
    "active-zone-minutes": 90,
    "time-in-heart-rate-zone": 90,
    "calories-in-heart-rate-zone": 14,
}

# The daily-* types reject dailyRollUp ("DailyRollup is not supported ...,
# but the following actions are supported: list, reconcile"). They are already
# daily-granularity, so a filtered list over a range yields one point per day.
LIST_TYPES = {
    "steps": 100,
    "daily-resting-heart-rate": 100,
    "daily-heart-rate-zones": 100,
    "daily-heart-rate-variability": 100,
    "daily-oxygen-saturation": 100,
    "daily-respiratory-rate": 100,
    "daily-vo2-max": 100,
    "daily-sleep-temperature-derivations": 100,
    "sleep": 25,
    "heart-rate": 100,
}


def filter_candidates(data_type: str, start: str, end: str) -> list[tuple[str, dict]]:
    """Candidate query params for a list call, most specific first.

    The filter field path differs between interval-based, sample-based and
    daily-summary data types, and the reference does not say which applies to
    which. Trying them in order and recording the winner resolves it from the
    API itself.
    """
    return [
        (
            "interval.civil_start_time",
            {
                "filter": f'{data_type}.interval.civil_start_time >= "{start}" '
                f'AND {data_type}.interval.civil_start_time < "{end}"'
            },
        ),
        (
            "sample_time.civil_time",
            {
                "filter": f'{data_type}.sample_time.civil_time >= "{start}" '
                f'AND {data_type}.sample_time.civil_time < "{end}"'
            },
        ),
        ("date", {"filter": f'{data_type}.date >= "{start}" AND {data_type}.date < "{end}"'}),
        ("no-filter", {}),
    ]


def civil(d: datetime) -> dict:
    """Render a date as a CivilDateTime.

    CivilDateTime nests a google.type.Date under `date`; passing year/month/day
    at the top level is rejected with INVALID_ARGUMENT. `time` is optional and
    defaults to midnight.
    """
    return {"date": {"year": d.year, "month": d.month, "day": d.day}}


def probe(auth: GoogleAuth, days: int) -> dict:
    """Collect raw responses for every candidate endpoint."""
    token = auth.get_valid_token()
    headers = {"Authorization": f"Bearer {token}"}
    base = Config.GOOGLE_API_BASE_URL

    end = datetime.now()
    start = end - timedelta(days=days)
    results: dict = {
        "probed_at": end.isoformat(),
        "range": {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d")},
    }

    def record(label: str, response: requests.Response) -> None:
        entry: dict = {"status": response.status_code}
        try:
            entry["body"] = response.json()
        except ValueError:
            entry["body"] = response.text[:2000]
        results[label] = entry
        marker = "ok " if response.ok else "ERR"
        print(f"  [{marker}] {label} -> {response.status_code}")

    print("\n== identity / profile / devices ==")
    for label, url in (
        ("profile", f"{base}/users/me/profile"),
        ("identity", f"{base}/users/me/identity"),
        ("pairedDevices", f"{base}/users/me/pairedDevices"),
    ):
        try:
            record(label, requests.get(url, headers=headers, timeout=30))
        except requests.RequestException as e:
            results[label] = {"error": str(e)}
            print(f"  [ERR] {label} -> {e}")

    print(f"\n== dailyRollUp ({days}d) ==")
    for data_type, max_days in ROLLUP_TYPES.items():
        window_start = end - timedelta(days=min(days, max_days))
        body = {
            "range": {"start": civil(window_start), "end": civil(end)},
            "windowSizeDays": 1,
        }
        url = f"{base}/users/me/dataTypes/{data_type}/dataPoints:dailyRollUp"
        try:
            record(
                f"rollup:{data_type}", requests.post(url, headers=headers, json=body, timeout=30)
            )
        except requests.RequestException as e:
            results[f"rollup:{data_type}"] = {"error": str(e)}
            print(f"  [ERR] rollup:{data_type} -> {e}")

    print("\n== dataPoints.list ==")
    list_start = start.strftime("%Y-%m-%d")
    list_end = end.strftime("%Y-%m-%d")
    for data_type, page_size in LIST_TYPES.items():
        url = f"{base}/users/me/dataTypes/{data_type}/dataPoints"
        for filter_name, extra in filter_candidates(data_type, list_start, list_end):
            params = {"pageSize": page_size, **extra}
            try:
                response = requests.get(url, headers=headers, params=params, timeout=30)
            except requests.RequestException as e:
                results[f"list:{data_type}"] = {"error": str(e)}
                print(f"  [ERR] list:{data_type} ({filter_name}) -> {e}")
                break

            if response.ok:
                record(f"list:{data_type}", response)
                results[f"list:{data_type}"]["filter_used"] = filter_name
                break
            # A scope error will not improve by changing the filter.
            if response.status_code == 403:
                record(f"list:{data_type}", response)
                break
            print(f"  [..] list:{data_type} ({filter_name}) -> {response.status_code}")
        else:
            results[f"list:{data_type}"] = {"status": response.status_code, "body": response.json()}
            print(f"  [ERR] list:{data_type} -> no filter form accepted")

    return results


def summarize(results: dict) -> None:
    """Print the JSON keys that the collector needs to know."""
    print("\n== shapes ==")
    for label, entry in results.items():
        if not isinstance(entry, dict) or "body" not in entry:
            continue
        body = entry["body"]
        if not isinstance(body, dict):
            continue

        points = body.get("rollupDataPoints") or body.get("dataPoints")
        if points:
            sample = points[0]
            keys = [k for k in sample if k not in ("civilStartTime", "civilEndTime", "name")]
            print(f"  {label}: {len(points)} points, value keys={keys}")
            for key in keys:
                print(f"      {key} = {json.dumps(sample[key])[:200]}")
        elif entry["status"] == 200:
            print(f"  {label}: 200 but empty ({list(body)[:6]})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorize", action="store_true", help="run the consent flow first")
    parser.add_argument(
        "--print-auth-url",
        action="store_true",
        help="print the consent URL and exit, so it can be opened by hand",
    )
    parser.add_argument(
        "--code",
        help="authorization code copied from the redirect URL (pairs with --print-auth-url)",
    )
    parser.add_argument(
        "--manual", action="store_true", help="paste the code instead of a callback"
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="do not try to launch a browser (headless/WSL)",
    )
    parser.add_argument("--days", type=int, default=7, help="days of history to request")
    parser.add_argument("--out", default="probe_google_health.json", help="where to write raw JSON")
    args = parser.parse_args()

    Config.validate_google()
    Config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    auth = GoogleAuth()

    if args.print_auth_url:
        url = auth.get_authorization_url()
        url_file = Config.DATA_DIR / "authorize_url.txt"
        url_file.write_text(url + "\n")
        print("\nOpen this URL in a browser and approve access:\n")
        print(url)
        print(f"\n(also saved to {url_file})")
        print(
            "\nThe browser will then fail to load localhost:8080 - that is expected.\n"
            "Copy the `code` value out of the address bar and re-run with:\n"
            "    python scripts/probe_google_health.py --code '<the code>'"
        )
        return 0

    if args.code:
        auth.exchange_code_for_token(args.code)
        print("Authorization complete.")
    elif args.authorize or not auth.is_authorized():
        auth.authorize(manual=args.manual, open_browser=not args.no_browser)

    results = probe(auth, args.days)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(results, indent=2, default=str))
    summarize(results)
    print(f"\nRaw responses written to {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
