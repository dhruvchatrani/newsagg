PASS2_INSTRUCTION = """

Task: Build a single Cascade prediction-market event for an isolated target story.
It is better to reject with worthy=false than to generate a mediocre event.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TITLE PHILOSOPHY & RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- The title must be a SHORT, PUNCHY QUESTION that frames genuine market uncertainty.
- VARY your openers across these styles — do NOT repeat the same opener twice in a row:
  Uncertainty / possibility:  Could | Might | May | Is it possible that… | Are we likely to see… | Will there be… | Is there a chance that… | Can we expect… | Could we witness… | Is X headed toward…
  Market / forecast:          Odds of… | Chances of… | Probability that… | Likelihood of… | Market expects… | Forecast for… | Predicted outcome… | Implied odds of… | Consensus expectation…
  Engaging / dramatic:        Will X finally… | Is X about to… | Could X trigger… | Are we nearing… | Is X on track to… | Will X manage to… | Could this lead to… | Is X at risk of… | Is X poised to…
  Data / finance:             Will X surpass… | Will X fall below… | Expected range for… | Probability of approval… | Likelihood of recession… | Odds of rate cuts… | Chances of breakout… | Will markets price in…
  Casual / conversational:    Think X happens? | Betting on X? | Bullish on X? | Will X actually happen? | Is X cooked? | Is X inevitable now? | Calling it now: will X…?
- Length: Max 65 characters, ideally between 30 and 55 characters.
- Keep it tight: remove articles (a/an/the), trim filler words.
- BANNED: Price targets, percentages, "Cross X by Y", ticker symbols in the title.
- BANNED openers: "Market Reaction to", "Impact of", "Effect of", "Outlook for", "Analysis of", "Do you think".
- The subject MUST be a real named entity, country, region, trend, or sector.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DESCRIPTION RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Explain the news context and why people may react.
- Length: Max 170 characters, ideally between 120 and 150 characters.
- Must be concise and informative, avoiding filler words or unnecessary padding.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WORTHY GATE & EVALUATION LAYER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Evaluate the event strictly across four pillars:
1. Validation Check: Is the source credible and unique?
2. Tradability Check: Does the trend have clear, actionable market relevance for a specific asset?
3. Virality Check: Is there measurable trend strength, engagement, or social/news momentum?
4. Worthiness Assessment: Is this event important enough for downstream trading?

If ALL four pillars pass, set decision_approved=true. Otherwise, decision_approved=false.
Set worthy=false if decision_approved=false, OR if any title/description rules fail.
Honest tradability_score MUST be above 0.90 to be worthy.

CAUSAL CHAIN: Provide a compact "Real-World Trigger -> Market Mechanism -> Asset Direction" string."""
