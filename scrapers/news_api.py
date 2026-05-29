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
            self.logger.warning("No API key provided for News API. Skipping.")
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

        self.logger.info(f"Scraping News API. Query: '{self.query}', Limit: {self.limit}, URL: {url}")
        articles = []
        try:
            response = requests.get(url, params=params, timeout=10)
            self.logger.debug(f"News API response status: {response.status_code}")
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
                    self.logger.error(f"News API returned non-ok status in JSON: {data.get('message')}")
            else:
                self.logger.error(f"News API HTTP error status: {response.status_code}. Response: {response.text}")
        except Exception as e:
            self.logger.error(f"Error fetching from News API: {e}", exc_info=True)

        self.logger.info(f"Successfully scraped {len(articles)} articles from News API.")
        return articles
