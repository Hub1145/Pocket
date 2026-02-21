# Trusted Spots Trading Bot ($10 to $10k Challenge)

An automated trading bot for Pocket Option based on the "Trusted Spots" price action strategy. It uses 1-minute Support/Resistance zones and 5-second entry confirmations to replicate the strategy used in the $10 to $10,000 YouTube challenge.

## 🚀 Features
- **Pure Price Action:** No lagging indicators. Uses 1m SNR zones and 5s candle patterns.
- **Multi-Asset Monitoring:** Scans multiple currency pairs/OTC assets simultaneously via WebSockets.
- **Smart Staking:** Automatically switches from $1 flat stakes to 5% compounding once the $20 milestone is reached.
- **Web Dashboard:** Real-time metrics, log streaming, and SNR zone visualization.
- **Risk Management:** Daily 30% profit targets, 3-loss kill switch, and trade limits.

## 🛠 Setup

### 1. Requirements
- Python 3.9+
- A Pocket Option account
- Your `SSID` (Found in browser DevTools -> Network -> search for `42["auth"`)

### 2. Installation
```bash
pip install -r requirements.txt
```

### 3. Configuration
Edit `config.json`:
- Paste your `POCKET_OPTION_SSID`.
- Adjust `is_demo` to `true` or `false`.
- Set your `start_balance` (e.g., 10.0).

### 4. Running the Bot
```bash
python app.py
```
Then open `http://localhost:5000` in your browser.

## 📈 The Strategy
The bot follows a 3-step process for every trade:
1. **Identify Trusted Spots:** Scans 1m charts for levels with multiple historical rejections.
2. **Exhaustion Filter:** Ensures price isn't slamming into the level with high momentum (avoiding breakouts).
3. **5s Confirmation:** Waits for a rejection candle (Hammer, Shooting Star, or Engulfing) on the 5-second timeframe before entering.

For more details, see [STRATEGY_GUIDE.md](strategy_guide.md).

## ⚠️ Disclaimer
Trading involves significant risk. This bot is for educational purposes and to demonstrate the automation of a specific strategy. Never trade money you cannot afford to lose.
