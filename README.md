# Trusted Spots | Automated Trading Bot System

This system is a full automation of the **"Trusted Spots" 1-Minute Strategy**, designed specifically for the **$10 to $10,000 Compounding Challenge**. It combines pure price action analysis, multi-timeframe micro-confirmation, and institutional-grade risk management into a production-ready system with a stylish web dashboard.

## 🚀 Strategy: The "Trusted Spots" Method

The strategy is based on the educational teachings of the TRUSTED SPOTS channel, focusing on high-probability reversal trading at Support and Resistance (SNR) zones.

### 1. Identifying "Trusted Spots" (LuxAlgo Pivot Logic)
A "Trusted Spot" is an area of significant supply or demand. The bot identifies these using **Pivot Point Analysis**:
- **Pivot High/Low**: Uses a configurable window (default 15 bars left/right) to find absolute price peaks and troughs.
- **Wick-to-Body Zones**: Levels are not single lines but **Zones** mapped between the pivot candle's wick (High/Low) and its nearest body boundary (Open/Close).
- **5 Criteria for Strength**:
    1. **Extreme Levels**: Absolute highest/lowest points in recent history.
    2. **Series of Rejections**: Multiple historical touches at the same level.
    3. **Obviousness**: Levels that are clearly visible and "uncluttered."
    4. **Drastic Movement**: Price moves away sharply after hitting the spot.
    5. **Role Reversal**: Levels that have acted as both Support and Resistance.

### 2. The "Candlestick Story" Analysis
Before triggering a setup, the bot reads the "story" of the 1-minute candles:
- **Approach & Exhaustion**: If the price approaches a zone with high momentum (Marubozu candles), the setup is skipped. The bot looks for **decreasing body sizes** and **rejection wicks** as price nears the SNR, indicating exhaustion.
- **The Trap (Liquidity Grab)**: The bot prioritizes setups where price briefly breaches the level but fails to hold, creating a "False Breakout" signal.

### 3. Multi-Timeframe Execution (1m / 5s)
This is the "Secret Sauce" for 1-minute accuracy:
- **1-Minute Chart (Macro)**: Used to define the SNR zones and overall market context.
- **5-Second Chart (Micro)**: When price enters a 1m zone, the bot switches to a 5-second tick stream.
- **Confirmation Patterns (5s)**:
    - **Engulfing**: A fast shift in power on the micro-level.
    - **Hammer/Shooting Star**: Immediate micro-rejection wicks.
    - **Stalling**: 5-second candles failing to move further into the zone.

### 4. Trade Execution
- **Direction**: Reversal (Call at Support, Put at Resistance).
- **Expiration**: **1 Minute**.
- **Timing**: Taken immediately upon 5s micro-rejection confirmation.

---

## 💰 The $10 to $10,000 Challenge Logic

The bot is hard-coded with the strict discipline rules required to compound a small account aggressively.

### Risk Management (Kill Switch)
The bot uses a **Daily Session** model that resets every midnight:
- **Daily Target**: **30% Profit** on the starting balance of the day.
- **Stop Loss**: **3 Consecutive Losses** or if market structure is being ignored.
- **Session Limit**: Max **5 trades** per day.
- **Kill Switch**: Once any of the above limits are hit, the bot stops all activity for the day to preserve capital.

### Stake Scaling (Compounding)
- **Safe Start**: If account balance is **< $20**, the bot uses a fixed **$1 stake**.
- **Growth Mode**: If balance is **>= $20**, the bot switches to a **5% compounding stake** (rounded).
- **No Martingale**: The bot never increases stakes after a loss. It relies on high win-rate setups and consistency.

---

## 🖥️ System Features

### Stylish Dashboard
- **Live Assets**: View all monitored pairs concurrently.
- **Real-time Visualization**: See current price relative to active SNR zones.
- **Performance Metrics**: Live tracking of daily profit, target progress, and compounding milestones.
- **Live Console**: High-priority logs (touches, rejections, results) streamed via WebSockets.
- **Dark/Light Mode**: Toggle between unique aesthetic themes.

### Automation Engine
- **Multi-Asset Scanning**: Monitors up to 9+ high-payout pairs (EURUSD_otc, GBPUSD_otc, etc.) simultaneously.
- **Payout Filter**: Automatically skips any asset with a payout below **80%**.
- **LuxAlgo Integration**: Professional-grade pivot and volume oscillator logic.

---

## 🛠️ Setup & Deployment

### 1. Requirements
- Docker & Docker Compose (Recommended)
- Python 3.10+ (if running locally)

### 2. Configuration (`config.json`)
You **must** update your Pocket Option SSID in the configuration:
1. Open Pocket Option in your browser.
2. F12 (DevTools) -> Network -> Filter "WS".
3. Look for the auth message starting with `42["auth", ...]`.
4. Copy the entire string into the `POCKET_OPTION_SSID` field in `config.json` or the Dashboard UI.

### 3. Deployment
**Using Docker:**
```bash
docker-compose up -d
```
Access the dashboard at `http://localhost:5000`.

**Hosting on Railway.com:**
1. Connect your GitHub repo.
2. Railway will automatically detect the `Dockerfile`.
3. Set the `PORT` environment variable to `5000`.

---

### ⚠️ Disclaimer
*Trading binary options involves high risk. This bot is an automated execution tool based on specific price action strategies. Historical performance does not guarantee future results. Use at your own risk.*
