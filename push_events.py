import argparse
import json
import os
import random
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

# Quantum service base URL (no trailing slash)
QUANTUM_API_URL = os.getenv("QUANTUM_API_URL", "http://localhost:3002/runs").rstrip("/")
# Bitzaurus admin markets endpoint
MARKETS_API_URL = os.getenv("MARKETS_API_URL", "http://localhost:8800/api/admin/markets").rstrip("/")

ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "").strip()


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if ADMIN_API_TOKEN:
        h["Authorization"] = f"Bearer {ADMIN_API_TOKEN}"
    return h


def _load_events(file_path: str) -> list:
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    events = data.get("events", [])
    if not isinstance(events, list):
        return []
    return events


def _start_run(title: str, description: str, basket_size: int, depth: str) -> str:
    payload = {
        "basketSize": basket_size,
        "depth": depth,
        "description": description,
        "title": title,
    }
    resp = requests.post(f"{QUANTUM_API_URL}/start", json=payload, headers=_headers(), timeout=30)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Failed to start run ({resp.status_code}): {resp.text}")
    data = resp.json()
    run_id = data.get("runId") or data.get("run_id")
    if not run_id:
        raise RuntimeError(f"No runId in response: {data}")
    return run_id


def _poll_done(run_id: str, max_attempts: int = 60, sleep_s: int = 5) -> None:
    status_url = f"{QUANTUM_API_URL}/{run_id}/status"
    for attempt in range(max_attempts):
        resp = requests.get(status_url, headers=_headers(), timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            status = data.get("status")
            print(f"Attempt {attempt + 1}/{max_attempts}: Status is '{status}'")
            if status in ("done", "completed"):
                return
            if status == "error":
                raise RuntimeError(f"Run failed: {data.get('error')}")
        else:
            print(f"Status check failed ({resp.status_code})")
        time.sleep(sleep_s)
    raise TimeoutError("Timeout waiting for run completion")


def _get_allocations_payload(run_id: str, source: str, broker_mode: str) -> dict:
    url = f"{QUANTUM_API_URL}/{run_id}/allocations"
    params = {"source": source, "brokerMode": broker_mode}
    resp = requests.get(url, params=params, headers=_headers(), timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch allocations ({resp.status_code}): {resp.text}")
    return resp.json()


def push_events(
    file_path: str = "prediction_events.json",
    basket_size: int = 4,
    depth: str = "tree",
    source: str = "quantum",
    broker_mode: str = "mock",
) -> None:
    events = _load_events(file_path)
    if not events:
        print(f"No events found in {file_path}.")
        return

    event = random.choice(events)
    title = (event.get("title") or "").strip()
    description = (event.get("description") or "").strip()
    if not title:
        print("Selected event does not have a title.")
        return

    print(f"\n[Step 1] Starting run for event: {title}")
    run_id = _start_run(title, description, basket_size=basket_size, depth=depth)
    print(f"Successfully started run. Run ID: {run_id}")

    print("\n[Step 2] Polling for run completion...")
    _poll_done(run_id)

    print(f"\n[Step 3] Fetching allocations from Quantum API (source={source})")
    market_payload = _get_allocations_payload(run_id, source=source, broker_mode=broker_mode)

    print("\n[Step 4] POSTing Market create body to Bitzaurus API")
    resp = requests.post(MARKETS_API_URL, json=market_payload, headers=_headers(), timeout=30)
    try:
        resp_content = json.dumps(resp.json(), indent=2)
    except ValueError:
        resp_content = resp.text

    if resp.status_code in (200, 201):
        print(f"Successfully created market event.\nResponse:\n{resp_content}")
    else:
        print(f"Failed to create market event ({resp.status_code}).\nResponse:\n{resp_content}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Push a random prediction event through Quantum, then POST to markets using /allocations.")
    ap.add_argument("--file", default="prediction_events.json")
    ap.add_argument("--basket-size", type=int, default=4)
    ap.add_argument("--depth", default="tree")
    ap.add_argument("--source", choices=["quantum", "classical"], default="quantum")
    ap.add_argument("--broker-mode", default="mock")
    args = ap.parse_args()

    push_events(
        file_path=args.file,
        basket_size=args.basket_size,
        depth=args.depth,
        source=args.source,
        broker_mode=args.broker_mode,
    )


if __name__ == "__main__":
    main()
