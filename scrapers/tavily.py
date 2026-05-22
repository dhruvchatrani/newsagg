from typing import List, Dict, Any
import urllib.parse
import requests

from scrapers.base import BaseScraper

class TavilyScraper(BaseScraper):
    """
    Scraper for Tavily Search API (https://tavily.com).
    Uses the POST /search endpoint with topic='news' for news results.
    Requires a valid TAVILY_API_KEY.
    """
    def scrape(self) -> List[Dict[str, Any]]:
        if not self.api_key:
            return []

        url = "https://api.tavily.com/search"
        query_str = self.query if self.query else "latest world news election inflation economy crypto"
        payload = {
            "api_key": self.api_key,
            "query": query_str,
            "topic": "news",
            "max_results": self.limit,
            "search_depth": "basic"
        }

        articles = []
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                for item in data.get("results", []):
                    # Extrapolate source name from URL domain
                    url_str = item.get("url", "")
                    source_name = "Tavily"
                    if url_str:
                        try:
                            parsed_url = urllib.parse.urlparse(url_str)
                            source_name = parsed_url.netloc.replace("www.", "")
                        except Exception:
                            pass
                    
                    # Tavily news topic returns a published_date field if available
                    pub_date = item.get("published_date", "")
                    
                    normalized = self.normalize_article(
                        title=item.get("title", ""),
                        url=url_str,
                        source=source_name,
                        published_at=pub_date,
                        snippet=item.get("content", "")
                    )
                    articles.append(normalized)
            else:
                pass
        except Exception:
            pass

        return articles
