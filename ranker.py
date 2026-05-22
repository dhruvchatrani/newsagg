from datetime import datetime, timezone
import json
import re
from typing import List, Dict, Any
from google import genai
from google.genai import types

import config

class PredictionMarketRanker:
    """
    Ranks and categorizes aggregated news articles for a prediction market database.
    """
    def __init__(self, query: str):
        self.query = query.lower()
        self.prediction_keywords = [
            "will", "shall", "predict", "probability", "odds", "forecast", "poll", "verdict",
            "decision", "outcome", "announcement", "winner", "candidate", "legislation", "bill",
            "approval", "rate cut", "rate hike", "acquisition", "merger", "ban", "launch", "cancellation",
            "clinical trial", "fda", "verdict", "guilty", "settlement", "nomination", "resign"
        ]

    def deduplicate(self, articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen_urls = set()
        seen_titles = set()
        unique_articles = []

        for article in articles:
            url = article.get("url", "").strip()
            title = article.get("title", "").strip().lower()

            clean_url = url
            if url:
                clean_url = re.sub(r'^https?://(www\.)?', '', url).split('?')[0].rstrip('/')

            clean_title = re.sub(r'[^a-z0-9]', '', title)

            is_duplicate = False
            if clean_url and clean_url in seen_urls:
                is_duplicate = True
            elif clean_title and clean_title in seen_titles:
                is_duplicate = True

            if not is_duplicate:
                if clean_url:
                    seen_urls.add(clean_url)
                if clean_title:
                    seen_titles.add(clean_title)
                unique_articles.append(article)
            else:
                for existing in unique_articles:
                    existing_url = re.sub(r'^https?://(www\.)?', '', existing.get("url", "")).split('?')[0].rstrip('/')
                    existing_title = re.sub(r'[^a-z0-9]', '', existing.get("title", "").lower())
                    if (clean_url and existing_url == clean_url) or (clean_title and existing_title == clean_title):
                        if len(article.get("snippet", "")) > len(existing.get("snippet", "")):
                            existing["snippet"] = article.get("snippet", "")
                            existing["published_at"] = article.get("published_at", "")
                        break
        return unique_articles

    def calculate_rule_based_scores(self, articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        scored_articles = []
        now = datetime.now(timezone.utc)

        for art in articles:
            hours_old = 24.0
            try:
                pub_date = datetime.fromisoformat(art["published_at"].replace("Z", "+00:00"))
                if pub_date.tzinfo is None:
                    pub_date = pub_date.replace(tzinfo=timezone.utc)
                diff = now - pub_date
                hours_old = max(0.0, diff.total_seconds() / 3600.0)
            except Exception:
                pass
            freshness_score = max(0.1, 1 - hours_old/72.0)

            source_name = art.get("source", "").lower()
            authority_score = 0.5
            for ts in config.TRUSTED_SOURCES:
                if ts in source_name:
                    authority_score = 1.0
                    break

            title_content = art.get("title", "").lower()
            snippet_content = art.get("snippet", "").lower()
            full_text = f"{title_content} {snippet_content}"

            query_match_score = 1.0
            if self.query:
                query_match_score = 1.2 if self.query in title_content else 1.0 if self.query in full_text else 0.5

            pred_match_count = sum(1 for kw in self.prediction_keywords if kw in full_text)
            pred_score = min(1.0, pred_match_count * 0.2)

            relevance_score = max(0.0, min(1.0, (query_match_score * 0.6) + (pred_score * 0.4)))

            w = config.RANKING_WEIGHTS
            final_score = (freshness_score * w["freshness"]) + (authority_score * w["authority"]) + (relevance_score * w["relevance"])

            best_category = "Other"
            max_matches = 0
            for category, keywords in config.PREDICTION_MARKET_INDUSTRIES.items():
                matches = sum(full_text.count(kw) for kw in keywords)
                if matches > max_matches:
                    max_matches = matches
                    best_category = category

            scored_art = art.copy()
            scored_art.update({
                "industry": best_category,
                "score": round(final_score, 3),
                "ranking_criteria_breakdown": {
                    "freshness": round(freshness_score, 2),
                    "authority": round(authority_score, 2),
                    "relevance": round(relevance_score, 2)
                },
                "ranking_method": "rule-based"
            })
            scored_articles.append(scored_art)
        
        scored_articles.sort(key=lambda x: x["score"], reverse=True)
        return scored_articles

    def evaluate_with_gemini(self, articles: List[Dict[str, Any]], api_key: str) -> List[Dict[str, Any]]:
        if not articles:
            return []

        batch_size = 10
        scored_articles = []

        pre_scored_list = []
        for index, art in enumerate(articles):
            pre_scored_list.append({"article": art, "index": index})

        try:
            client = genai.Client(api_key=api_key)
        except Exception as e:
            print(f"Error initializing GenAI Client: {e}. Falling back to rule-based.")
            return self.calculate_rule_based_scores(articles)

        for i in range(0, len(pre_scored_list), batch_size):
            batch = pre_scored_list[i:i+batch_size]

            articles_text = ""
            for item in batch:
                art = item["article"]
                articles_text += (
                    f"Index: {item['index']}\n"
                    f"Title: {art.get('title')}\n"
                    f"Source: {art.get('source')}\n"
                    f"Snippet: {art.get('snippet')}\n"
                    f"-----------------\n"
                )

            prompt = f"""You are a senior prediction-market analyst triaging news for an event-creation pipeline.
Your job: score each article on how useful it is for generating tradable binary prediction events on financial assets.

Context: The target asset universe includes US/UK/EU Equities, Major FX pairs, Crypto, and major Commodities. Do not highly rank stories that cannot map to these.

For each article, output:
1. category — exactly one of: Politics, Economics & Macro, Crypto & Web3, Geopolitics, Tech & AI, Science & Health, Sports, Pop Culture & Entertainment, Other
2. prediction_relevance — float in [0.0, 1.0]. Composite of:
   a) Event-driven: Does it point to a discrete, datable future outcome?
   b) Asset-linkable: Can it be tied to a publicly tradable asset or a clean macro proxy?
   c) Magnitude: Would the outcome move price by more than noise?
   d) Freshness: Is this new information, not stale or fully priced-in?
   Anchors:
   - 0.85–1.00: Hard catalyst with clear asset path (e.g., Fed decision, M&A).
   - 0.60–0.84: Strong macro/geopolitical impact with proxy assets.
   - 0.35–0.59: Soft signal, indirect path.
   - 0.10–0.34: Generic coverage, soft news.
   - 0.00–0.09: Un-tradable lifestyle/entertainment.
3. reason — one sentence stating the catalyst and likely asset path.

OUTPUT RULES:
- Return ONLY a JSON array. No markdown fences.
- One object per input article, in the EXACT same order as input. Count must match input count.

Articles to evaluate:
{articles_text}

Schema:
[
  {{
    "index": <int>,
    "category": "<one of the 9 categories>",
    "prediction_relevance": <float 0.0-1.0>,
    "reason": "<one sentence>"
  }}
]"""

            try:
                response = client.models.generate_content(
                    model="gemini-3.1-flash-lite",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )
                
                text_response = response.text
                gemini_results = json.loads(text_response)
                results_map = {res["index"]: res for res in gemini_results}

                for item in batch:
                    idx = item["index"]
                    res = results_map.get(idx, {})
                    
                    gemini_category = res.get("category", "Other")
                    if gemini_category not in config.PREDICTION_MARKET_INDUSTRIES:
                        gemini_category = "Other"

                    relevance_score = float(res.get("prediction_relevance", 0.5))
                    relevance_score = max(0.0, min(1.0, relevance_score))

                    scored_art = item["article"].copy()
                    scored_art.update({
                        "industry": gemini_category,
                        "score": round(relevance_score, 3), # Trusting LLM's full composite score
                        "reason": res.get("reason", "Scored using Gemini LLM analysis."),
                        "ranking_method": "gemini-llm"
                    })
                    scored_articles.append(scored_art)
            except Exception as e:
                print(f"Gemini API request failed for batch: {e}. Falling back to rule-based ranking.")
                fallback = self.calculate_rule_based_scores([i["article"] for i in batch])
                scored_articles.extend(fallback)

        scored_articles.sort(key=lambda x: x["score"], reverse=True)
        return scored_articles
