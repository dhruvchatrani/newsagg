1. OVERVIEW -- What This Project Does
Newsagg (News Aggregator for Prediction Markets) is a Python-based pipeline that:
1. Scrapes news from 4 sources concurrently (Google RSS, News API, World News API, Tavily)
2. Deduplicates, scores, and categorizes articles for prediction-market relevance
3. Generates prediction-market events from high-value articles using LLMs (Gemini API)
4. Pushes events to an external "Cascade" / Bitzaurus platform as tradable binary prediction markets
It is designed to feed a prediction-market platform with automatically generated, news-driven trading events.
2. PROJECT STRUCTURE
newsagg/
  .env                    # API keys (gitignored)
  .env.example            # Template for API keys
  .gitignore              # Ignores __pycache__, .env, output JSONs, logs
  requirements.txt        # requests, feedparser, python-dotenv, google-genai
  config.py               # Configuration: API keys, industry keywords, trusted sources, ranking weights
  logger_setup.py         # Logging: console + rotating file handler (aggregator.log)
  main.py                 # Entry point: CLI argument parsing, multi-source scraping, ranking, JSON output
  orchestrator.py         # Continuous loop: runs aggregation, builds queue, triggers event generation at threshold
  event_generator.py      # Two-pass LLM pipeline: triage articles -> map assets -> generate events -> push to API
  ranker.py               # PredictionMarketRanker class: deduplication, rule-based scoring, Gemini-based scoring
  run_aggregator.sh       # Shell script to run main.py in daemon mode
  scrapers/
    __init__.py           # (not present -- package via directory)
    base.py               # BaseScraper ABC: normalize_article(), _parse_iso_date()
    google_rss.py         # GoogleRSSScraper  -- feedparser + XML fallback (no API key)
    news_api.py           # NewsAPIScraper    -- newsapi.org
    world_news.py         # WorldNewsScraper  -- worldnewsapi.com
    tavily.py             # TavilyScraper     -- tavily.com (POST /search)
  prompts/
    __init__.py
    event_generator_base.py     # BASE_PROMPT -- tradability gate rules
    event_generator_pass1.py    # PASS1_INSTRUCTION -- triage & asset mapping
    event_generator_pass2.py    # PASS2_INSTRUCTION -- single-event generation with title/description/worthiness rules
    ranker_gemini.py            # PROMPT -- Gemini ranking prompt template
  scripts/
    event_generator_local.py    # Standalone event generator (runs locally, saves to prediction_events.json)
    push_events_local.py        # Standalone push script: reads prediction_events.json, starts Quantum run, polls, allocates, posts to Bitzaurus
    parse_assets.py             # Extracts ticker symbols from bucketlist.txt -> assets.json
  tests/
    test_scraper.py             # Unit tests for normalization, dedup, rule-based ranking, News API/Tavily mocks
    test_orchestrator.py        # Unit tests for queue load/save, threshold logic, duplicate injection, error handling
  # Runtime output files (gitignored):
  news_database.json            # Latest aggregated + ranked + categorized articles
  prediction_events.json        # LLM-generated prediction events
  news_queue.json               # Orchestrator's persistent queue (processed_urls + pending_articles)
  results.json                  # Output from a past run (query="Bitcoin ETF")
  aggregator.log                # Rotating log file (5MB x 3)
  assets.json                   # Tradable asset universe (2514 tickers/symbols)
