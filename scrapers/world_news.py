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
            return []

        url = "https://api.worldnewsapi.com/search-news"
        # We can pass api-key as query param or x-api-key header.
        # Let's pass it in the headers for safety, and use 'api-key' as fallback.
        headers = {
            "x-api-key": self.api_key
        }
        params = {
            "number": self.limit,
            "language": "en"
        }
        if self.query:
            params["text"] = self.query

        articles = []
        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            
            # If header auth fails, try query param auth
            if response.status_code == 401 or response.status_code == 403:
                params["api-key"] = self.api_key
                response = requests.get(url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                for item in data.get("news", []):
                    # Extract source domain from URL as source name since World News API does not always have source name directly in standard fields.
                    url_str = item.get("url", "")
                    source_name = "World News API"
                    if url_str:
                        try:
                            parsed_url = urllib.parse.urlparse(url_str)
                            source_name = parsed_url.netloc.replace("www.", "")
                        except Exception:
                            pass
                            
                    normalized = self.normalize_article(
                        title=item.get("title", ""),
                        url=url_str,
                        source=source_name,
                        published_at=item.get("publish_date", ""),
                        snippet=item.get("summary", "") or item.get("text", "")
                    )
                    articles.append(normalized)
            else:
                pass
        except Exception:
            pass

        return articles
