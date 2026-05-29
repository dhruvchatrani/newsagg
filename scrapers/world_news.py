from typing import List, Dict, Any
import urllib.parse
import requests

from scrapers.base import BaseScraper

class WorldNewsScraper(BaseScraper):
    """
    Scraper for World News API (https://worldnewsapi.com).
    Requires a valid WORLD_NEWS_API_KEY.
    """
    def scrape(self) -> List[Dict[str, Any]]:
        if not self.api_key:
            self.logger.warning("No API key provided for World News API. Skipping.")
            return []

        url = "https://api.worldnewsapi.com/search-news"
        headers = {
            "x-api-key": self.api_key
        }
        params = {
            "number": self.limit,
            "language": "en"
        }
        if self.query:
            params["text"] = self.query

        self.logger.info(f"Scraping World News API. Query: '{self.query}', Limit: {self.limit}, URL: {url}")
        articles = []
        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            self.logger.debug(f"World News API response status: {response.status_code}")
            
            # If header auth fails, try query param auth
            if response.status_code == 401 or response.status_code == 403:
                self.logger.info("Header authorization failed. Trying query parameter authorization fallback.")
                params["api-key"] = self.api_key
                response = requests.get(url, params=params, timeout=10)
                self.logger.debug(f"World News API (query fallback) response status: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                for item in data.get("news", []):
                    url_str = item.get("url", "")
                    source_name = "World News API"
                    if url_str:
                        try:
                            parsed_url = urllib.parse.urlparse(url_str)
                            source_name = parsed_url.netloc.replace("www.", "")
                        except Exception as e:
                            self.logger.debug(f"Failed to parse source domain from URL '{url_str}': {e}")
                            
                    normalized = self.normalize_article(
                        title=item.get("title", ""),
                        url=url_str,
                        source=source_name,
                        published_at=item.get("publish_date", ""),
                        snippet=item.get("summary", "") or item.get("text", "")
                    )
                    articles.append(normalized)
            else:
                self.logger.error(f"World News API HTTP error status: {response.status_code}. Response: {response.text}")
        except Exception as e:
            self.logger.error(f"Error fetching from World News API: {e}", exc_info=True)

        self.logger.info(f"Successfully scraped {len(articles)} articles from World News API.")
        return articles
