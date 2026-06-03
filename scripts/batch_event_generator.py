import json
import os
import sys
import requests
import time
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from logger_setup import get_logger

logger = get_logger("newsagg.batch_event_generator")

_env_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(dotenv_path=_env_path, override=True)


def chunk_list(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


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


def generate_batch(news_file="news_database.json", assets_file="assets.json", output_file="batch_payloads.json", articles=None):
    proj_root = Path(__file__).resolve().parent.parent
    if not os.path.isabs(news_file):
        news_file = str(proj_root / news_file)
    if not os.path.isabs(assets_file):
        assets_file = str(proj_root / assets_file)
    if not os.path.isabs(output_file):
        output_file = str(proj_root / output_file)

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
                for article in items:
                    article["_category"] = cat
                    articles.append(article)
        except Exception as e:
            logger.error(f"Error loading news file {news_file}: {e}", exc_info=True)
            return False

    high_value_articles = [a for a in articles if a.get("score", 0) >= 0.9]
    high_value_articles = sorted(high_value_articles, key=lambda x: x.get("score", 0), reverse=True)
    top_articles = high_value_articles[:50]

    if not top_articles:
        logger.info("No articles with score >= 0.9 found. Exiting pipeline.")
        return False

    articles_lite = [
        {"url": a["url"], "title": a["title"], "snippet": a["snippet"], "source": a["source"], "_category": a.get("_category", "Other")}
        for a in top_articles
    ]
    universe_str = ", ".join(universe)

    from prompts.event_generator_base import BASE_PROMPT
    from prompts.event_generator_pass1 import PASS1_INSTRUCTION

    system_prompt_base = BASE_PROMPT.format(universe_str=universe_str)

    # PASS 1: TRIAGE & ASSET MAPPING
    logger.info(f"PASS 1: Triaging {len(articles_lite)} highly-rated articles in batches of 5...")
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
        return False

    valid_mappings = [m for m in all_triaged_mappings if m.get("mapped_assets")]
    logger.info(f"Triage complete. {len(valid_mappings)} stories successfully mapped to assets.")

    unique_assets = set()
    for item in valid_mappings:
        for asset_obj in item["mapped_assets"]:
            if asset_obj.get("ticker"):
                unique_assets.add(asset_obj["ticker"])

    # PRICE DISCOVERY
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

    # PASS 2: EVENT GENERATION WITH HEADLINE
    logger.info("PASS 2: Generating events with headline, summary, and market question...")
    final_payloads = []

    from prompts.event_generator_pass2 import PASS2_INSTRUCTION
    pass2_system_instruction = system_prompt_base + PASS2_INSTRUCTION

    for item in valid_mappings:
        url = item.get("source_story_id")
        orig_article = article_lookup.get(url)
        if not orig_article:
            continue

        category = orig_article.get("_category", "Other")

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

In addition to the standard fields, generate a "headline" — a short, punchy news-style headline (max 80 chars) that summarizes the catalyst, e.g. "Nvidia Earnings Miss Shakes AI Sector" or "Fed Signals Pivot, Dollar Slides".

Output strict JSON matching schema:
{{
  "source_story_id": "{url}",
  "headline": "<short news-style headline summarizing the catalyst — max 80 chars>",
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
                    worthy = event_obj.get("worthy", True)
                    decision = event_obj.get("decision_approved", True)
                    if worthy and decision and score >= 0.90:
                        payload = {
                            "category": category,
                            "source_link": url,
                            "headline": event_obj.get("headline", event_obj.get("title", "")),
                            "description": event_obj.get("description", ""),
                            "title": event_obj.get("title", "")
                        }
                        final_payloads.append(payload)
                        logger.info(f"     [+] Accepted event for {ticker}: {payload['headline'][:60]}")
                    else:
                        logger.warning(f"     [~] Dropped low-quality event for {ticker} (worthy={worthy}, decision={decision}, score={score:.2f})")
            except Exception as e:
                logger.error(f"     [!] Failed to generate isolated event for {ticker}: {e}", exc_info=True)

    if not final_payloads:
        logger.info("No worthy events generated. Nothing to append.")
        return True

    # APPEND TO OUTPUT FILE
    existing = []
    if os.path.exists(output_file):
        try:
            with open(output_file, 'r') as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = []
        except (json.JSONDecodeError, Exception):
            existing = []

    existing.extend(final_payloads)

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(existing, f, indent=2)

    logger.info(f"Batch complete. Appended {len(final_payloads)} payloads to {output_file} (total: {len(existing)}).")
    return True


if __name__ == "__main__":
    generate_batch()
