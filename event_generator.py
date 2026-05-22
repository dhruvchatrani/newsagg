import json
import re
import os
import requests
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
import config

_env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=_env_path, override=True)

CASCADE_API_URL = os.getenv("CASCADE_API_URL", "http://localhost:8800/api/admin/markets/from-prompt").strip()
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "").strip()

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
TITLE PHILOSOPHY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The title is what a user reads before deciding to Buy or Sell.
It must NOT sound like a financial research headline or an analyst report.
It must sound like a plain-English market intuition a smart person would say.

TITLE PATTERN:
  [Country / Region / Trend / Named Actor] + [market or behavior reaction] + [natural direction phrase]

GOOD EXAMPLES (use this register and style):
  - Middle East Tension Could Push Oil Higher
  - US-China Tariff Escalation Could Weaken Asian Supply Chains
  - Japan Fiscal Concerns Could Pressure JPY Lower
  - Fed Rate Uncertainty Could Keep Gold Elevated
  - Iran Conflict Risk Could Lift Defense Spending
  - Meta Workforce Cuts Could Expand Operating Margins
  - Red Sea Disruption Could Keep Shipping Costs High
  - AI Copyright Pressure Could Raise Content Licensing Costs
  - Trump AI Security Order Could Benefit Cloud Security Leaders

TITLE RULES — all must hold or set worthy=false:
- Use the pattern above: subject + reaction + direction
- Direction phrase MUST use soft modal language: "Could", "May", "Likely to" — NOT "Will"
- BANNED openers: "Market Reaction to", "Impact of", "Effect of", "Outlook for", "Analysis of"
- BANNED: price targets, percentages, "Cross X by Y", ticker symbols in the title
- The subject MUST be a real named entity, country, region, trend, or sector — not a generic placeholder
- A reader should instantly understand: What is happening? Which way is the market likely to move?

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DESCRIPTION RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Minimum 5 dense, information-rich sentences — no filler or padding
- MUST cite at least 2 concrete facts from the snippet: named individuals, dollar amounts, percentages, vote counts, dates, or specific data points
- MUST explain the step-by-step mechanism: how the real-world event flows through to the asset price
- MUST NOT invent numbers or claims not present in the snippet or price context
- Final sentence MUST state precise binary resolution terms: specific price level, direction, and weekly timeframe

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WORTHY GATE & EVALUATION LAYER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Evaluate the event strictly across four pillars:
1. Validation Check: Is the source credible and unique?
2. Tradability Check: Does the trend have clear, actionable market relevance for a specific asset?
3. Virality Check: Is there measurable trend strength, engagement, or social/news momentum?
4. Worthiness Assessment: Is this event important enough for downstream trading?

If ALL four pillars pass, set decision_approved=true. Otherwise, decision_approved=false.
Set worthy=false if decision_approved=false, OR if any title/description rules fail.
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

Apply ALL title, description, and worthy rules from the system instruction before writing your response.
If ANY quality gate fails, still return the full JSON but set worthy=false with your honest tradability_score.

TITLE REMINDER — follow this pattern exactly:
  [Country/Region/Trend/Named Actor] + [market or behavior reaction] + [soft directional phrase: Could/May/Likely to]
  Example: "Meta Workforce Cuts Could Expand Operating Margins"
  NOT: "Market Reaction to Meta's Layoffs" or "Impact of Meta Cost Reduction"

Output strict JSON matching schema:
{{
  "source_story_id": "{url}",
  "validation_check": true,
  "tradability_check": true,
  "virality_check": true,
  "worthiness_assessment": true,
  "decision_approved": true,
  "worthy": true,
  "title": "<[Subject] + [reaction] + [Could/May/Likely to + direction] — plain English, no tickers, no price targets>",
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
    # PUSH: Send each worthy event to the Cascade API
    # ========================================================
    if sliced_events:
        print(f"\nPUSHING {len(sliced_events)} events to Cascade API...")
        push_headers = {"Content-Type": "application/json"}
        if ADMIN_API_TOKEN:
            push_headers["Authorization"] = f"Bearer {ADMIN_API_TOKEN}"

        pushed, failed = 0, 0
        for event in sliced_events:
            title = event.get("title", "")
            description = event.get("description", "")
            if not title:
                continue
            payload = {
                "title": title,
                "description": description,
                "basketSize": 4,
                "source": "quantum",
                "depth": "low",
                "brokerMode": "mock"
            }
            try:
                resp = requests.post(CASCADE_API_URL, json=payload, headers=push_headers, timeout=30)
                if resp.status_code in [200, 201]:
                    print(f"  [+] Pushed: {title[:90]}")
                    pushed += 1
                else:
                    print(f"  [x] Failed ({resp.status_code}): {title[:90]}")
                    failed += 1
            except Exception as e:
                print(f"  [!] Error pushing '{title[:80]}': {e}")
                failed += 1

        print(f"\nPush complete: {pushed} succeeded, {failed} failed.")

    return True

if __name__ == "__main__":
    generate_events()
