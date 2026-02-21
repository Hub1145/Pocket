# Trusted Spots 1-Minute Trading Strategy Guide

This guide synthesizes the trading strategy and lessons from the "TRUSTED SPOTS" 100-Day/100K Trading Challenge.

## 1. Strategy Overview
- **Platform**: Pocket Option (or Quotex).
- **Timeframe**: 1-Minute Candlesticks.
- **Expiry**: 1 Minute.
- **Style**: Pure Price Action Reversals (No Indicators).

## 2. Identifying "Trusted Spots" (Strong SNR)
A "Trusted Spot" is a high-probability Support or Resistance level. A level must meet as many of these 5 criteria as possible:
1. **Extreme Levels**: The absolute highest and lowest points in the recent chart view.
2. **Series of Rejections**: Multiple touches and bounces off the level (best in ranging markets).
3. **Obviousness**: The level should be easy to spot within 3 seconds.
4. **Drastic Movement**: Price must move away sharply and significantly after touching the level.
5. **Role Reversal (S/R Flip)**: The level has acted as both support and resistance in the past.

### 2.1 Technical Zone Definition (For Automation)
To automate this, the SNR should be defined as a **Zone** rather than a single line:
- **Resistance Zone**: Between the **High (Wick)** and the **Open/Close (Body)** of the defining candle(s).
- **Support Zone**: Between the **Low (Wick)** and the **Open/Close (Body)** of the defining candle(s).
- *Logic*: The zone represents the area of "Price Rejection." If price enters this zone, it is "in the spot."

## 3. The "Candlestick Story" Analysis
Before entering, "Trusted Spots" teaches reading the "story" told by the candles:
1. **The Approach**: How is the price reaching the level?
   - *Good*: Slowing down, smaller bodies, increasing wicks (Sign of exhaustion).
   - *Bad*: Large, solid candles with no wicks (Strong momentum, likely to break through).
2. **Exhaustion Signs**: Look for "Doji" or "Spinning Top" candles as the price touches the SNR. This shows the dominant force (buyers or sellers) is losing control.
3. **The Trap**: If a candle breaks a level slightly and then pulls back (forming a long wick), it is often a "False Breakout" or "Liquidity Grab," which is a high-probability reversal signal.

## 4. Multi-Timeframe Confirmation (1m/5s)
The core of the execution is the **Micro-Rejection** technique:
- **1-Minute Chart (Macro)**: Draw your Trusted Spots zones.
- **5-Second Chart (Micro)**: Switch to this view the moment price enters the 1m zone.
- **Entry Confirmation Patterns on 5s**:
    - **The Stall**: Price enters the zone and "stops" moving for 2-3 small 5s candles.
    - **The Momentum Spike (The Trap)**: A quick, aggressive 5s candle that "breaks out" of the zone momentarily but fails to hold. *This "full momentum" spike is actually the signal to enter the reversal once it begins to pull back.*
    - **The Spike & Pull**: A quick 5s spike through the zone that immediately forms a long wick.
    - **The Reversal Candle**: A 5s Hammer or Shooting Star forming *within* or *at the edge* of the zone.
- **Automation Note on Momentum**: If the 5s candles continue to close **outside** the zone with strong bodies (Full Momentum Breakout), do NOT enter. The reversal requires a visible loss of momentum or a pull-back into the zone.

## 5. Execution Workflow
1. **Level Identification**: Draw "Trusted Spots" on the 1-minute chart.
2. **Patience**: Wait for price to enter your "Zone."
3. **Switch**: Open the **5-second timeframe** tab or toggle.
4. **Confirmation**: Wait for the micro-rejection (wick or stall) on the 5s chart.
5. **Trade**: Enter a 1-minute trade in the direction of the reversal.

## 6. Observed Risk Management: The "Kill Switch"
The strategy relies on a strict **Kill Switch Strategy** to survive the 75-day compounding journey:
- **Daily Target**: **15% profit** on the starting balance of the day.
- **Trade Count**: Typically **3 trades per day**. He stops immediately once the 15% target is reached.
- **Compounding Structure**: Starting with $100, the goal is to compound 15% daily over **75 days** to reach $100,000.
- **Observed Stop Loss**: If he suffers **2 consecutive losses** or if the market conditions (e.g., high volatility or news) stop respecting the SNR levels, he activates the "Kill Switch" and stops for the day.
- **Fixed Stake**: Each trade is roughly **5% to 7%** of the account balance (allowing for ~3 trades to reach the 15% target with ~80%+ payouts).

## 7. Psychology & Discipline
- **Selective Trading**: Quality > Quantity. He only trades when all 5 SNR criteria and the 5-second confirmation align.
- **Avoid Revenge Trading**: He emphasizes accepting losses and not trying to "win back" money in the same session.
- **Avoid FOMO**: He teaches that missing a setup is fine because the market is infinite, but a bad trade can set the challenge back days.
- **Gambler's Fallacy**: Treating each trade as a fresh, independent event regardless of previous wins or losses.

## 8. Market Selection (Observed)
- **OTC Markets**: He frequently trades OTC pairs on Pocket Option, which provide high payouts (often 92%).
- **Payout Threshold**: He typically looks for payouts above **80%** to ensure his 15% daily target is achievable with 2-3 successful trades.

## 9. Pro Tips & Lessons
- **Lesson 1**: Market direction is secondary to level reaction. Even in a downtrend, a strong support (Trusted Spot) will usually produce a 1-minute bounce.
- **Lesson 2**: If the 5s chart shows "Breaking with Momentum," do not enter even if it touches your 1m level.
- **Lesson 3**: The best trades are those where the 1m level is "Clean" (no messy price action around it).
- **Lesson 4**: Consistency comes from doing the same thing every day. He replicates the same process in every video of the challenge.

## 10. Bot Automation Logic (Developer Spec)
If you are building a bot for this strategy, follow this logic flow:
1. **Filter**: Identify 1m candles meeting the 5 "Trusted Spot" criteria.
2. **Zone Mapping**: Create a rectangle between `High` and `Max(Open, Close)` for Resistance, or `Low` and `Min(Open, Close)` for Support.
3. **Trigger**: When `Price_Current` enters the Zone on the 1m chart.
4. **Validation (5s Stream)**:
   - Start 5s interval check.
   - If a 5s candle closes **inside** the zone or shows a **rejection wick** (Wick > 50% of candle body) -> **SIGNAL BUY/SELL**.
   - If a 5s candle closes **outside** the zone with a body > 80% of total length (Full Momentum) -> **WAIT/CANCEL**.
5. **Trade**: 1 Minute Expiry in the opposite direction of the Approach.

---
*Disclaimer: Trading involves significant risk. This guide is for educational purposes based on the analyzed content.*