3. DATA MODELS / SCHEMAS
3a. Normalized Article Schema (from BaseScraper.normalize_article)
Every scraper produces this uniform dictionary:
{
    "title":           str,          # Cleaned title
    "url":             str,          # Canonical URL
    "source":          str,          # Domain/source name
    "published_at":    str,          # ISO-8601 normalized date
    "snippet":         str,          # Summary/description
    "raw_published_at": str          # Original date string (preserved)
}
3b. Scored / Ranked Article Schema (from PredictionMarketRanker)
After ranking, articles are augmented with:
{
    # ... all normalized fields ...
    "industry":  str,                # One of 9 categories (Politics, Economics & Macro, Crypto & Web3, etc.)
    "score":     float,              # 0.0 - 1.0 (rule-based or Gemini-assigned)
    "reason":    str,                # (Gemini only) one-sentence catalyst reasoning
    "ranking_method": str,           # "rule-based" | "gemini-llm"
    # (rule-based only):
    "ranking_criteria_breakdown": {
        "freshness":  float,         # 0.0-1.0 based on hours old (decay over 72h)
        "authority":  float,         # 0.5 (default) or 1.0 (trusted source)
        "relevance":  float          # 0.0-1.0 based on query match + prediction keywords
    }
}
3c. Prediction Market Event Schema (from event_generator.py Pass 2)
This is the core "event" model you specifically asked about:
{
    "source_story_id":    str,       # Original article URL
    "validation_check":   bool,      # Credible/unique source?
    "tradability_check":  bool,      # Clear asset path?
    "virality_check":     bool,      # Social/news momentum?
    "worthiness_assessment": bool,   # Important enough?
    "decision_approved":  bool,      # All four pillars passed?
    "worthy":             bool,      # final worthy gate
    "title":              str,       # Short uncertainty question (30-55 chars)
    "causal_chain":       str,       # "Trigger -> Mechanism -> Direction"
    "description":        str,       # 5+ dense sentences with resolution terms
    "linked_assets":      [str],     # e.g. ["EURUSD"], ["BTCUSD"], ["LMT.NYSE"]
    "directional_impact": str,       # "Bullish" | "Bearish" | "Volatile"
    "category":           str,       # e.g. "Macroeconomics"
    "horizon":            str,       # "weekly"
    "tradability_score":  float      # 0.0-1.0, must be >= 0.90 to be accepted
}
3d. Output news_database.json Schema
{
    "metadata": {
        "query":            str,
        "timestamp":        str (ISO-8601),
        "total_raw_scraped": int,
        "total_unique":      int,
        "ranking_method":   str
    },
    "categories": {
        "Politics":                   [scored_article, ...],
        "Economics & Macro":          [...],
        "Crypto & Web3":              [...],
        "Geopolitics":                [...],
        "Tech & AI":                  [...],
        "Science & Health":           [...],
        "Sports":                     [...],
        "Pop Culture & Entertainment": [...],
        "Other":                      [...]
    }
}
3e. Output prediction_events.json Schema
{
    "metadata": {
        "timestamp":        str,
        "events_generated": int,
        "engine":           str
    },
    "events": [event, ...]   # Array of Prediction Market Event objects
}
3f. Queue (news_queue.json) Schema
{
    "processed_urls":   [str, ...],
    "pending_articles": [scored_article, ...]
}
4. API ENDPOINTS / EXTERNAL SERVICE CALLS
The project does not expose its own API endpoints. Instead, it consumes external APIs and pushes data to external services:
Scraper APIs consumed:
Scraper
GoogleRSSScraper
NewsAPIScraper
WorldNewsScraper
TavilyScraper
Gemini (LLM) APIs consumed:
- Ranking: POST https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}
- Event generation (Pass 1 & 2): Same Gemini endpoint, with systemInstruction and responseMimeType: "application/json"
Push / Export mechanisms:
1. Quantum API (for AI basket computation):
- POST {QUANTUM_API_URL}/runs/start -- start a run
- GET  {QUANTUM_API_URL}/runs/{runId}/status -- poll status
- GET  {QUANTUM_API_URL}/runs/{runId} -- fetch run details/allocations
2. Bitzaurus Markets API (to create actual prediction markets):
- POST https://api.bitzaurus.com/api/admin/markets -- create market with title, description, outcomes, allocations, open/close times
3. Local file export (no server needed):
- news_database.json -- grouped/categorized/ranked articles
- prediction_events.json -- generated events
- results.json -- alternative output file
5. KEY ENTRY POINTS
File	Purpose
main.py	Primary CLI entry point. Scrapes, ranks, categorizes, outputs JSON.
main.py --daemon	Continuous 3-minute loop mode.
orchestrator.py	Continuous pipeline. Runs aggregation, builds queue, triggers event generation when >= 5 articles are pending.
event_generator.py	Two-pass LLM event generation + push to Quantum/Bitzaurus. Can be run standalone.
scripts/event_generator_local.py	Same as event_generator.py but saves locally only (no push).
scripts/push_events_local.py	Picks one event from prediction_events.json, executes full Quantum run -> poll -> allocate -> push pipeline.
scripts/parse_assets.py	Extracts ticker symbols from bucketlist.txt into assets.json.
6. CONFIGURATION
File: /home/drax/work/wesee/newsagg/config.py
API Keys (from .env):
Variable
NEWS_API_KEY
WORLD_NEWS_API_KEY
TAVILY_API_KEY
GEMINI_API_KEY
GEMINI_MODEL
Category System (PREDICTION_MARKET_INDUSTRIES):
8 categories with keyword lists: Politics, Economics & Macro, Crypto & Web3, Geopolitics, Tech & AI, Science & Health, Sports, Pop Culture & Entertainment, plus an implicit Other.
Trusted Sources (TRUSTED_SOURCES):
35 high-authority news outlets (Reuters, AP, Bloomberg, FT, WSJ, NYT, BBC, etc.)
Ranking Weights (RANKING_WEIGHTS):
- Freshness: 0.35
- Authority: 0.30
- Relevance: 0.35
Event Generation Threshold (in orchestrator.py):
- Minimum 5 pending articles before triggering event generation
7. DATA FLOW SUMMARY
```
                    +-----------+
                    |  .env     |  (API keys)
                    +-----+-----+
                          |
  +-------+  +--------+  +-------+  +--------+  +---------+
  |Google |  | News   |  | World |  | Tavily |  | (Gemini |
  | RSS   |  | API    |  | News  |  |        |  |  API)   |
  +---+---+  +---+----+  +---+---+  +---+----+  +----+----+
      |          |            |          |            |
      +----------+-----+------+----------+            |
                       |                               |
                  +----v----+                     +----v----+
                  |  main.py|--[rank/dedup]------>| Gemini  |
                  | (scrape)|                     | Ranker  |
                  +----+----+                     +----+----+
                       |                               |
                  +----v----+                          |
                  | news_db |                          |
                  |  .json  |                          |
                  +----+----+                          |
                       |                               |
                  +----v----+                     +----v----+
                  | orches- |  (if pending>=5)    | event_  |
                  | trator  +-------------------->| generator|
                  +---------+                     +----+----+
                                                        |
                                        +---------------+---------------+
                                        |                               |
                                   +----v----+                    +-----v------+
                                   | pred_   |                    | Quantum    |
                                   | events  |                    | API (basket|
                                   | .json   |                    | allocation)|
                                   +----+----+                    +-----+------+
                                        |                               |
                                        +---------------v---------------+
                                                        |
                                                  +-----v------+
                                                  | Bitzaurus  |
                                                  | Markets API|
                                                  | (create     |
                                                  |  markets)   |
                                                  +------------+
```
8. KEY FUNCTIONS (by file)
File	Function
main.py	run_aggregation(args)
main.py	main()
orchestrator.py	run_orchestrator()
orchestrator.py	load_queue() / save_queue()
event_generator.py	generate_events()
event_generator.py	_start_run() / _poll_done() / _get_allocations_payload()
event_generator.py	call_gemini_api()
ranker.py	PredictionMarketRanker.deduplicate()
ranker.py	PredictionMarketRanker.calculate_rule_based_scores()
ranker.py	PredictionMarketRanker.evaluate_with_gemini()
scrapers/base.py	BaseScraper.normalize_article()
scrapers/base.py	BaseScraper._parse_iso_date()
9. EVENT MODEL DETAIL
The "event" model you specifically asked about is defined across two stages:
Pass 1 (Triage) -- produces a mapping:
{
    "source_story_id": str,          # Article URL
    "mapped_assets": [{
        "ticker": str,               # e.g. "EURUSD", "BTCUSD", "LMT.NYSE"
        "directional_thesis": str    # "Bullish|Bearish|Volatile - explanation"
    }]
}
Pass 2 (Event Generation) -- produces the final event object (detailed in section 3c above). The key fields that control quality gates are:
- worthy (bool) -- must be true
- decision_approved (bool) -- must be true
- tradability_score (float) -- must be >= 0.90
Events that pass all gates are saved to prediction_events.json (capped at 1 event per run in the main event_generator.py, but up to max_events in the local version). They are then pushed through the Quantum -> Bitzaurus pipeline to create real tradable markets.
10. NOTABLE DETAILS
- Daemon mode runs on a 3-minute interval (180 seconds).
- Orchestrator runs on a 30-second interval and triggers event generation when the pending queue reaches 5 articles.
- Rule-based scoring uses keyword matching against 8 industry categories with PREDICTION_MARKET_INDUSTRIES.
- LLM scoring (Gemini) processes articles in batches of 10.
- Event generation Pass 1 processes articles in batches of 5 (for KV cache optimization).
- Price discovery step between passes uses Tavily API to fetch recent price context for mapped assets.
- Two scripts (push_events_local.py and the push logic in event_generator.py) handle the Quantum-to-Bitzaurus pipeline with slightly different allocation algorithms (one with min/max bounds of 2.5%/40%, the other without).
