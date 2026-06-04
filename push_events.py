import argparse
import json
import os
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

QUANTUM_API_URL = os.getenv("QUANTUM_API_URL", "http://localhost:3002/runs").rstrip("/")
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


def weighted_allocate(symbols, scores):
    MIN_WEIGHT_PCT = 2.5
    MAX_WEIGHT_PCT = 40.0
    SCORE_EXPONENT = 1.5
    n = len(symbols)
    if n == 0:
        return []

    if n * MAX_WEIGHT_PCT < 100.0 or n * MIN_WEIGHT_PCT > 100.0:
        equal = 100.0 / n
        rounded = [round(equal, 4)] * n
        rounded[-1] = round(100.0 - sum(rounded[:-1]), 4)
        return [{"symbol": s, "allocationPct": p} for s, p in zip(symbols, rounded)]

    raw = [max(s, 0.0) ** SCORE_EXPONENT for s in scores]
    total_raw = sum(raw) or 1.0
    pcts = [r / total_raw * 100.0 for r in raw]

    for _ in range(50):
        for i in range(n):
            if pcts[i] < MIN_WEIGHT_PCT - 1e-9:
                pcts[i] = MIN_WEIGHT_PCT
            if pcts[i] > MAX_WEIGHT_PCT + 1e-9:
                pcts[i] = MAX_WEIGHT_PCT
        diff = 100.0 - sum(pcts)
        if abs(diff) < 1e-6:
            break
        if diff > 0:
            eligible = [i for i in range(n) if pcts[i] < MAX_WEIGHT_PCT - 1e-9]
            if not eligible:
                break
            headroom = sum(MAX_WEIGHT_PCT - pcts[i] for i in eligible) or 1.0
            for i in eligible:
                pcts[i] += diff * (MAX_WEIGHT_PCT - pcts[i]) / headroom
        else:
            eligible = [i for i in range(n) if pcts[i] > MIN_WEIGHT_PCT + 1e-9]
            if not eligible:
                break
            excess = sum(pcts[i] - MIN_WEIGHT_PCT for i in eligible) or 1.0
            for i in eligible:
                pcts[i] += diff * (pcts[i] - MIN_WEIGHT_PCT) / excess

    rounded = [round(p, 4) for p in pcts]
    drift = round(100.0 - sum(rounded), 4)
    if abs(drift) > 0:
        idx = max(range(n), key=lambda i: rounded[i])
        rounded[idx] = round(rounded[idx] + drift, 4)

    return [{"symbol": sym, "allocationPct": pct} for sym, pct in zip(symbols, rounded)]


def push_events(
    file_path: str = "prediction_events.json",
    basket_size: int = 4,
    depth: str = "tree",
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

    print(f"\n[Step 1] Running Quantum pipeline for event: {title}")
    run_payload = {"basketSize": basket_size, "depth": depth, "description": description, "title": title}
    resp = requests.post(QUANTUM_API_URL, json=run_payload, headers=_headers(), timeout=120)
    if resp.status_code not in (200, 201):
        print(f"Quantum run failed ({resp.status_code}): {resp.text}")
        return
    run_data = resp.json()
    print(f"Pipeline complete. Run ID: {run_data.get('runId')}")

    print("\n[Step 2] Building market payload from run outcomes...")
    mapped_outcomes = []
    for outcome in run_data.get("outcomes", []):
        basket_data = outcome.get("quantumBasket") or outcome.get("classicalBasket") or {}
        selected_symbols = basket_data.get("selected", [])
        if not selected_symbols:
            continue
        candidate_scores = {c["symbol"]: c["score"] for c in outcome.get("candidates", [])}
        scores = [candidate_scores.get(sym, 0.5) for sym in selected_symbols]
        allocations = weighted_allocate(selected_symbols, scores)
        mapped_stocks = []
        for alloc in allocations:
            mapped_stocks.append({
                "symbol": alloc["symbol"],
                "name": alloc["symbol"],
                "allocationPct": alloc["allocationPct"]
            })
        mapped_outcomes.append({
            "outcomeId": outcome.get("outcomeId", ""),
            "label": outcome.get("label", ""),
            "stocks": mapped_stocks
        })

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    closes_at = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat().replace("+00:00", "Z")
    market_payload = {
        "eventId": run_data.get("runId", ""),
        "headline": title,
        "title": title,
        "description": description,
        "sourceLink": event.get("source_story_id", ""),
        "outcomes": mapped_outcomes,
        "opensAt": now_iso,
        "closesAt": closes_at,
        "brokerMode": "vantage"
    }

    print("\n[Step 3] POSTing market payload to Cascade API")
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
    ap = argparse.ArgumentParser(description="Push a random prediction event through sync Quantum pipeline, then POST to markets.")
    ap.add_argument("--file", default="prediction_events.json")
    ap.add_argument("--basket-size", type=int, default=4)
    ap.add_argument("--depth", default="tree")
    args = ap.parse_args()

    push_events(
        file_path=args.file,
        basket_size=args.basket_size,
        depth=args.depth,
    )


if __name__ == "__main__":
    main()
