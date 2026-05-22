Here's a refined version of your prompt:

---

**Goal:** Build a continuous news-to-prediction-event pipeline. The system runs in a loop — ingest news across categories, identify stories with tradable market impact, map them to assets in our universe, and generate prediction event titles + descriptions. Final output is only the event copy; resolution, pricing, and execution happen downstream.

**Inputs to define:**
- News sources — pick the mix: RSS feeds (Reuters, Bloomberg, FT), aggregators (GDELT, NewsAPI, Benzinga), social signal (X firehose, Reddit), financial wires, macro calendars
- Asset universe: ~2,100 tickers + other tradable assets (commodities, FX, crypto, indices, rates, macro prints)
- Category taxonomy (geopolitics, macro, earnings, regulatory, commodities, tech, etc.)

**Pipeline stages:**
1. **Ingest** — poll sources at interval X, dedupe, normalize to `{headline, body, source, timestamp, entities, url}`
2. **Triage** — LLM filters for stories with cross-asset price impact; drops noise, local news, already-priced-in stories
3. **Asset mapping** — for each surviving story, LLM returns affected assets from the universe with directional thesis (e.g., "US strikes Iran" → `{CL: oil↑, XOM↑, DXY↑, SPY↓, GLD↑}`)
4. **Event generation** — for each (story, asset-cluster) pair, generate a binary prediction event: clean yes/no question, resolution-friendly phrasing, measurable threshold, time bound
5. **Dedupe + rank** — collapse near-identical events across stories, score by tradability (resolution clarity, liquidity potential, market interest)

**Design decisions to lock down before building:**
- Do titles encode the threshold (`"Will WTI close above $90 by Dec 31?"`) or stay loose?
- Time horizons — daily / weekly / monthly mix?
- Fan-out policy: one event per story, or N events across all affected assets? How to avoid spamming 10 correlated oil variants from one story?
- Resolution source-of-truth — even if not your task here, titles have to be *resolvable* against some feed
- Cold-start: backfill from historical news, or live-only?
- Volume control: how many events/day is the target ceiling?

**Output schema per event:**
```
{
  title: string,           // one-line question
  description: string,     // 2-3 sentences: news context + resolution criteria + asset linkage
  linked_assets: string[], // from the 2,100-asset universe
  category: string,
  horizon: enum,
  source_story_id: string
}
```

---

Two things worth your call before we go further: (1) the **fan-out rule** is the biggest design lever — it decides whether you generate 50 events/day or 5,000, and (2) **threshold encoding in titles** affects both LLM prompt complexity and downstream resolution work. Which would you like to lock first?