import json
import requests
from datetime import datetime, timezone
import config

def generate_events(news_file="news_database.json", assets_file="assets.json", max_events=15):
    try:
        with open(news_file, 'r') as f:
            news_data = json.load(f)
        with open(assets_file, 'r') as f:
            universe = json.load(f)
    except Exception as e:
        print(f"Error loading files: {e}")
        return

    articles = []
    for cat, items in news_data.get("categories", {}).items():
        articles.extend(items)

    articles = sorted(articles, key=lambda x: x.get("score", 0), reverse=True)
    top_articles = articles[:100]
    articles_lite = [{"url": a["url"], "title": a["title"], "snippet": a["snippet"], "source": a["source"]} for a in top_articles]

    universe_str = ", ".join(universe)

    if not config.GEMINI_API_KEY:
        print("Error: GEMINI_API_KEY not found in config.")
        return

    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key={config.GEMINI_API_KEY}"

    # ==========================================
    # PASS 1: TRIAGE & ASSET MAPPING WITH THESIS
    # ==========================================
    prompt1 = f"""You are a quantitative prediction-market analyst.
Task: Triage news and map valid stories to specific financial assets from the universe, explicitly stating the directional thesis.

PIPELINE:
1. TRIAGE — Ask: is there a discrete, datable future outcome that a tradable asset will react to? Skip stories that are already resolved, fully priced-in, or pure commentary.
2. ASSET MAPPING & THESIS — Map each surviving story to the smallest set of assets with a DEFENSIBLE causal chain. 
   - You MUST explicitly state the directional thesis (e.g., "Bullish - supply shock drives prices up", "Bearish - missing earnings hurts stock").
   - ONLY use tickers EXACTLY as written in the Asset Universe.

Asset Universe:
{universe_str}

Articles to process:
{json.dumps(articles_lite)}

OUTPUT — return ONLY a JSON array, no prose:
[
  {{
    "source_story_id": "<exact URL of the source article>",
    "mapped_assets": [
      {{
        "ticker": "<EXACT TICKER from universe>",
        "directional_thesis": "<e.g., Bullish / Bearish / Volatile - 1 sentence explaining why>"
      }}
    ]
  }}
]"""

    print(f"PASS 1: Sending {len(top_articles)} articles to Gemini for Triage and Directional Mapping...")
    try:
        resp1 = requests.post(gemini_url, json={
            "contents": [{"parts": [{"text": prompt1}]}],
            "generationConfig": {"responseMimeType": "application/json"}
        }, timeout=120)
        resp1.raise_for_status()
        pass1_json = json.loads(resp1.json()["candidates"][0]["content"]["parts"][0]["text"])
    except Exception as e:
        print(f"Error during Pass 1 LLM generation: {e}")
        return

    # Extract unique assets based on the new schema
    unique_assets = set()
    for item in pass1_json:
        for asset_obj in item.get("mapped_assets", []):
            ticker = asset_obj.get("ticker")
            if ticker:
                unique_assets.add(ticker)

    if not unique_assets:
        print("No valid asset mappings found in Pass 1. Exiting.")
        return

    # ==========================================
    # PYTHON STEP: TAVILY PRICE DISCOVERY
    # ==========================================
    print(f"PYTHON STEP: Fetching current prices for {len(unique_assets)} mapped assets via Tavily...")
    price_context = {}
    if not config.TAVILY_API_KEY:
        print("Warning: TAVILY_API_KEY not found. Proceeding without price context.")
    else:
        for asset in unique_assets:
            payload = {
                "api_key": config.TAVILY_API_KEY,
                "query": f"current live market trading price of {asset} stock crypto commodity forex today",
                "search_depth": "basic",
                "max_results": 2
            }
            try:
                tav_resp = requests.post("https://api.tavily.com/search", json=payload, timeout=15)
                if tav_resp.status_code == 200:
                    results = tav_resp.json().get("results", [])
                    price_context[asset] = " | ".join([r.get("content", "") for r in results])
            except Exception as e:
                print(f"Failed to fetch price for {asset}: {e}")

    # ==========================================
    # PASS 2: NARRATIVE EVENT GENERATION
    # ==========================================
    prompt2 = f"""You are a quantitative prediction-market event generator. Quality over quantity.
We have triaged the news, mapped the assets with a directional thesis, and fetched live prices to understand the current market state.

Current Price Context:
{json.dumps(price_context, indent=2)}

Triaged Stories, Mapped Assets, and Directional Thesis:
{json.dumps(pass1_json, indent=2)}

Original Articles (For deep context extraction):
{json.dumps(articles_lite)}

    TITLE RULES (STRICT):
    - BANNED: Do NOT formulate titles as questions (do NOT start with "Will", "Whether", "How", or similar, and do NOT use a question mark "?").
    - BANNED: Do NOT include raw ticker symbols or asset codes (e.g., "US500", "AAPL.NAS", "WTI.NYSE", "BP.LSE", "NVDA.NAS") in the title.
    - BANNED: Do NOT use price targets, dollar amounts, or percentages in the title.
    - REQUIRED: Write the title as a confident, declarative statement or proposition of the expected outcome.
    - REQUIRED: Use clean, natural, human-friendly terms for the asset in the title (e.g., "the US stock market", "Apple's stock price", "crude oil prices", "BP's share price").
    - REQUIRED: The title MUST explicitly describe the news linkage and the directional reaction.
    - Format Example: "Crude oil prices to trade higher this week following reported US strikes in Iran"
    - Format Example: "Apple's stock price to drop on the news of the DOJ expanding its antitrust lawsuit"
    - Format Example: "US stock market to decline following Federal Reserve officials' remarks on inflation"

    CAUSAL CHAIN RULE:
    - You must output a "causal_chain" field mapping the exact mechanism: "News Event -> Market Mechanism -> Asset Direction". Use human-friendly terms for the asset.
    - Example: "USA hits Iran -> Strait of Hormuz supply risk -> Global oil supply shock -> Crude oil prices go up."

DESCRIPTION RULES:
- Minimum 5 sentences. Pull specific names, stats, geopolitical details, or quotes directly from the provided article snippets. Do not write fluffy, generic summaries. Provide real context.
- The final sentence must state the resolution criteria relative to current price (e.g., "Resolves to Yes if WTI.NYSE closes the week higher than its current trading price.")

TRADABILITY SCORE — float in [0.0, 1.0]:
- 0.85–1.00: Clean catalyst, unambiguous resolution, explicit linkage.
- 0.60–0.84: Clear resolution, moderate ambiguity.
- Below 0.60: DO NOT EMIT. Skip the event.

OUTPUT — return ONLY a JSON array, no prose:
[
  {{
    "source_story_id": "<exact URL of the source article>",
    "title": "<catalyst-driven directional question>",
    "causal_chain": "<A -> B -> C mapping>",
    "description": "<5-6 sentences: Deep news context extracted from article + exact resolution criteria>",
    "linked_assets": ["<EXACT TICKER>"],
    "directional_impact": "<Bullish|Bearish|Volatile>",
    "category": "<industry category>",
    "horizon": "<daily|weekly|monthly>",
    "tradability_score": <float 0.60-1.0>
  }}
]"""

    print("PASS 2: Generating narrative, directional prediction events...")
    try:
        resp2 = requests.post(gemini_url, json={
            "contents": [{"parts": [{"text": prompt2}]}],
            "generationConfig": {"responseMimeType": "application/json"}
        }, timeout=120)
        resp2.raise_for_status()
        events = json.loads(resp2.json()["candidates"][0]["content"]["parts"][0]["text"])

        # Post-LLM Deduplication: group by (Asset, Horizon) and keep the highest score
        deduped_map = {}
        for event in events:
            assets = event.get("linked_assets", [])
            if not assets or event.get("tradability_score", 0) < 0.60:
                continue
            
            key = f"{assets[0]}_{event.get('horizon', 'daily')}"
            
            if key not in deduped_map:
                deduped_map[key] = event
            else:
                if event.get("tradability_score", 0) > deduped_map[key].get("tradability_score", 0):
                    deduped_map[key] = event

        unique_events = sorted(list(deduped_map.values()), key=lambda x: x.get("tradability_score", 0), reverse=True)
        final_events = unique_events[:max_events]

        output_data = {
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
                "events_generated": len(final_events)
            },
            "events": final_events
        }

        with open("prediction_events.json", "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)

        print(f"Pipeline complete: Saved {len(final_events)} linkage-driven events to prediction_events.json")

    except Exception as e:
        print(f"Error during Pass 2 LLM generation: {e}")

if __name__ == "__main__":
    generate_events()
