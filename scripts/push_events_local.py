import json
import random
import requests
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path, override=True)

_q_base = os.getenv("QUANTUM_API_URL", "http://localhost:3002").rstrip("/").removesuffix("/runs")
QUANTUM_API_URL = _q_base + "/runs"
MARKETS_API_URL = os.getenv("MARKETS_API_URL", "http://localhost:8800/api/admin/markets").rstrip("/")

# Fetch token if needed, otherwise it sends without auth
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "").strip()

def weighted_allocate(symbols, scores):
    """
    Local allocation calculation matching the newer JS algorithm
    (bounds 2.5% to 40.0%, score exponent 1.5).
    """
    MIN_WEIGHT_PCT = 2.5
    MAX_WEIGHT_PCT = 40.0
    SCORE_EXPONENT = 1.5
    n = len(symbols)
    if n == 0:
        return []

    # Fallback to equal weighting if bounds are mathematically impossible
    if n * MAX_WEIGHT_PCT < 100.0 or n * MIN_WEIGHT_PCT > 100.0:
        equal = 100.0 / n
        rounded = [round(equal, 4)] * n
        rounded[-1] = round(100.0 - sum(rounded[:-1]), 4)
        return [{"symbol": s, "allocationPct": p} for s, p in zip(symbols, rounded)]

    # Initial weights from score^1.5
    raw = [max(s, 0.0) ** SCORE_EXPONENT for s in scores]
    total_raw = sum(raw) or 1.0
    pcts = [r / total_raw * 100.0 for r in raw]

    # Iterative cap-and-redistribute
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

    # Round to 4 decimal places and resolve drift on heaviest ticker
    rounded = [round(p, 4) for p in pcts]
    drift = round(100.0 - sum(rounded), 4)
    if abs(drift) > 0:
        idx = max(range(n), key=lambda i: rounded[i])
        rounded[idx] = round(rounded[idx] + drift, 4)

    return [{"symbol": sym, "allocationPct": pct} for sym, pct in zip(symbols, rounded)]

def push_events(file_path="prediction_events.json"):
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
        return

    events = data.get("events", [])
    if not events:
        print("No events found in prediction_events.json.")
        return

    headers = {
        "Content-Type": "application/json"
    }
    if ADMIN_API_TOKEN:
        headers["Authorization"] = f"Bearer {ADMIN_API_TOKEN}"

    # Pick a random event to push
    event = random.choice(events)
    title = event.get("title", "")
    description = event.get("description", "")
    if not title:
        print("Selected event does not have a title.")
        return

    print(f"\n[Step 1] Running Quantum pipeline for event: {title}")
    run_payload = {"basketSize": 4, "depth": "tree", "description": description, "title": title}
    try:
        resp = requests.post(QUANTUM_API_URL, json=run_payload, headers=headers, timeout=None)
        if resp.status_code not in (200, 201):
            print(f"Quantum run failed ({resp.status_code}): {resp.text}")
            return
        run_data = resp.json()
        print(f"Pipeline complete. Run ID: {run_data.get('runId')}")
    except Exception as e:
        print(f"Error running Quantum pipeline: {e}")
        return

    # ==========================================
    # STEP 2: Calculate allocationPct locally
    # ==========================================
    print("\n[Step 2] Performing local allocation calculations...")
    try:
        mapped_outcomes = []
        for outcome in run_data.get("outcomes", []):
            basket_data = outcome.get("quantumBasket") or outcome.get("classicalBasket") or {}
            selected_symbols = basket_data.get("selected", [])
            if not selected_symbols:
                print(f"Outcome {outcome.get('outcomeId')} has an empty basket.")
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
        opens_at = now_iso
        closes_at = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat().replace("+00:00", "Z")

        market_payload = {
            "eventId": run_data.get("runId", ""),
            "headline": title,
            "title": title,
            "description": description,
            "sourceLink": event.get("source_story_id", ""),
            "outcomes": mapped_outcomes,
            "opensAt": opens_at,
            "closesAt": closes_at,
            "brokerMode": "vantage"
        }

        print(f"Posting finalized payload to markets API: {MARKETS_API_URL}")
        market_resp = requests.post(MARKETS_API_URL, json=market_payload, headers=headers, timeout=30)

        try:
            resp_content = json.dumps(market_resp.json(), indent=2)
        except ValueError:
            resp_content = market_resp.text

        if market_resp.status_code in [200, 201]:
            print(f"Successfully created Cascade market event!\nResponse:\n{resp_content}")
        else:
            print(f"Failed to create Cascade market event. Status: {market_resp.status_code}\nResponse:\n{resp_content}")

    except Exception as e:
        print(f"Error processing allocation or posting market: {e}")

if __name__ == "__main__":
    push_events()
