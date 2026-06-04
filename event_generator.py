import json
import re
import os
import time
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import config

_env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=_env_path, override=True)

QUANTUM_BASE_URL = os.getenv("QUANTUM_API_URL", "http://localhost:3002").rstrip("/").removesuffix("/runs")
MARKETS_API_URL = os.getenv("MARKETS_API_URL", "http://localhost:8800/api/admin/markets").rstrip("/")
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "").strip()

def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if ADMIN_API_TOKEN:
        h["Authorization"] = f"Bearer {ADMIN_API_TOKEN}"
    return h

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


def _market_payload_from_outcomes(run_id: str, outcomes: list, title: str, description: str, source_link: str = "", headline: str = "") -> dict:
    mapped_outcomes = []
    for outcome in outcomes:
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

    return {
        "eventId": run_id,
        "headline": headline or title,
        "title": title,
        "description": description,
        "sourceLink": source_link,
        "outcomes": mapped_outcomes,
        "opensAt": now_iso,
        "closesAt": closes_at,
        "brokerMode": "mock"
    }

def run_quantum_streaming(title, description, basket_size=4, depth="tree", poll_interval=0.4):
    """Non-blocking start + poll + fetch pattern matching admin-live flow.
    
    POST /runs/start → poll GET /runs/{id}/events?since=N → GET /runs/{id}.
    Returns the same shape as the old blocking POST /runs call.
    """
    url_base = f"{QUANTUM_BASE_URL}/runs"
    payload = {"basketSize": basket_size, "depth": depth, "description": description, "title": title}

    # 1. Kick off the run (non-blocking)
    start_resp = requests.post(f"{url_base}/start", json=payload, headers=_headers(), timeout=30)
    if start_resp.status_code not in (200, 201):
        raise RuntimeError(f"Quantum run start failed ({start_resp.status_code}): {start_resp.text}")
    run_id = start_resp.json()["runId"]

    # 2. Poll for progress events
    cursor = 0
    while True:
        ev_resp = requests.get(f"{url_base}/{run_id}/events?since={cursor}", headers=_headers(), timeout=15)
        if ev_resp.status_code != 200:
            time.sleep(poll_interval)
            continue
        body = ev_resp.json()
        for ev in body.get("events", []):
            ev_type = ev["type"]
            pl = ev.get("payload", {})
            if ev_type == "analysis.done":
                themes = ", ".join(pl.get("themes", []))
                print(f"  → Themes: {themes} · Horizon: {pl.get('horizon', '')} · Sentiment: {pl.get('sentiment', '')}")
            elif ev_type == "scenario.discovered":
                print(f"  → Scenario: {pl.get('name', '')} ({pl.get('probability', 0) * 100:.0f}%)")
            elif ev_type == "mapping.done":
                cand = pl.get("candidates", [])
                print(f"  → {len(cand)} candidates scored")
            elif ev_type == "solver.classical.done":
                basket = pl.get("basket", [])
                print(f"  → Classical basket: {basket}")
            elif ev_type == "solver.quantum.done":
                basket = pl.get("basket", [])
                print(f"  → Quantum basket:  {basket}")
            elif ev_type == "run.error":
                err = pl.get("error", "unknown error")
                raise RuntimeError(f"Quantum run failed: {err}")

        cursor = body.get("nextCursor", cursor)
        if body.get("done"):
            break
        time.sleep(poll_interval)

    # 3. Fetch the final cached result
    run_resp = requests.get(f"{url_base}/{run_id}", headers=_headers(), timeout=15)
    if run_resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch Quantum run result ({run_resp.status_code}): {run_resp.text}")
    run_data = run_resp.json()
    total_s = (run_data.get("outcomes", [None]) or [{}])[0].get("quantumBasket", {}).get("durationMs", 0) / 1000
    print(f"  → Pipeline complete ({total_s:.1f}s)")
    return run_data


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
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key={config.GEMINI_API_KEY}"
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
    response = requests.post(url, json=payload, headers=headers, timeout=60)
    if response.status_code == 200:
        resp_json = response.json()
        text = resp_json["candidates"][0]["content"]["parts"][0]["text"].strip()
        return clean_and_parse_json(text)
    else:
        raise Exception(f"Gemini API error {response.status_code}: {response.text}")

