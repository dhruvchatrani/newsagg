import urllib.parse
import xml.etree.ElementTree as ET
from typing import List, Dict, Any
import requests

from scrapers.base import BaseScraper

class GoogleRSSScraper(BaseScraper):
    """
    Scraper for Google News RSS Search results.
    Does not require an API key.
    """
    def scrape(self) -> List[Dict[str, Any]]:
        articles = []
        if self.query:
            # URL-encode the query
            encoded_query = urllib.parse.quote(self.query)
            rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-US&gl=US&ceid=US:en"
        else:
            # Fetch general top stories
            rss_url = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"

        articles = []
        
        # Try using feedparser first
        try:
            import feedparser
            feed = feedparser.parse(rss_url)
            
            for entry in feed.entries[:self.limit]:
                # Extract source if present
                source_name = "Google News"
                if hasattr(entry, 'source'):
                    source_name = entry.source.get('title', 'Google News')
                elif 'title' in entry and ' - ' in entry.title:
                    # Often the source name is appended at the end of title: "Headline - Source Name"
                    parts = entry.title.rsplit(' - ', 1)
                    if len(parts) > 1:
                        source_name = parts[1]
                
                # Title cleanup (remove source name at the end)
                title = entry.get('title', '')
                if ' - ' in title:
                    title = title.rsplit(' - ', 1)[0]

                # Google RSS redirects can have tracking links. Entry.link is the redirect.
                normalized = self.normalize_article(
                    title=title,
                    url=entry.get('link', ''),
                    source=source_name,
                    published_at=entry.get('published', ''),
                    snippet=entry.get('summary', '')
                )
                articles.append(normalized)
            return articles
            
        except ImportError:
            # Fallback to standard library xml.etree.ElementTree if feedparser is not installed
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                }
                response = requests.get(rss_url, headers=headers, timeout=10)
                if response.status_code == 200:
                    root = ET.fromstring(response.content)
                    channel = root.find('channel')
                    if channel is not None:
                        items = channel.findall('item')
                        for item in items[:self.limit]:
                            title_el = item.find('title')
                            link_el = item.find('link')
                            pub_date_el = item.find('pubDate')
                            description_el = item.find('description')
                            source_el = item.find('source')
                            
                            title = title_el.text if title_el is not None else ""
                            url = link_el.text if link_el is not None else ""
                            pub_date = pub_date_el.text if pub_date_el is not None else ""
                            description = description_el.text if description_el is not None else ""
                            source = source_el.text if source_el is not None else "Google News"
                            
                            # Clean source from title if needed
                            if not source_el and ' - ' in title:
                                parts = title.rsplit(' - ', 1)
                                title = parts[0]
                                source = parts[1]
                                
                            normalized = self.normalize_article(
                                title=title,
                                url=url,
                                source=source,
                                published_at=pub_date,
                                snippet=description
                            )
                            articles.append(normalized)
            except Exception as e:
                # Log error or print to stderr, return empty list
                pass
                
        return articles
