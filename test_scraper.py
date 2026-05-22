import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

from config import PREDICTION_MARKET_INDUSTRIES, TRUSTED_SOURCES
from scrapers.base import BaseScraper
from scrapers.google_rss import GoogleRSSScraper
from scrapers.news_api import NewsAPIScraper
from scrapers.world_news import WorldNewsScraper
from scrapers.tavily import TavilyScraper
from ranker import PredictionMarketRanker

class DummyScraper(BaseScraper):
    def scrape(self):
        return [
            self.normalize_article(
                title="US Election Odds Shift Following Debate",
                url="https://reuters.com/us-election-debate",
                source="Reuters",
                published_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                snippet="The presidential debate triggered significant swings in election prediction markets."
            )
        ]

class TestNewsAggregator(unittest.TestCase):
    
    def test_normalization(self):
        scraper = DummyScraper(query="test")
        articles = scraper.scrape()
        self.assertEqual(len(articles), 1)
        art = articles[0]
        self.assertEqual(art["source"], "Reuters")
        # Assert ISO-8601 timezone indication is present
        self.assertTrue(art["published_at"].endswith("Z") or "+00:00" in art["published_at"])
        self.assertIn("election", art["title"].lower())

    def test_deduplication(self):
        ranker = PredictionMarketRanker(query="election")
        raw_articles = [
            {
                "title": "US Election Odds Shift Following Debate",
                "url": "https://reuters.com/us-election-debate",
                "source": "Reuters",
                "published_at": "2026-05-21T06:00:00Z",
                "snippet": "Short snippet"
            },
            {
                "title": "US Election Odds Shift Following Debate",
                "url": "https://reuters.com/us-election-debate?ref=rss",
                "source": "Reuters News",
                "published_at": "2026-05-21T06:01:00Z",
                "snippet": "Longer detailed snippet about swings in election prediction markets."
            },
            {
                "title": "A completely different topic",
                "url": "https://bloomberg.com/market-news",
                "source": "Bloomberg",
                "published_at": "2026-05-21T05:00:00Z",
                "snippet": "Macroeconomics discussion."
            }
        ]
        
        deduped = ranker.deduplicate(raw_articles)
        self.assertEqual(len(deduped), 2)
        # Verify the duplicate was merged and the longer snippet was retained
        reuters_art = [a for a in deduped if "reuters" in a["url"]][0]
        self.assertEqual(reuters_art["snippet"], "Longer detailed snippet about swings in election prediction markets.")

    def test_rule_based_ranking_and_classification(self):
        ranker = PredictionMarketRanker(query="fed")
        
        now_str = datetime.now(timezone.utc).isoformat()
        yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        
        test_articles = [
            {
                "title": "Federal Reserve Hints at Interest Rate Cuts in Next Meeting",
                "url": "https://bloomberg.com/fed-rates",
                "source": "Bloomberg",
                "published_at": now_str,
                "snippet": "Fed chair indicated a high probability of rate cuts coming soon."
            },
            {
                "title": "Bitcoin Price Tumbles as ETF Flows Slow Down",
                "url": "https://someblog.xyz/crypto-news",
                "source": "Random Blog",
                "published_at": yesterday_str,
                "snippet": "Cryptocurrency markets took a hit today."
            }
        ]
        
        ranked = ranker.calculate_rule_based_scores(test_articles)
        self.assertEqual(len(ranked), 2)
        
        # Verify Industry Classifications
        fed_art = ranked[0]
        self.assertEqual(fed_art["industry"], "Economics & Macro")
        self.assertEqual(fed_art["ranking_criteria_breakdown"]["authority"], 1.0) # Bloomberg is trusted
        self.assertAlmostEqual(fed_art["ranking_criteria_breakdown"]["freshness"], 1.0, places=1)
        
        crypto_art = ranked[1]
        self.assertEqual(crypto_art["industry"], "Crypto & Web3")
        self.assertEqual(crypto_art["ranking_criteria_breakdown"]["authority"], 0.5) # Random Blog is not trusted
        
        # Bloomberg Fed article should score higher due to query relevance, freshness, and authority
        self.assertTrue(fed_art["score"] > crypto_art["score"])

    @patch('requests.get')
    def test_news_api_scraper(self, mock_get):
        # Mock News API response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "articles": [
                {
                    "source": {"name": "AP"},
                    "title": "Mock AP News",
                    "url": "https://apnews.com/mock",
                    "publishedAt": "2026-05-21T06:00:00Z",
                    "description": "Mock AP news description"
                }
            ]
        }
        mock_get.return_value = mock_response
        
        scraper = NewsAPIScraper(query="test", api_key="dummy_key")
        results = scraper.scrape()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "AP")

    @patch('requests.post')
    def test_tavily_scraper(self, mock_post):
        # Mock Tavily API response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "title": "Tavily Mock News",
                    "url": "https://tavily.com/news/1",
                    "content": "Tavily news content",
                    "published_date": "2026-05-21T06:00:00Z"
                }
            ]
        }
        mock_post.return_value = mock_response
        
        scraper = TavilyScraper(query="test", api_key="dummy_key")
        results = scraper.scrape()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "tavily.com")

if __name__ == "__main__":
    unittest.main()
