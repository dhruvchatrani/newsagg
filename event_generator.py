import json
import re
import os
import requests
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import config
from logger_setup import get_logger

logger = get_logger("newsagg.event_generator")

import time
_env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=_env_path, override=True)

QUANTUM_API_URL = os.getenv("QUANTUM_API_URL", "http://localhost:3002/runs").rstrip("/")
MARKETS_API_URL = os.getenv("MARKETS_API_URL", "http://localhost:8800/api/admin/markets").rstrip("/")
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "").strip()
QUANTUM_API_URL = os.getenv("QUANTUM_API_URL", "https://blackbox-quantum.bitzaurus.com").strip()
BITZAURUS_API_URL = os.getenv("BITZAURUS_API_URL", "https://api.bitzaurus.com/api/admin/markets").strip()

def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if ADMIN_API_TOKEN:
        h["Authorization"] = f"Bearer {ADMIN_API_TOKEN}"
    return h

def _start_run(title: str, description: str, basket_size: int = 4, depth: str = "low") -> str:
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
            if status in ("done", "completed"):
                return
            if status == "error":
                raise RuntimeError(f"Run failed: {data.get('error')}")
        time.sleep(sleep_s)
    raise TimeoutError("Timeout waiting for run completion")

def _get_allocations_payload(run_id: str, source: str = "quantum", broker_mode: str = "mock") -> dict:
    url = f"{QUANTUM_API_URL}/{run_id}/allocations"
    params = {"source": source, "brokerMode": broker_mode}
    resp = requests.get(url, params=params, headers=_headers(), timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch allocations ({resp.status_code}): {resp.text}")
    return resp.json()

def chunk_list(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

# def parse_ollama_json(resp):
#     text = resp.get("response", "").strip()
#     if not text:
#         text = resp.get("thinking", "").strip()
#     if not text and "message" in resp:
#         msg = resp["message"]
#         text = msg.get("content", "").strip()
#         if not text:
#             text = msg.get("thinking", "").strip()
#     if not text:
#         raise ValueError("Empty response/thinking from Ollama.")
#     
#     text = text.strip()
#     array_match = re.search(r'\[.*\]', text, re.DOTALL)
#     if array_match:
#         text = array_match.group(0)
#     else:
#         object_match = re.search(r'\{.*\}', text, re.DOTALL)
#         if object_match:
#             text = object_match.group(0)
#     return json.loads(text)

def clean_and_parse_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        newline_idx = text.find("\n")
        if newline_idx != -1:
            text = text[newline_idx:].strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    return json.loads(text)

def parse_tradability_score(value) -> float:
    """Coerce LLM tradability_score (float, int, or string) to a float in [0, 1]."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    if isinstance(value, str):
        s = value.strip()
        if not s or s.startswith("<"):
            return 0.0
        try:
            return max(0.0, min(1.0, float(s)))
        except ValueError:
            return 0.0
    return 0.0

def call_gemini_api(system_instruction: str, user_prompt: str) -> dict:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "systemInstruction": {
            "parts": [
                {"text": system_instruction}
            ]
        },
        "contents": [
            {
                "parts": [
                    {"text": user_prompt}
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }
    logger.debug(f"Calling Gemini API. System instruction length: {len(system_instruction)}, User prompt length: {len(user_prompt)}")
    start_time = time.time()
    response = requests.post(url, json=payload, headers=headers, timeout=60)
    elapsed = time.time() - start_time
    logger.debug(f"Gemini API responded in {elapsed:.2f}s with status {response.status_code}")
    if response.status_code == 200:
        resp_json = response.json()
        text = resp_json["candidates"][0]["content"]["parts"][0]["text"].strip()
        logger.debug(f"Gemini API raw response text: {text}")
        return clean_and_parse_json(text)
    else:
        logger.error(f"Gemini API error status {response.status_code}: {response.text}")
        raise Exception(f"Gemini API error {response.status_code}: {response.text}")

def generate_events(news_file="news_database.json", assets_file="assets.json", max_events=15, articles=None):
    try:
        with open(assets_file, 'r') as f:
            universe = json.load(f)
    except Exception as e:
        logger.error(f"Error loading assets file {assets_file}: {e}", exc_info=True)
        return False

    if articles is None:
        try:
            with open(news_file, 'r') as f:
                news_data = json.load(f)
            articles = []
            for cat, items in news_data.get("categories", {}).items():
                articles.extend(items)
        except Exception as e:
            logger.error(f"Error loading news file {news_file}: {e}", exc_info=True)
            return False

    # ---------------------------------------------------------
    # OPTIMIZATION: Only process articles with a score >= 0.9
    # ---------------------------------------------------------
    high_value_articles = [a for a in articles if a.get("score", 0) >= 0.9]
    high_value_articles = sorted(high_value_articles, key=lambda x: x.get("score", 0), reverse=True)
    top_articles = high_value_articles[:50] # Hard cap just in case

    if not top_articles:
        logger.info("No articles with score >= 0.9 found. Exiting pipeline.")
        return False

    articles_lite = [{"url": a["url"], "title": a["title"], "snippet": a["snippet"], "source": a["source"]} for a in top_articles]

    universe_str = ", ".join(universe)

    from prompts.event_generator_base import BASE_PROMPT
    from prompts.event_generator_pass1 import PASS1_INSTRUCTION

    system_prompt_base = BASE_PROMPT.format(universe_str=universe_str)

    # ========================================================
    # PASS 1: BATCHED TRIAGE & ASSET MAPPING (5 per batch)
    # ========================================================
    logger.info(f"PASS 1: Triaging {len(articles_lite)} highly-rated articles in batches of 5 to optimize KV caching...")
    batched_articles = list(chunk_list(articles_lite, 5))
    all_triaged_mappings = []

    pass1_system_instruction = system_prompt_base + PASS1_INSTRUCTION

    for index, batch in enumerate(batched_articles):
        logger.info(f" -> Processing Batch {index + 1}/{len(batched_articles)}...")
        
        user_prompt = f"Triage and map this batch of articles:\n{json.dumps(batch)}\n\nOutput strict JSON matching schema:\n[\n  {{\n    \"source_story_id\": \"<exact URL>\",\n    \"mapped_assets\": [\n      {{\n        \"ticker\": \"<EXACT TICKER>\",\n        \"directional_thesis\": \"<Bullish|Bearish|Volatile - explanation>\"\n      }}\n    ]\n  }}\n]"

        try:
            batch_res = call_gemini_api(pass1_system_instruction, user_prompt)
            if isinstance(batch_res, list):
                all_triaged_mappings.extend(batch_res)
            elif isinstance(batch_res, dict):
                if "mappings" in batch_res and isinstance(batch_res["mappings"], list):
                    all_triaged_mappings.extend(batch_res["mappings"])
                elif "triage" in batch_res and isinstance(batch_res["triage"], list):
                    all_triaged_mappings.extend(batch_res["triage"])
                else:
                    for k, v in batch_res.items():
                        if isinstance(v, dict):
                            if "source_story_id" not in v:
                                v["source_story_id"] = k
                            all_triaged_mappings.append(v)
                        elif isinstance(v, list):
                            all_triaged_mappings.append({
                                "source_story_id": k,
                                "mapped_assets": v
                            })
        except Exception as e:
            logger.error(f"     [!] Failed processing batch {index + 1}: {e}", exc_info=True)

    if not all_triaged_mappings:
        logger.warning("No valid asset mappings survived triage across all batches. Exiting.")
        return

    valid_mappings = [m for m in all_triaged_mappings if m.get("mapped_assets")]
    logger.info(f"Triage complete. {len(valid_mappings)} stories successfully mapped to assets.")

    unique_assets = set()
    for item in valid_mappings:
        for asset_obj in item["mapped_assets"]:
            if asset_obj.get("ticker"):
                unique_assets.add(asset_obj["ticker"])

    # ========================================================
    # PYTHON INTERMEDIARY: TOKEN-RESTRICTED PRICE DISCOVERY
    # ========================================================
    logger.info(f"PYTHON STEP: Executing price discovery for {len(unique_assets)} unique tickers via Tavily...")
    price_context = {}
    if config.TAVILY_API_KEY:
        for asset in unique_assets:
            payload = {
                "api_key": config.TAVILY_API_KEY,
                "query": f"current market trading price of {asset} asset today close",
                "search_depth": "basic",
                "max_results": 1
            }
            try:
                tav_resp = requests.post("https://api.tavily.com/search", json=payload, timeout=15)
                if tav_resp.status_code == 200:
                    results = tav_resp.json().get("results", [])
                    if results:
                        raw_snippet = results[0].get("content", "")
                        price_context[asset] = raw_snippet[:150]
                        logger.debug(f"Price context for {asset}: {price_context[asset]}")
            except Exception as e:
                logger.error(f"     [!] Price look-up skipped for {asset}: {e}", exc_info=True)

    article_lookup = {a["url"]: a for a in articles_lite}

    # ========================================================
    # PASS 2: CONTEXT-ISOLATED NARRATIVE EVENT GENERATION
    # ========================================================
    logger.info("PASS 2: Generating catalyst-driven narrative events via isolated single-story chat targets...")
    final_events = []

    from prompts.event_generator_pass2 import PASS2_INSTRUCTION

    pass2_system_instruction = system_prompt_base + PASS2_INSTRUCTION

    for item in valid_mappings:
        url = item.get("source_story_id")
        orig_article = article_lookup.get(url)
        if not orig_article:
            continue

        for asset_obj in item.get("mapped_assets", []):
            ticker = asset_obj.get("ticker")
            thesis = asset_obj.get("directional_thesis")
            if not ticker:
                continue

            asset_price_info = price_context.get(ticker, "No real-time context available.")
            
            isolated_user_prompt = f"""Generate exactly ONE prediction market event for this single mapped configuration:

Target Article Details:
- Title: {orig_article['title']}
- Snippet: {orig_article['snippet']}
- Source: {orig_article['source']}
- URL: {url}

Target Asset Mapping:
- Ticker: {ticker}
- Preliminary Directional Thesis: {thesis}
- Current Web Price Context: {asset_price_info}

Apply ALL title, description, and worthy rules from the system instruction before writing your response.
If ANY quality gate fails, still return the full JSON but set worthy=false with your honest tradability_score.

TITLE REMINDER — short uncertainty question, 30-55 chars.
  VARY your openers across styles: Could/Might/May | Odds of/Chances of | Is X about to/Are we nearing | Betting on/Bullish on
  Do NOT repeat the same opener twice. Full list of options in the system instruction.
  NOT: declarative statements, price targets, tickers, filler words.

Output strict JSON matching schema:
{{
  "source_story_id": "{url}",
  "validation_check": true,
  "tradability_check": true,
  "virality_check": true,
  "worthiness_assessment": true,
  "decision_approved": true,
  "worthy": true,
  "title": "<short uncertainty question — see TITLE PHILOSOPHY in system instruction>",
  "causal_chain": "<Real-World Trigger -> Market Mechanism -> Asset Direction>",
  "description": "<5+ dense sentences: 2+ named facts from snippet, mechanism explanation, binary resolution terms with specific price and weekly timeframe>",
  "linked_assets": ["{ticker}"],
  "directional_impact": "<Bullish|Bearish|Volatile>",
  "category": "Macroeconomics",
  "horizon": "weekly",
  "tradability_score": "<honest float 0.0-1.0>"
}}"""

            try:
                event_obj = call_gemini_api(pass2_system_instruction, isolated_user_prompt)
                if isinstance(event_obj, dict):
                    score = parse_tradability_score(event_obj.get("tradability_score"))
                    event_obj["tradability_score"] = score
                    worthy = event_obj.get("worthy", True)
                    decision = event_obj.get("decision_approved", True)
                    if worthy and decision and score >= 0.90:
                        final_events.append(event_obj)
                        logger.info(f"     [+] Accepted event for {ticker}: {event_obj.get('title', '')[:80]}")
                    else:
                        logger.warning(f"     [~] Dropped low-quality event for {ticker} (worthy={worthy}, decision={decision}, score={score:.2f}): {event_obj.get('title', '')[:80]}")
            except Exception as e:
                logger.error(f"     [!] Failed to generate isolated event for {ticker}: {e}", exc_info=True)

    # ========================================================
    # POST-LLM DEDUPLICATION & METADATA SAVE
    # ========================================================
    deduped_map = {}
    for event in final_events:
        url = event.get("source_story_id")
        if not url:
            continue
        event_score = parse_tradability_score(event.get("tradability_score"))
        event["tradability_score"] = event_score
        existing_score = parse_tradability_score(deduped_map[url].get("tradability_score")) if url in deduped_map else 0.0
        if url not in deduped_map or event_score > existing_score:
            deduped_map[url] = event

    unique_events = sorted(
        list(deduped_map.values()),
        key=lambda x: parse_tradability_score(x.get("tradability_score")),
        reverse=True,
    )
    sliced_events = unique_events[:1]

    output_data = {
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "events_generated": len(sliced_events),
            "engine": f"Gemini API ({config.GEMINI_MODEL})"
        },
        "events": sliced_events
    }

    with open("prediction_events.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)

    logger.info(f"Pipeline complete. {len(sliced_events)} grounded events saved.")

    # ========================================================
    # PUSH: Send each worthy event to the Cascade API via Run -> Poll -> Promote -> Post flow
    # ========================================================
    if sliced_events:
        logger.info(f"PUSHING {len(sliced_events)} events to Bitzaurus (Runs & Markets)...")
        push_headers = {"Content-Type": "application/json"}
        if ADMIN_API_TOKEN:
            push_headers["Authorization"] = f"Bearer {ADMIN_API_TOKEN}"

        pushed, failed = 0, 0
        for event in sliced_events:
            title = event.get("title", "")
            description = event.get("description", "")
            if not title:
                continue

            logger.info(f"Processing event: '{title[:60]}'")
            
            # Step 1: Start Run
            run_payload = {
                "title": title,
                "description": description,
                "basketSize": 4,
                "depth": "tree"
            }
            try:
                logger.info(f" -> Starting run on {QUANTUM_API_URL}...")
                logger.debug(f"Quantum run payload: {json.dumps(run_payload)}")
                start_resp = requests.post(f"{QUANTUM_API_URL}/runs/start", json=run_payload, timeout=30)
                start_resp.raise_for_status()
                run_data = start_resp.json()
                logger.debug(f"Quantum start response: {json.dumps(run_data)}")
                run_id = run_data.get("runId")
                if not run_id:
                    logger.error("  [x] Failed: No runId returned in start response.")
                    failed += 1
                    continue
            except Exception as e:
                logger.error(f"  [!] Error starting run: {e}", exc_info=True)
                failed += 1
                continue

            # Step 2: Poll Run Status
            logger.info(f" -> Run started with ID: {run_id}. Polling status...")
            status = "queued"
            success_status = False
            for _ in range(60): # 120 seconds max timeout
                time.sleep(2)
                try:
                    status_resp = requests.get(f"{QUANTUM_API_URL}/runs/{run_id}/status", timeout=15)
                    status_resp.raise_for_status()
                    status_data = status_resp.json()
                    status = status_data.get("status")
                    logger.info(f"    - Current status: {status}")
                    if status == "done":
                        success_status = True
                        break
                    elif status == "error":
                        logger.error(f"  [x] Run failed with error: {status_data.get('error')}")
                        break
                except Exception as e:
                    logger.error(f"    [!] Error polling status: {e}", exc_info=True)
            
            if not success_status:
                logger.error(f"  [x] Run did not complete successfully (final status: {status}). skipping promotion.")
                failed += 1
                continue

            # Step 3: Fetch Run Details
            logger.info(f" -> Fetching run {run_id}...")
            try:
                run_resp = requests.get(f"{QUANTUM_API_URL}/runs/{run_id}", timeout=30)
                run_resp.raise_for_status()
                run_detail = run_resp.json()
                logger.debug(f"Quantum run details: {json.dumps(run_detail)}")
            except Exception as e:
                logger.error(f"  [!] Error fetching run details: {e}", exc_info=True)
                failed += 1
                continue

            # Step 4: Map & POST to bitzaurus-server
            now = datetime.now(timezone.utc)
            default_close = now + timedelta(days=14)

            # Extract candidates and calculate score-weighted allocations
            raw_outcomes = run_detail.get("outcomes", [])
            outcomes_for_bitzaurus = []
            
            for o in raw_outcomes:
                candidates = o.get("candidates", [])
                if not candidates:
                    continue
                # Build symbol->score map (admin style)
                score_by_sym = {}
                for c in candidates:
                    try:
                        score_by_sym[c["symbol"]] = float(c.get("score", 0.5))
                    except (ValueError, TypeError, KeyError):
                        score_by_sym[c.get("symbol", "")] = 0.5
                
                # Take basket selected symbols or fall back to top 4 by score
                basket = (
                    o.get("quantumBasket", {}).get("selected", []) or
                    o.get("classicalBasket", {}).get("selected", [])
                )
                if basket:
                    symbols = basket
                else:
                    sorted_candidates = sorted(candidates, key=lambda c: float(c.get("score", 0) or 0), reverse=True)
                    symbols = [c["symbol"] for c in sorted_candidates[:4] if "symbol" in c]
                
                # Admin-style allocation: score^1.5, 2-decimal rounding, no min/max bounds
                raw_weights = [max(0.0, score_by_sym.get(s, 0.5)) ** 1.5 for s in symbols]
                total_w = sum(raw_weights) or 1.0
                stocks = []
                for i, symbol in enumerate(symbols):
                    pct = round((raw_weights[i] / total_w) * 10000) / 100
                    stocks.append({"symbol": symbol, "allocationPct": pct})
                
                # Pin residual to top-weighted stock
                drift = round(100.0 - sum(s["allocationPct"] for s in stocks), 2)
                if abs(drift) > 0.01:
                    top_idx = max(range(len(stocks)), key=lambda i: stocks[i]["allocationPct"])
                    stocks[top_idx]["allocationPct"] = round(stocks[top_idx]["allocationPct"] + drift, 2)
                
                outcomes_for_bitzaurus.append({
                    "label": o.get("label", "Trade"),
                    "stocks": stocks
                })

            bitzaurus_payload = {
                "title": run_detail.get("event", {}).get("title") or title,
                "description": run_detail.get("event", {}).get("description") or description,
                "outcomes": outcomes_for_bitzaurus,
                "opensAt": now.isoformat().replace("+00:00", "Z"),
                "closesAt": default_close.isoformat().replace("+00:00", "Z"),
                "brokerMode": "mock",
                "aiRunId": run_id
            }

            try:
                logger.info(f" -> Posting event to bitzaurus-server {BITZAURUS_API_URL}...")
                logger.debug(f"Bitzaurus payload: {json.dumps(bitzaurus_payload)}")
                resp = requests.post(BITZAURUS_API_URL, json=bitzaurus_payload, headers=push_headers, timeout=30)
                if resp.status_code in [200, 201]:
                    logger.info(f"  [+] Pushed and created market: {title[:90]}")
                    pushed += 1
                else:
                    logger.error(f"  [x] Failed posting market ({resp.status_code}): {resp.text[:200]}")
                    failed += 1
            except Exception as e:
                logger.error(f"  [!] Error creating market: {e}", exc_info=True)
                failed += 1

        logger.info(f"Push complete: {pushed} succeeded, {failed} failed.")

    return True

if __name__ == "__main__":
    generate_events()
