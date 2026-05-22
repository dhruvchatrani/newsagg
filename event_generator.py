import json
import re
import requests
from datetime import datetime, timezone
import config

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

    # ---------------------------------------------------------
    # OPTIMIZATION: Only process articles with a score >= 0.8
    # ---------------------------------------------------------
    high_value_articles = [a for a in articles if a.get("score", 0) >= 0.8]
    high_value_articles = sorted(high_value_articles, key=lambda x: x.get("score", 0), reverse=True)
    top_articles = high_value_articles[:50] # Hard cap just in case

    if not top_articles:
        print("No articles with score >= 0.8 found. Exiting pipeline.")
        return

    articles_lite = [{"url": a["url"], "title": a["title"], "snippet": a["snippet"], "source": a["source"]} for a in top_articles]

    universe_str = ", ".join(universe)

    system_prompt_base = f"""You are an expert quantitative prediction-market AI system. 
You strictly communicate by returning raw JSON arrays matching the requested schemas. No prose, no markdown formatting blocks.

Valid Asset Universe:
{universe_str}"""

    # ========================================================
    # PASS 1: BATCHED TRIAGE & ASSET MAPPING (5 per batch)
    # ========================================================
    print(f"PASS 1: Triaging {len(articles_lite)} highly-rated articles in batches of 5 to optimize KV caching...")
    batched_articles = list(chunk_list(articles_lite, 5))
    all_triaged_mappings = []

    pass1_system_instruction = system_prompt_base + "\n\nTask: Filter incoming news batches for discrete, datable future outcomes that drive clear directional price movements for assets in the universe. Reject commentary or fully priced-in stories. For surviving stories, return their exact URL and an array of mapped assets from the universe with a 1-sentence directional thesis."

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

    pass2_system_instruction = system_prompt_base + """\n\nTask: Build a catalyst-driven prediction market event for an isolated target story.
- TITLE RULES: BANNED from using absolute price targets or percentages. BANNED from using "Will X cross Y by Z". MUST use natural, organic phrasing naming the explicit news trigger and directional trend.
- DESCRIPTION RULES: Minimum 5 sentences. Extract deep facts, specific metrics, or names from the snippet. Explain the complete operational linkage mechanism. Final sentence must define clean resolution terms relative to today's price context.
- CAUSAL CHAIN RULE: Provide a compact "News Event -> Market Mechanism -> Asset Direction" flow string."""

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

Output strict JSON matching schema:
{{
  "source_story_id": "{url}",
  "title": "<organic catalyst directional question>",
  "causal_chain": "<A -> B -> C mechanism flow string>",
  "description": "<5+ sentences rich context, clear linkage, and concrete resolution terms>",
  "linked_assets": ["{ticker}"],
  "directional_impact": "{asset_obj.get('directional_impact', 'Bullish')}",
  "category": "Macroeconomics",
  "horizon": "weekly",
  "tradability_score": 0.90
}}"""

            try:
                event_obj = call_gemini_api(pass2_system_instruction, isolated_user_prompt)
                if isinstance(event_obj, dict):
                    if event_obj.get("tradability_score", 0) >= 0.60:
                        final_events.append(event_obj)
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
        if key not in deduped_map or event.get("tradability_score", 0) > deduped_map[key].get("tradability_score", 0):
            deduped_map[key] = event

    unique_events = sorted(list(deduped_map.values()), key=lambda x: x.get("tradability_score", 0), reverse=True)
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

    print(f"Pipeline completely successful! Caching optimizations saved {len(sliced_events)} grounded events.")

if __name__ == "__main__":
    generate_events()
