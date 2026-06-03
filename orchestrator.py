import time
import json
import os
import sys
from datetime import datetime, timezone

from main import run_aggregation
from event_generator import generate_events, is_news_aggregator_enabled
from logger_setup import get_logger

logger = get_logger("newsagg.orchestrator")

QUEUE_FILE = "news_queue.json"

class MockArgs:
    def __init__(self):
        self.query = ""
        self.output = "temp_news_db.json"
        self.limit = 10
        self.sources = None
        self.no_gemini = False

def load_queue():
    if os.path.exists(QUEUE_FILE):
        try:
            with open(QUEUE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading queue file: {e}", exc_info=True)
            
    return {"processed_urls": [], "pending_articles": []}

def save_queue(queue):
    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f, indent=2)

def run_orchestrator(sleep_interval=30):
    logger.info("Starting News-to-Event Orchestrator...")
    args = MockArgs()
    
    while True:
        logger.info("Waking up to fetch news")

        if not is_news_aggregator_enabled():
            logger.info("News aggregator is DISABLED via feature flag. Skipping this cycle.")
            logger.info(f"Sleeping for {sleep_interval} seconds...")
            time.sleep(sleep_interval)
            continue

        queue = load_queue()
        
        try:
            # 1. Periodic News Fetching
            output_data = run_aggregation(args)
            
            # 2. News Queue Creation
            new_articles_count = 0
            for category, articles in output_data.get("categories", {}).items():
                for article in articles:
                    url = article.get("url")
                    if not url:
                        continue
                    
                    # Filter out already-processed and currently-pending
                    if url in queue["processed_urls"]:
                        continue
                        
                    is_pending = any(a.get("url") == url for a in queue["pending_articles"])
                    if is_pending:
                        continue
                        
                    # Add to queue
                    queue["pending_articles"].append(article)
                    new_articles_count += 1
            
            logger.info(f"Added {new_articles_count} new unique articles to the queue.")
            save_queue(queue)
            
            # 3. Threshold Check
            pending_count = len(queue["pending_articles"])
            logger.info(f"Current pending queue size: {pending_count}")
            
            if pending_count >= 5:
                logger.info(f"Threshold met ({pending_count} >= 5). Triggering Event Generation Pipeline...")
                
                articles_to_process = queue["pending_articles"][:]
                
                # 4. Event Generation
                success = generate_events(articles=articles_to_process, max_events=15)
                
                if success:
                    # Mark all processed as done
                    for art in articles_to_process:
                        queue["processed_urls"].append(art.get("url"))
                    
                    queue["pending_articles"] = []
                    save_queue(queue)
                    logger.info("Event Generation successful. Queue cleared.")
                else:
                    logger.warning("Event generation failed or returned no events.")
                    
            else:
                logger.info("Threshold not met. Waiting for more news.")

        except Exception as e:
            logger.error(f"Error in orchestrator loop: {e}", exc_info=True)
            
        logger.info(f"Sleeping for {sleep_interval} seconds...")
        time.sleep(sleep_interval)

if __name__ == "__main__":
    run_orchestrator()
