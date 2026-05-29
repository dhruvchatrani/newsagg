import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import List, Dict, Any

import config
from logger_setup import get_logger
from scrapers.google_rss import GoogleRSSScraper
from scrapers.news_api import NewsAPIScraper
from scrapers.world_news import WorldNewsScraper
from scrapers.tavily import TavilyScraper
from ranker import PredictionMarketRanker

logger = get_logger("newsagg.main")

def run_scraper(scraper_name: str, scraper_instance) -> List[Dict[str, Any]]:
    """Runs a single scraper instance and returns the normalized articles."""
    try:
        articles = scraper_instance.scrape()
        return articles
    except Exception as e:
        logger.error(f"Error running scraper {scraper_name}: {e}", exc_info=True)
        return []

def run_aggregation(args):
    query = args.query.strip() if args.query else ""

    # Initialize scraper mapping
    all_scrapers = {
        "google_rss": GoogleRSSScraper(query=query, limit=args.limit),
        "news_api": NewsAPIScraper(query=query, api_key=config.NEWS_API_KEY, limit=args.limit),
        "world_news": WorldNewsScraper(query=query, api_key=config.WORLD_NEWS_API_KEY, limit=args.limit),
        "tavily": TavilyScraper(query=query, api_key=config.TAVILY_API_KEY, limit=args.limit)
    }

    # Determine which scrapers to run
    selected_sources = []
    if args.sources:
        for src in args.sources.split(","):
            src_clean = src.strip().lower()
            if src_clean in all_scrapers:
                selected_sources.append(src_clean)
            else:
                logger.warning(f"Unknown source '{src_clean}' skipped.")
    else:
        # Run all by default, but warn about missing API keys
        selected_sources = list(all_scrapers.keys())
        missing_keys = []
        if not config.NEWS_API_KEY:
            missing_keys.append("News API (NEWS_API_KEY)")
        if not config.WORLD_NEWS_API_KEY:
            missing_keys.append("World News API (WORLD_NEWS_API_KEY/WORLD_NEWS_API)")
        if not config.TAVILY_API_KEY:
            missing_keys.append("Tavily API (TAVILY_API_KEY)")
        
        if missing_keys:
            logger.warning(f"Notice: Missing API keys for: {', '.join(missing_keys)}. "
                           f"These sources will be skipped, but available sources will run.")

    # Run selected scrapers concurrently
    aggregated_articles = []
    futures = {}
    
    with ThreadPoolExecutor(max_workers=len(selected_sources)) as executor:
        for src in selected_sources:
            scraper_instance = all_scrapers[src]
            
            # Double check if keys are present before executing
            if src == "news_api" and not config.NEWS_API_KEY:
                continue
            if src == "world_news" and not config.WORLD_NEWS_API_KEY:
                continue
            if src == "tavily" and not config.TAVILY_API_KEY:
                continue
                
            future = executor.submit(run_scraper, src, scraper_instance)
            futures[future] = src

        for future in as_completed(futures):
            src_name = futures[future]
            articles = future.result()
            logger.info(f"Scraped {len(articles)} articles from {src_name}")
            aggregated_articles.extend(articles)

    # Deduplicate articles
    ranker = PredictionMarketRanker(query)
    unique_articles = ranker.deduplicate(aggregated_articles)
    logger.info(f"Deduplicated to {len(unique_articles)} unique articles.")

    # Rank and categorize articles
    use_llm = bool(config.GEMINI_API_KEY) and not args.no_gemini
    
    if use_llm:
        logger.info("Using Gemini API for prediction market relevance and categorization...")
        final_articles = ranker.evaluate_with_gemini(unique_articles)
    else:
        logger.info("Using rule-based algorithm for ranking and categorization...")
        final_articles = ranker.calculate_rule_based_scores(unique_articles)

    # Group final results by category/industry
    grouped_articles = {category: [] for category in config.PREDICTION_MARKET_INDUSTRIES.keys()}
    grouped_articles["Other"] = []

    for art in final_articles:
        category = art.get("industry", "Other")
        if category in grouped_articles:
            grouped_articles[category].append(art)
        else:
            grouped_articles["Other"].append(art)

    # Sort each category list by score descending (to guarantee sorting)
    for category in grouped_articles:
        grouped_articles[category].sort(key=lambda x: x["score"], reverse=True)

    # Prepare JSON Output
    output_data = {
        "metadata": {
            "query": query if query else "General Headlines (No Query)",
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "total_raw_scraped": len(aggregated_articles),
            "total_unique": len(unique_articles),
            "ranking_method": "gemini-llm" if use_llm else "rule-based"
        },
        "categories": grouped_articles
    }

    # Write output or print to stdout
    json_output = json.dumps(output_data, indent=2)
    output_file = args.output if args.output else "news_database.json"
    
    if output_file:
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(json_output)
            logger.info(f"Successfully saved grouped results to {output_file}")
        except Exception as e:
            logger.error(f"Error writing output file: {e}", exc_info=True)
            # Fallback to printing to stdout
            print(json_output)
    else:
        print(json_output)

    return output_data

def main():
    parser = argparse.ArgumentParser(
        description="Scrapes, ranks, and classifies news articles for prediction market databases."
    )
    parser.add_argument(
        "--query", "-q", 
        default="",
        help="Search query to scrape news for. If empty, fetches general top news (default: empty)."
    )
    parser.add_argument(
        "--output", "-o", 
        default="news_database.json",
        help="Output JSON file path (default: news_database.json)."
    )
    parser.add_argument(
        "--limit", "-l", 
        type=int, 
        default=15, 
        help="Maximum number of articles to retrieve per source (default: 15)."
    )
    parser.add_argument(
        "--sources", "-s", 
        help="Comma-separated scrapers to use (options: google_rss, news_api, world_news, tavily)."
    )
    parser.add_argument(
        "--no-gemini", 
        action="store_true", 
        help="Force rule-based ranking even if GEMINI_API_KEY is available."
    )
    parser.add_argument(
        "--daemon", "-d",
        action="store_true",
        help="Run continuously in a loop every 3 minutes."
    )
    args = parser.parse_args()

    if args.daemon:
        logger.info("Running in continuous daemon mode. Execution will run every 3 minutes. Press Ctrl+C to terminate.")
        while True:
            start_time = time.time()
            try:
                run_aggregation(args)
            except Exception as e:
                logger.error(f"Error during daemon execution: {e}", exc_info=True)
            
            # Sleep for remainder of the 3-minute interval (180 seconds)
            elapsed = time.time() - start_time
            sleep_duration = max(1.0, 180.0 - elapsed)
            logger.info(f"Iteration completed in {elapsed:.2f}s. Sleeping for {sleep_duration:.2f}s...")
            time.sleep(sleep_duration)
    else:
        run_aggregation(args)

if __name__ == "__main__":
    main()
