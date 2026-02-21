# Trusted Spots Strategy Guide
*Based on the $10 to $10,000 Trading Challenge*

This guide details the "Pure Price Action" strategy implemented in the bot, derived from the YouTube challenge series.

## 1. SNR Selection (The "Trusted Spots")
We don't trade every line. We only trade "Trusted Spots" that meet 5 core criteria:
1. **Extreme Levels:** The highest and lowest points on the 1-minute chart within the recent 100 candles.
2. **Series of Rejections:** Points where price has touched and reversed multiple times (Pivot Points).
3. **Obviousness:** If you have to squint to see it, it's not a trusted spot.
4. **Drastic Movement:** Levels from which price previously moved away rapidly (indicating high supply/demand).
5. **S/R Flip:** A former resistance that now acts as support (or vice-versa).

### Implementation Logic:
The bot scans the last 100 1-minute candles. It identifies peaks and troughs and clusters them. Only levels with at least 2 touches (rejections) are considered "Active Zones."

## 2. The Candlestick Story (Approach)
How price approaches the level is more important than the level itself.
- **Exhaustion:** We look for candles getting smaller as they approach the zone.
- **Trap:** A "fake" breakout where price spikes through the zone and immediately pulls back.
- **Avoid Aggression:** If a massive Marubozu candle (full body, no wicks) slams into the zone, we **do not trade**. This indicates a breakout, not a reversal.

## 3. Entry Confirmation (5-Second Chart)
Once a 1-minute zone is touched:
1. Switch focus to the **5-second stream**.
2. Look for a micro-reversal pattern:
   - **Pin Bar / Hammer:** Long wick sticking into the zone.
   - **Engulfing:** A small candle followed by a larger candle in the opposite direction.
   - **Indecision:** A Doji candle exactly at the line.

The bot automates this by monitoring the 5s WebSocket stream for these specific price action patterns.

## 4. Risk Management (The Challenge Rules)
- **Daily Target:** 30% profit. Once hit, the bot stops.
- **Stake Scaling:**
  - Balance < $20: Flat $1 stake.
  - Balance >= $20: 5% of current balance (Compounding).
- **Kill Switch:** 3 consecutive losses or 5 total trades per day ends the session.

## 5. Daily Routine
1. **Reset:** Every morning, the bot captures the "Day Start Balance."
2. **Scan:** It identifies the 5 strongest zones per asset.
3. **Wait:** It sits IDLE until a price touch is detected.
4. **Execute:** It confirms rejection on the 5s timeframe and places a 1-minute trade.
