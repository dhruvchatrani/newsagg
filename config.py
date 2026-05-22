import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path, override=True)

# API Keys
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "").strip()
WORLD_NEWS_API_KEY = os.getenv("WORLD_NEWS_API_KEY", os.getenv("WORLD_NEWS_API", "")).strip()
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Target Prediction Market Industries / Categories
PREDICTION_MARKET_INDUSTRIES = {
    "Politics": [
        "election", "vote", "president", "senate", "house", "congress", "democrat", "republican", 
        "nominee", "poll", "candidate", "legislation", "supreme court", "governor", "primary"
    ],
    "Economics & Macro": [
        "inflation", "cpi", "fed", "federal reserve", "interest rate", "gdp", "employment", "unemployment", 
        "nfp", "recession", "tariff", "debt ceiling", "stocks", "market", "economy", "sec", "bond", "yield"
    ],
    "Crypto & Web3": [
        "bitcoin", "ethereum", "crypto", "blockchain", "defi", "nft", "etf", "sec", "binance", "coinbase", 
        "halving", "solana", "stablecoin", "fed", "regulation"
    ],
    "Geopolitics": [
        "sanctions", "treaty", "conflict", "nato", "un", "border", "military", "war", "alliance", 
        "china", "russia", "taiwan", "ukraine", "middle east", "nuclear", "trade war"
    ],
    "Tech & AI": [
        "artificial intelligence", "ai", "openai", "gpt", "gemini", "nvidia", "llm", "semiconductor", 
        "chip", "apple", "google", "meta", "microsoft", "acquisition", "merger", "quantum"
    ],
    "Science & Health": [
        "fda", "clinical trial", "vaccine", "cancer", "space", "nasa", "spacex", "launch", 
        "fusion", "climate change", "superconductor", "patent", "outbreak", "who"
    ],
    "Sports": [
        "olympics", "championship", "nfl", "nba", "super bowl", "world cup", "fifa", "premier league", 
        "finalist", "tournament", "formula 1", "mlb", "mvp"
    ],
    "Pop Culture & Entertainment": [
        "oscars", "grammys", "box office", "netflix", "celebrity", "award", "disney", "trailer", 
        "ratings", "viral", "album", "premiere"
    ]
}

# Default list of high-trust sources for prediction markets (e.g., major news publications, agencies, specialist outlets)
TRUSTED_SOURCES = [
    # General & High Trust Agencies
    "reuters", "ap", "associated press", "bloomberg", "financial times", "ft.com", 
    "the economist", "wall street journal", "wsj", "nytimes", "new york times", 
    "washington post", "bbc", "bbc news", "guardian", "cnbc",
    # Tech / AI Specialists
    "techcrunch", "the verge", "wired", "venturebeat",
    # Crypto Specialists
    "coindesk", "cointelegraph", "the block", "decrypt",
    # Politics & Policy
    "politico", "axios", "hill", "the hill", "fivethirtyeight"
]

# Ranking weights for criteria (should sum to 1.0)
RANKING_WEIGHTS = {
    "freshness": 0.35,     # Penalty based on article age
    "authority": 0.30,     # Bonus if source is in TRUSTED_SOURCES
    "relevance": 0.35      # Keyword/Semantic relevance to the search query and prediction interest
}
