from datetime import datetime, timezone
import json
import re
from typing import List, Dict, Any
import requests

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

    # def evaluate_with_ollama(self, articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    #     if not articles:
    #         return []
    # 
    #     batch_size = 10
    #     scored_articles = []
    # 
    #     pre_scored_list = []
    #     for index, art in enumerate(articles):
    #         pre_scored_list.append({"article": art, "index": index})
    # 
    #     for i in range(0, len(pre_scored_list), batch_size):
    #         batch = pre_scored_list[i:i+batch_size]
    # 
    #         articles_text = ""
    #         for item in batch:
    #             art = item["article"]
    #             articles_text += (
    #                 f"Index: {item['index']}\n"
    #                 f"Title: {art.get('title')}\n"
    #                 f"Source: {art.get('source')}\n"
    #                 f"Snippet: {art.get('snippet')}\n"
    #                 f"-----------------\n"
    #             )
    # 
    #         prompt = f"""You are a senior prediction-market analyst triaging news for an event-creation pipeline.
    # Your job: score each article on how useful it is for generating tradable binary prediction events on financial assets.
    # 
    # Context: The target asset universe includes US/UK/EU Equities, Major FX pairs, Crypto, and major Commodities. Do not highly rank stories that cannot map to these.
    # 
    # For each article, output:
    # 1. category — exactly one of: Politics, Economics & Macro, Crypto & Web3, Geopolitics, Tech & AI, Science & Health, Sports, Pop Culture & Entertainment, Other
    # 2. prediction_relevance — float in [0.0, 1.0].
    #    - 0.85–1.00: Hard catalyst with clear asset path (e.g., Fed decision, M&A).
    #    - 0.60–0.84: Strong macro/geopolitical impact with proxy assets.
    #    - 0.35–0.59: Soft signal, indirect path.
    #    - 0.10–0.34: Generic coverage, soft news.
    #    - 0.00–0.09: Un-tradable lifestyle/entertainment.
    # 3. reason — one sentence stating the catalyst and likely asset path.
    # 
    # OUTPUT RULES:
    # - Return ONLY a valid JSON array. No markdown fences.
    # - One object per input article.
    # 
    # Articles to evaluate:
    # {articles_text}
    # 
    # Schema:
    # [
    #   {{
    #     "index": <int>,
    #     "category": "<one of the 9 categories>",
    #     "prediction_relevance": <float 0.0-1.0>,
    #     "reason": "<one sentence>"
    #   }}
    # ]"""
    # 
    #         url = f"{config.OLLAMA_BASE_URL}/api/generate"
    #         payload = {
    #             "model": config.OLLAMA_MODEL,
    #             "prompt": prompt,
    #             "stream": False,
    #             "format": "json",
    #             "options": {
    #                 "num_ctx": 4096
    #             }
    #         }
    # 
    #         try:
    #             response = requests.post(url, json=payload, timeout=300)
    #             if response.status_code == 200:
    #                 resp_json = response.json()
    #                 
    #                 # Robust parsing helper
    #                 def parse_ollama_json(resp):
    #                     text = resp.get("response", "").strip()
    #                     if not text:
    #                         text = resp.get("thinking", "").strip()
    #                     if not text and "message" in resp:
    #                         msg = resp["message"]
    #                         text = msg.get("content", "").strip()
    #                         if not text:
    #                             text = msg.get("thinking", "").strip()
    #                     if not text:
    #                         raise ValueError("Empty response/thinking from Ollama.")
    #                     
    #                     text = text.strip()
    #                     array_match = re.search(r'\[.*\]', text, re.DOTALL)
    #                     if array_match:
    #                         text = array_match.group(0)
    #                     else:
    #                         object_match = re.search(r'\{.*\}', text, re.DOTALL)
    #                         if object_match:
    #                             text = object_match.group(0)
    #                     return json.loads(text)
    # 
    #                 ollama_results = parse_ollama_json(resp_json)
    #                 results_map = {}
    #                 if isinstance(ollama_results, list):
    #                     for res in ollama_results:
    #                         if isinstance(res, dict) and "index" in res:
    #                             results_map[res["index"]] = res
    #                 elif isinstance(ollama_results, dict):
    #                     for k, res in ollama_results.items():
    #                         if isinstance(res, dict):
    #                             match = re.search(r'\d+', k)
    #                             if match:
    #                                 idx = int(match.group())
    #                                 results_map[idx] = res
    # 
    #                 for item in batch:
    #                     idx = item["index"]
    #                     res = results_map.get(idx, {})
    #                     
    #                     category = res.get("category", "Other")
    #                     if category not in config.PREDICTION_MARKET_INDUSTRIES:
    #                         category = "Other"
    # 
    #                     relevance_score = float(res.get("prediction_relevance", 0.5))
    #                     relevance_score = max(0.0, min(1.0, relevance_score))
    # 
    #                     scored_art = item["article"].copy()
    #                     scored_art.update({
    #                         "industry": category,
    #                         "score": round(relevance_score, 3), 
    #                         "reason": res.get("reason", "Scored using Ollama analysis."),
    #                         "ranking_method": "ollama-llm"
    #                     })
    #                     scored_articles.append(scored_art)
    #             else:
    #                 fallback = self.calculate_rule_based_scores([i["article"] for i in batch])
    #                 scored_articles.extend(fallback)
    #         except Exception as e:
    #             print(f"Ollama request failed: {e}")
    #             fallback = self.calculate_rule_based_scores([i["article"] for i in batch])
    #             scored_articles.extend(fallback)
    # 
    #     scored_articles.sort(key=lambda x: x["score"], reverse=True)
    #     return scored_articles

    def evaluate_with_gemini(self, articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not articles:
            return []

        batch_size = 10
        scored_articles = []

        pre_scored_list = []
        for index, art in enumerate(articles):
            pre_scored_list.append({"article": art, "index": index})

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
2. prediction_relevance — float in [0.0, 1.0].
   - 0.85–1.00: Hard catalyst with clear asset path (e.g., Fed decision, M&A).
   - 0.60–0.84: Strong macro/geopolitical impact with proxy assets.
   - 0.35–0.59: Soft signal, indirect path.
   - 0.10–0.34: Generic coverage, soft news.
   - 0.00–0.09: Un-tradable lifestyle/entertainment.
3. reason — one sentence stating the catalyst and likely asset path.

OUTPUT RULES:
- Return ONLY a valid JSON array. No markdown fences.
- One object per input article.

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

            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key={config.GEMINI_API_KEY}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt}
                        ]
                    }
                ],
                "generationConfig": {
                    "responseMimeType": "application/json"
                }
            }

            try:
                response = requests.post(url, json=payload, headers=headers, timeout=60)
                if response.status_code == 200:
                    resp_json = response.json()
                    text = resp_json["candidates"][0]["content"]["parts"][0]["text"].strip()
                    
                    # Robust cleaning and parsing
                    if text.startswith("```"):
                        newline_idx = text.find("\n")
                        if newline_idx != -1:
                            text = text[newline_idx:].strip()
                        if text.endswith("```"):
                            text = text[:-3].strip()

                    gemini_results = json.loads(text)
                    results_map = {}
                    if isinstance(gemini_results, list):
                        for res in gemini_results:
                            if isinstance(res, dict) and "index" in res:
                                results_map[res["index"]] = res
                    elif isinstance(gemini_results, dict):
                        for k, res in gemini_results.items():
                            if isinstance(res, dict):
                                match = re.search(r'\d+', k)
                                if match:
                                    idx = int(match.group())
                                    results_map[idx] = res

                    for item in batch:
                        idx = item["index"]
                        res = results_map.get(idx, {})
                        
                        category = res.get("category", "Other")
                        if category not in config.PREDICTION_MARKET_INDUSTRIES:
                            category = "Other"

                        relevance_score = float(res.get("prediction_relevance", 0.5))
                        relevance_score = max(0.0, min(1.0, relevance_score))

                        scored_art = item["article"].copy()
                        scored_art.update({
                            "industry": category,
                            "score": round(relevance_score, 3), 
                            "reason": res.get("reason", "Scored using Gemini analysis."),
                            "ranking_method": "gemini-llm"
                        })
                        scored_articles.append(scored_art)
                else:
                    print(f"Gemini API returned error status {response.status_code}: {response.text}")
                    fallback = self.calculate_rule_based_scores([i["article"] for i in batch])
                    scored_articles.extend(fallback)
            except Exception as e:
                print(f"Gemini request failed: {e}")
                fallback = self.calculate_rule_based_scores([i["article"] for i in batch])
                scored_articles.extend(fallback)

        scored_articles.sort(key=lambda x: x["score"], reverse=True)
        return scored_articles