def generate_events(news_file="news_database.json", assets_file="assets.json", max_events=15, articles=None):
    try:
        with open(assets_file, 'r') as f:
            universe = json.load(f)
    except Exception as e:
        print(f"Error loading files: {e}")
        return False

    if articles is None:
        try:
            with open(news_file, 'r') as f:
                news_data = json.load(f)
            articles = []
            for cat, items in news_data.get("categories", {}).items():
                articles.extend(items)
        except Exception as e:
            print(f"Error loading news file: {e}")
            return False

    # ---------------------------------------------------------
    # OPTIMIZATION: Only process articles with a score >= 0.8
    # ---------------------------------------------------------
    high_value_articles = [a for a in articles if a.get("score", 0) >= 0.8]
    high_value_articles = sorted(high_value_articles, key=lambda x: x.get("score", 0), reverse=True)
    top_articles = high_value_articles[:50] # Hard cap just in case

    if not top_articles:
        print("No articles with score >= 0.8 found. Exiting pipeline.")
        return False

    articles_lite = [{"url": a["url"], "title": a["title"], "snippet": a["snippet"], "source": a["source"]} for a in top_articles]

    universe_str = ", ".join(universe)

    system_prompt_base = f"""You are a prediction-market event builder for Cascade, a platform where users take Buy/Sell positions on real-world trends.

You strictly communicate by returning raw JSON matching the requested schemas. No prose, no markdown.

CORE RULE — TRADABILITY GATE:
An event may ONLY be created if the underlying trend can be mapped to at least one instrument that can form a tradable Basket:
- FX pairs (e.g. USDJPY, EURUSD)
- Commodities (e.g. Oil, Gold, Natural Gas, Wheat, Copper)
- Crypto assets (e.g. BTC, ETH, SOL)
- Indices (e.g. S&P 500, NASDAQ, Nikkei)
- ETFs or Sector baskets (e.g. Defense ETF, Energy, Semiconductors, Cybersecurity)
- Country or region baskets
- Publicly traded companies with direct exposure to the trend
If no tradable Basket can be constructed from the news, the event must be REJECTED.

Valid Asset Universe:
{universe_str}"""

    # ========================================================
    # PASS 1: BATCHED TRIAGE & ASSET MAPPING (5 per batch)
    # ========================================================
    print(f"PASS 1: Triaging {len(articles_lite)} highly-rated articles in batches of 5 to optimize KV caching...")
    batched_articles = list(chunk_list(articles_lite, 5))
    all_triaged_mappings = []

    pass1_system_instruction = system_prompt_base + """

Task: Filter incoming news batches for real-world trends that meet the Tradability Gate AND have a clear directional impact on a Basket of instruments from the universe.

HARD REJECT — return NO mapping for any article that:
- Cannot be mapped to a tradable Basket (FX, Commodity, Crypto, Index, ETF, Sector, Company)
- Is vague macro commentary with no specific named event, actor, or decision
- Describes a trend already fully priced in with no new catalyst
- Is speculative opinion or analyst note without a firm near-term trigger
- Duplicates a story already mapped in this batch — keep only the strongest version

ACCEPT only stories where:
- A clear real-world trend or named event is unfolding (geopolitical, economic, corporate, policy)
- At least one instrument in the universe has direct mechanistic exposure to that trend
- The market direction (up or down pressure) is reasonably clear from the story

For each surviving story, return its exact URL and the mapped assets with a 1-sentence directional thesis."""

    for index, batch in enumerate(batched_articles):
        print(f" -> Processing Batch {index + 1}/{len(batched_articles)}...")
        
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
            print(f"     [!] Failed processing batch {index + 1}: {e}")

    if not all_triaged_mappings:
        print("No valid asset mappings survived triage across all batches. Exiting.")
        return

    valid_mappings = [m for m in all_triaged_mappings if m.get("mapped_assets")]
    print(f"Triage complete. {len(valid_mappings)} stories successfully mapped to assets.")

    unique_assets = set()
    for item in valid_mappings:
        for asset_obj in item["mapped_assets"]:
            if asset_obj.get("ticker"):
                unique_assets.add(asset_obj["ticker"])

    # ========================================================
    # PYTHON INTERMEDIARY: TOKEN-RESTRICTED PRICE DISCOVERY
    # ========================================================
    print(f"PYTHON STEP: Executing price discovery for {len(unique_assets)} unique tickers via Tavily...")
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
            except Exception as e:
                print(f"     [!] Price look-up skipped for {asset}: {e}")

    article_lookup = {a["url"]: a for a in articles_lite}

    # ========================================================
    # PASS 2: CONTEXT-ISOLATED NARRATIVE EVENT GENERATION
    # ========================================================
    print("PASS 2: Generating catalyst-driven narrative events via isolated single-story chat targets...")
    final_events = []

    pass2_system_instruction = system_prompt_base + """

Task: Build a single Cascade prediction-market event for an isolated target story.
It is better to reject with worthy=false than to generate a mediocre event.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEWS HEADLINE RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The news headline is a short summary of the original news. It must reflect the source article, not the market theme.

RULES:
- Extract a concise news headline from the target article (45–70 characters recommended, max 86)
- Must fit within 2–3 lines on mobile
- Must be factual and grounded in the article, not speculative
- Do NOT include market predictions or trading signals in the headline

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SUMMARY RULES (maps to "description" field)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The summary explains why the news may affect the market.

RULES:
- 1–2 sentences only (80–120 characters recommended, max 150)
- MUST cite at least 1 concrete fact from the snippet
- MUST explain the mechanism: how the real-world event flows through to the asset price
- MUST NOT invent numbers or claims not present in the snippet
- Keep it concise — this is not a detailed analysis, just why the market should care
- Final sentence should connect to directional price movement

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
QUESTION RULES (maps to "title" field)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The question is what a user reads before deciding to Buy or Sell.
It must NOT sound like a financial research headline or repeat the news text.
It must convert the news into a market-prediction question users can answer intuitively.

QUESTION PATTERN:
  "Will [subject] [continue to / keep / move] [direction] [context]?"

GOOD EXAMPLES (use this register and style):
  - Will demand for nuclear energy continue to rise as AI increases electricity usage?
  - Will investor attention toward Middle East energy stocks increase?
  - Will Fed rate uncertainty keep gold elevated?
  - Will Iran conflict risk lift defense spending?
  - Will Meta workforce cuts expand operating margins?
  - Will AI copyright pressure raise content licensing costs?

QUESTION RULES — all must hold or set worthy=false:
- Must be a question (starts with "Will" or "Is")
- Must be 65–100 characters recommended, max 120
- Do NOT copy the news text directly into the question — convert it into a market prediction
- Do NOT use direct Yes/No phrasing like "Do you think X happened?"
- Direction phrase MUST use soft modal language: "continue to", "keep", "move" — NOT "Will" as a statement
- BANNED openers: "Market Reaction to", "Impact of", "Effect of", "Outlook for", "Analysis of"
- BANNED: price targets, percentages, ticker symbols in the question
- The subject MUST be a real named entity, country, region, trend, or sector — not a generic placeholder
- Connect the question to concepts: price rising/falling, demand increasing/decreasing, attention strengthening/weakening

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WORTHY GATE & EVALUATION LAYER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Evaluate the event strictly across four pillars:
1. Validation Check: Is the source credible and unique?
2. Tradability Check: Does the trend have clear, actionable market relevance for a specific asset?
3. Virality Check: Is there measurable trend strength, engagement, or social/news momentum?
4. Worthiness Assessment: Is this event important enough for downstream trading?

If ALL four pillars pass, set decision_approved=true. Otherwise, decision_approved=false.
Set worthy=false if decision_approved=false, OR if any headline/summary/question rules fail.
Honest tradability_score MUST be above 0.80 to be worthy.

CAUSAL CHAIN: Provide a compact "Real-World Trigger -> Market Mechanism -> Asset Direction" string."""

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

