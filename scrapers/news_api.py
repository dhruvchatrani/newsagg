from typing import List, Dict, Any
import requests

from scrapers.base import BaseScraper

class NewsAPIScraper(BaseScraper):
    """
    Scraper for News API (https://newsapi.org).
    Requires a valid NEWS_API_KEY.
    """
    def scrape(self) -> List[Dict[str, Any]]:
        if not self.api_key:
            # Silently skip if no API key provided, or print warning to stderr
            return []

        if self.query:
            url = "https://newsapi.org/v2/everything"
            params = {
                "q": self.query,
                "apiKey": self.api_key,
                "pageSize": self.limit,
                "language": "en",
                "sortBy": "relevancy"
            }
        else:
            url = "https://newsapi.org/v2/top-headlines"
            params = {
                "apiKey": self.api_key,
                "pageSize": self.limit,
                "language": "en"
            }

        articles = []
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "ok":
                    for item in data.get("articles", []):
                        source_name = item.get("source", {}).get("name", "News API")
                        
                        normalized = self.normalize_article(
                            title=item.get("title", ""),
                            url=item.get("url", ""),
                            source=source_name,
                            published_at=item.get("publishedAt", ""),
                            snippet=item.get("description", "") or item.get("content", "")
                        )
                        articles.append(normalized)
            else:
                # Log error or capture rate limits
                pass
        except Exception:
            pass

        return articles
