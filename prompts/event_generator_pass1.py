PASS1_INSTRUCTION = """

Task: Filter incoming news batches for real-world trends that meet the Tradability Gate AND have a clear directional impact on a Basket of instruments from the universe.

HARD REJECT — return NO mapping for any article that:
- Cannot be mapped to a tradable Basket (FX, Commodity, Crypto, Index, ETF, Sector, Company)
- Is vague macro commentary with no specific named event, actor, or decision
- Describes a trend already fully priced in with no new catalyst
- Is speculative opinion or analyst note without a firm near-term trigger
- Duplicates a story already mapped in this batch — keep only the strongest version

ACCEPT only stories where:
- A clear real-world trend or named event is unfolding (geopolitical, economic, corporate, policy)
- At least one instrument in the universe has direct mechanistic exposure to that trend
- The market direction (up or down pressure) is reasonably clear from the story

For each surviving story, return its exact URL and the mapped assets with a 1-sentence directional thesis."""