Apply ALL headline, summary, question, and worthy rules from the system instruction before writing your response.
If ANY quality gate fails, still return the full JSON but set worthy=false with your honest tradability_score.

QUESTION REMINDER:
- Must be a question starting with "Will" or "Is" (65–100 chars recommended, max 120)
- Example: "Will demand for nuclear energy continue to rise as AI increases electricity usage?"
- NOT: "Meta Workforce Cuts Could Expand Operating Margins" (this is a statement, not a question)

SUMMARY REMINDER:
- 1–2 sentences (80–120 chars recommended, max 150)
- NOT a multi-paragraph analysis

Output strict JSON matching schema:
{{
  "source_story_id": "{url}",
  "validation_check": true,
  "tradability_check": true,
  "virality_check": true,
  "worthiness_assessment": true,
  "decision_approved": true,
  "worthy": true,
  "headline": "<45-70 character news headline from the article — factual, not speculative, max 86>",
  "title": "<Market question: Will [subject] [direction] [context]? — 65-100 chars, max 120>",
  "causal_chain": "<Real-World Trigger -> Market Mechanism -> Asset Direction>",
  "description": "<1-2 sentence market impact summary — 80-120 chars, max 150>",
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
                    if worthy and decision and score >= 0.80:
                        final_events.append(event_obj)
                    else:
                        print(f"     [~] Dropped low-quality event for {ticker} (worthy={worthy}, decision={decision}, score={score:.2f}): {event_obj.get('title', '')[:80]}")
            except Exception as e:
                print(f"     [!] Failed to generate isolated event for {ticker}: {e}")

    # ========================================================
    # POST-LLM DEDUPLICATION & METADATA SAVE
    # ========================================================
    deduped_map = {}
    for event in final_events:
        assets = event.get("linked_assets", [])
        if not assets:
            continue
        key = f"{assets[0]}_{event.get('horizon', 'weekly')}"
        event_score = parse_tradability_score(event.get("tradability_score"))
        event["tradability_score"] = event_score
        existing_score = parse_tradability_score(deduped_map[key].get("tradability_score")) if key in deduped_map else 0.0
        if key not in deduped_map or event_score > existing_score:
            deduped_map[key] = event

    unique_events = sorted(
        list(deduped_map.values()),
        key=lambda x: parse_tradability_score(x.get("tradability_score")),
        reverse=True,
    )
    sliced_events = unique_events[:max_events]

    output_data = {
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "events_generated": len(sliced_events),
            "engine": "Gemini API (gemini-3.1-flash-lite)"
        },
        "events": sliced_events
    }

    with open("prediction_events.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)

    print(f"Pipeline complete. {len(sliced_events)} grounded events saved.")

    # ========================================================
    # PUSH: Send each worthy event to the Cascade API via allocations pipeline
    # ========================================================
    if sliced_events:
        print(f"\nPUSHING {len(sliced_events)} events to Cascade API...")
        pushed, failed = 0, 0
        for event in sliced_events:
            title = event.get("title", "")
            description = event.get("description", "")
            if not title:
                continue
            try:
                print(f" -> Running pipeline for event: {title[:90]}")
                run_data = run_quantum_streaming(title, description, basket_size=4, depth="tree")
                market_payload = _market_payload_from_outcomes(
                    run_data.get("runId", ""),
                    run_data.get("outcomes", []),
                    title, description,
                    source_link=event.get("source_story_id", ""),
                    headline=event.get("headline", "")
                )
                resp = requests.post(MARKETS_API_URL, json=market_payload, headers=_headers(), timeout=30)
                if resp.status_code in (200, 201):
                    print(f"  [+] Pushed successfully: {title[:90]}")
                    pushed += 1
                else:
                    print(f"  [x] Failed to create market event ({resp.status_code}): {title[:90]}")
                    failed += 1
            except Exception as e:
                print(f"  [!] Error pushing '{title[:80]}': {e}")
                failed += 1

        print(f"\nPush complete: {pushed} succeeded, {failed} failed.")

    return True

if __name__ == "__main__":
    generate_events()
