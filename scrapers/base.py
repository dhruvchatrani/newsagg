from abc import ABC, abstractmethod
from typing import List, Dict, Any
from datetime import datetime
import re

class BaseScraper(ABC):
    """
    Base class for all news scrapers.
    Forces all subclasses to implement a scrape method and provides a helper
    to normalize article fields into a standard structure.
    """
    def __init__(self, query: str, api_key: str = "", limit: int = 15):
        self.query = query
        self.api_key = api_key
        self.limit = limit

    @abstractmethod
    def scrape(self) -> List[Dict[str, Any]]:
        """
        Execute the scrape logic and return a list of normalized articles.
        Each article in the list must be normalized using self.normalize_article.
        """
        pass

    def normalize_article(
        self, 
        title: str, 
        url: str, 
        source: str, 
        published_at: str, 
        snippet: str
    ) -> Dict[str, Any]:
        """
        Helper method to standardize article dictionary format.
        Ensures dates are in ISO-8601 format or parsed safely.
        """
        clean_title = title.strip() if title else "Untitled Article"
        clean_url = url.strip() if url else ""
        clean_source = source.strip() if source else "Unknown Source"
        clean_snippet = snippet.strip() if snippet else ""

        # Normalize published date to ISO format if possible
        normalized_date = self._parse_iso_date(published_at)

        return {
            "title": clean_title,
            "url": clean_url,
            "source": clean_source,
            "published_at": normalized_date,
            "snippet": clean_snippet,
            "raw_published_at": published_at  # Keep original string just in case
        }

    def _parse_iso_date(self, date_str: str) -> str:
        """
        Attempt to parse various date formats and return a standard ISO 8601 string.
        Falls back to current time if unparseable.
        """
        if not date_str:
            return datetime.utcnow().isoformat() + "Z"
            
        # Clean string slightly
        date_str = date_str.strip()
        
        # Try common formats
        formats = [
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%a, %d %b %Y %H:%M:%S %Z",  # RSS format
            "%a, %d %b %Y %H:%M:%S %z",  # RSS format with offset
            "%d %b %Y %H:%M:%S %z",
            "%Y-%m-%d",
        ]
        
        # Strip some RSS timezones that Python's datetime struggles with (like GMT/EST)
        cleaned_date = re.sub(r'\s+([A-Z]{3,4})$', r' \1', date_str)

        for fmt in formats:
            try:
                dt = datetime.strptime(cleaned_date, fmt)
                return dt.isoformat() + "Z" if not dt.tzinfo else dt.isoformat()
            except ValueError:
                continue
                
        # Try raw ISO-8601 parse attempt via generic ISO parser
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.isoformat()
        except Exception:
            pass

        # Return default if all else fails
        return date_str
