import asyncio
import os
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Set
import pandas as pd
from loguru import logger

from pocketoptionapi_async import AsyncPocketOptionClient, OrderDirection, OrderStatus
from pocketoptionapi_async.models import Candle

class TrustedSpotsBot:
    def __init__(self, config: Dict):
        self.config = config
        self.ssid = config.get("POCKET_OPTION_SSID")
        self.assets = config.get("assets", ["EURUSD_otc"])
        self.is_demo = config.get("is_demo", True)

        self.client = AsyncPocketOptionClient(self.ssid, is_demo=self.is_demo)

        # Risk Management State
        rm_config = config.get("risk_management", {})
        self.day_start_balance = 0.0
        self.current_balance_cached = 0.0
        self.daily_target_pct = rm_config.get("daily_target_pct", 0.30)
        self.max_losses_streak = rm_config.get("max_losses_streak", 3)
        self.max_trades_per_day = rm_config.get("max_trades_per_day", 5)
        self.min_payout = rm_config.get("min_payout_pct", 0.80)

        self.current_losses_streak = 0
        self.trades_taken_today = 0
        self.kill_switch_active = False

        # Automation Params
        auto_config = config.get("automation", {})
        self.snr_update_interval = auto_config.get("snr_update_interval_mins", 15)
        self.rejection_window = auto_config.get("rejection_monitor_seconds", 30)

        # State Management
        self.resistance_zones: Dict[str, List[Dict]] = {asset: [] for asset in self.assets}
        self.support_zones: Dict[str, List[Dict]] = {asset: [] for asset in self.assets}
        self.last_snr_update: Dict[str, datetime] = {asset: datetime.min for asset in self.assets}
        self.asset_payouts: Dict[str, float] = {asset: 0.0 for asset in self.assets}

        # Real-time Tracking State
        self.asset_states: Dict[str, str] = {asset: "IDLE" for asset in self.assets} # IDLE, TOUCHING, COOLING_DOWN
        self.touch_start_time: Dict[str, float] = {asset: 0.0 for asset in self.assets}
        self.asset_last_price: Dict[str, float] = {asset: 0.0 for asset in self.assets}
        self.cooldown_until: Dict[str, float] = {asset: 0.0 for asset in self.assets}

    async def initialize(self):
        # Register Event Callbacks
        self.client.add_event_callback("payout_update", self._on_payout_update)
        self.client.add_event_callback("stream_update", self._on_stream_update)
        self.client.add_event_callback("balance_updated", self._on_balance_updated)

        # Connect to PO
        connected = await self.client.connect()
        if not connected:
            raise ConnectionError("Failed to connect to PocketOption.")

        await self.start_new_session()

        # Initial SNR Mappings and Subscriptions
        for asset in self.assets:
            await self.update_snr_zones(asset)
            # Subscribe to 5s stream for high-resolution tick tracking
            # Note: We use the internal changeSymbol logic
            msg = f'42["changeSymbol", {{"asset": "{asset}", "period": 5}}]'
            await self.client.send_message(msg)
            logger.info(f"Subscribed to 5s stream for {asset}")
            await asyncio.sleep(0.5) # Avoid spamming the server

    async def start_new_session(self):
        """Reset daily counters and capture start-of-day balance"""
        balance_info = await self.client.get_balance()
        self.day_start_balance = balance_info.balance
        self.trades_taken_today = 0
        self.current_losses_streak = 0
        self.kill_switch_active = False
        logger.info(f"--- NEW SESSION STARTED ---")
        logger.info(f"Start Balance: ${self.day_start_balance:.2f} | Target: +{self.daily_target_pct:.0%}")

    def _on_balance_updated(self, balance_obj):
        self.current_balance_cached = balance_obj.balance
        # logger.debug(f"Cached balance updated: ${self.current_balance_cached:.2f}")

    def _on_payout_update(self, data: Dict):
        symbol = data.get("symbol")
        payout = data.get("payout", 0) / 100.0
        if symbol in self.asset_payouts:
            self.asset_payouts[symbol] = payout

    async def _on_stream_update(self, data: Dict):
        """Websocket Event Handler for real-time price ticks"""
        if self.kill_switch_active:
            return

        asset = data.get("asset")
        if asset not in self.assets:
            return

        candles_data = data.get("data") or data.get("candles") or []
        if not candles_data:
            return

        last_item = candles_data[-1]
        current_price = 0.0
        if isinstance(last_item, dict):
            current_price = float(last_item.get("close", 0))
        elif isinstance(last_item, (list, tuple)) and len(last_item) >= 3:
            current_price = float(last_item[2])

        self.asset_last_price[asset] = current_price

        if self.asset_payouts.get(asset, 0) < self.min_payout:
            return

        if time.time() < self.cooldown_until[asset]:
            return

        state = self.asset_states[asset]

        if state == "IDLE":
            target_dir = self._check_zone_touch(asset, current_price)
            if target_dir:
                # NEW: Approach & Exhaustion Filter
                if await self._is_approach_aggressive(asset, target_dir):
                    logger.info(f"[{asset}] Approach too aggressive (Full Momentum). Skipping.")
                    self.cooldown_until[asset] = time.time() + 30 # Small cooldown to avoid spam
                    return

                logger.info(f"[{asset}] Zone Touch Detected. Price: {current_price}. Entering TOUCHING state.")
                self.asset_states[asset] = "TOUCHING"
                self.touch_start_time[asset] = time.time()
                self.config[f"{asset}_target_dir"] = target_dir

        elif state == "TOUCHING":
            # Check timeout
            if time.time() - self.touch_start_time[asset] > self.rejection_window:
                logger.info(f"[{asset}] Rejection window timed out. Returning to IDLE.")
                self.asset_states[asset] = "IDLE"
                return

            # Check Rejection Confirmation (using the latest stream data)
            target_dir = self.config.get(f"{asset}_target_dir")
            if await self._check_rejection_confirmed(asset, data, target_dir):
                # EXECUTE TRADE
                self.asset_states[asset] = "COOLING_DOWN"
                # Launch trade in background to not block the WS thread
                asyncio.create_task(self.execute_trade(asset, target_dir))
                self.cooldown_until[asset] = time.time() + 70 # Wait for trade + buffer

    async def _is_approach_aggressive(self, asset: str, direction: OrderDirection) -> bool:
        """Exhaustion Filter: Check if the 1m approach is too strong (momentum) or showing rejection (exhaustion)"""
        try:
            candles = await self.client.get_candles(asset, 60, count=2)
            if len(candles) < 2: return False

            last = candles[-1]
            body = abs(last.close - last.open)
            total_range = last.high - last.low if last.high > last.low else 0.0001

            # Pattern 1: Marubozu Check (Avoid entry if approaching candle is solid)
            # If body is > 80% of total candle and color matches direction of approach
            if (direction == OrderDirection.PUT and last.close > last.open and body/total_range > 0.8) or \
               (direction == OrderDirection.CALL and last.close < last.open and body/total_range > 0.8):
                return True # Aggressive momentum

            # Pattern 2: Rejection Confirmation (Good entry if approaching candle has a wick)
            # This is "exhaustion" - price already tried to go through and failed on 1m
            wick_against = (last.high - max(last.open, last.close)) if direction == OrderDirection.PUT else (min(last.open, last.close) - last.low)
            if wick_against > (body * 0.5):
                logger.debug(f"[{asset}] 1m Exhaustion wick detected. High quality setup.")
                return False # Not aggressive (it's exhausted)

            return False
        except Exception as e:
            logger.error(f"Exhaustion filter error: {e}")
            return False

    def _check_zone_touch(self, asset: str, price: float) -> Optional[OrderDirection]:
        for zone in self.resistance_zones[asset]:
            if zone['lower'] <= price <= zone['upper']:
                return OrderDirection.PUT
        for zone in self.support_zones[asset]:
            if zone['lower'] <= price <= zone['upper']:
                return OrderDirection.CALL
        return None

    async def _check_rejection_confirmed(self, asset: str, stream_data: Dict, direction: OrderDirection) -> bool:
        """Analyze the stream data for 5s candlestick patterns: Hammer, Shooting Star, Engulfing"""
        candles = self.client._parse_stream_candles(stream_data, asset, 5)
        if len(candles) < 2: return False

        last = candles[-1]
        prev = candles[-2]

        last_body = abs(last.close - last.open)
        prev_body = abs(prev.close - prev.open)
        last_range = last.high - last.low if last.high > last.low else 0.0001

        wick_top = last.high - max(last.open, last.close)
        wick_bottom = min(last.open, last.close) - last.low

        # Pattern 1: Reversal Engulfing (Strong Signal)
        if direction == OrderDirection.PUT and last.close < last.open and prev.close > prev.open:
            if last_body > prev_body * 0.8: # Engulfing or near-engulfing
                logger.info(f"[{asset}] 5s Bearish Engulfing Rejection.")
                return True
        if direction == OrderDirection.CALL and last.close > last.open and prev.close < prev.open:
            if last_body > prev_body * 0.8:
                logger.info(f"[{asset}] 5s Bullish Engulfing Rejection.")
                return True

        # Pattern 2: Pin Bar / Hammer / Shooting Star
        if direction == OrderDirection.PUT and wick_top > last_body * 2:
            logger.info(f"[{asset}] 5s Shooting Star (Rejection Wick).")
            return True
        if direction == OrderDirection.CALL and wick_bottom > last_body * 2:
            logger.info(f"[{asset}] 5s Hammer (Rejection Wick).")
            return True

        # Pattern 3: Doji / Stall (Indecision at level)
        if last_body < last_range * 0.15:
            logger.info(f"[{asset}] 5s Doji Indecision detected.")
            return True

        # Invalidation: Breaking Zone with Full Momentum
        if last_body / last_range > 0.9 and ((direction == OrderDirection.PUT and last.close > last.open) or (direction == OrderDirection.CALL and last.close < last.open)):
            logger.info(f"[{asset}] Breaking Zone with Momentum. Invalidation.")
            self.asset_states[asset] = "IDLE"
            return False

        return False

    async def get_stake(self) -> float:
        """Refined Stake Logic for $10-$10k Challenge:
        - If balance < $20, use $1 stake (corrected).
        - If balance >= $20, use 5% compounding stake.
        """
        balance_info = await self.client.get_balance()
        balance = balance_info.balance

        if balance < 20.0:
            stake = 1.0
        else:
            # 5% Compounding Stake
            stake = round(balance * 0.05, 2)

        # Ensure minimum $1 (Platform rule)
        return max(1.0, stake)

    async def check_kill_switch(self) -> bool:
        """Check if session should end based on daily start balance"""
        balance_info = await self.client.get_balance()
        current_balance = balance_info.balance
        profit = current_balance - self.day_start_balance

        if profit >= self.day_start_balance * self.daily_target_pct:
            logger.success(f"DAILY GOAL MET: +${profit:.2f} (>= 30%). Kill Switch ON.")
            self.kill_switch_active = True
            return True
        if self.current_losses_streak >= self.max_losses_streak:
            logger.warning(f"LOSS LIMIT HIT: {self.current_losses_streak} losses. Kill Switch ON.")
            self.kill_switch_active = True
            return True
        if self.trades_taken_today >= self.max_trades_per_day:
            logger.info(f"TRADE LIMIT REACHED: {self.trades_taken_today} trades. Kill Switch ON.")
            self.kill_switch_active = True
            return True
        return False

    async def update_snr_zones(self, asset: str):
        """Fetch 1m candles and identify zones"""
        logger.info(f"Remapping SNR for {asset}...")
        try:
            candles = await self.client.get_candles(asset, 60, count=100)
            if not candles:
                return

            self.resistance_zones[asset] = []
            self.support_zones[asset] = []

            df = pd.DataFrame([{
                'high': c.high, 'low': c.low,
                'open': c.open, 'close': c.close
            } for c in candles])

            # Extreme Levels
            res_idx = df['high'].idxmax()
            sup_idx = df['low'].idxmin()

            self.resistance_zones[asset].append({
                'upper': df['high'].iloc[res_idx],
                'lower': max(df['open'].iloc[res_idx], df['close'].iloc[res_idx])
            })
            self.support_zones[asset].append({
                'lower': df['low'].iloc[sup_idx],
                'upper': min(df['open'].iloc[sup_idx], df['close'].iloc[sup_idx])
            })

            # Pivot Points (Rejections)
            peaks = df[(df['high'] == df['high'].rolling(10, center=True).max())]
            troughs = df[(df['low'] == df['low'].rolling(10, center=True).min())]

            def cluster(levels, is_res=True):
                zones = []
                for _, row in levels.iterrows():
                    val = row['high'] if is_res else row['low']
                    found = False
                    for z in zones:
                        if abs(z['ref'] - val) / val < 0.0005:
                            z['count'] += 1
                            found = True
                            break
                    if not found:
                        if is_res: zones.append({'upper': row['high'], 'lower': max(row['open'], row['close']), 'ref': row['high'], 'count': 1})
                        else: zones.append({'lower': row['low'], 'upper': min(row['open'], row['close']), 'ref': row['low'], 'count': 1})
                return [z for z in zones if z['count'] >= 2]

            self.resistance_zones[asset].extend(cluster(peaks, True))
            self.support_zones[asset].extend(cluster(troughs, False))
            self.last_snr_update[asset] = datetime.now()
        except Exception as e:
            logger.error(f"Failed to update SNR for {asset}: {e}")

    async def monitor_and_trade(self):
        """Background maintenance loop (SNR updates and Kill Switch checks)"""
        logger.info("Bot is running in event-driven mode.")
        while not self.kill_switch_active:
            try:
                # 1. Periodic SNR Refresh
                for asset in self.assets:
                    if datetime.now() - self.last_snr_update[asset] > timedelta(minutes=self.snr_update_interval):
                        await self.update_snr_zones(asset)

                # 2. Kill Switch Check
                if await self.check_kill_switch():
                    break

                await asyncio.sleep(30) # Maintenance check every 30s
            except Exception as e:
                logger.error(f"Error in background maintenance: {e}")
                await asyncio.sleep(5)

    async def execute_trade(self, asset: str, direction: OrderDirection):
        stake = await self.get_stake()
        logger.warning(f"PLACING TRADE: {asset} {direction.value} | Stake: ${stake}")

        try:
            order = await self.client.place_order(asset, stake, direction, 60)
            result = await self.client.check_win(order.order_id)
            if result:
                status = result.get('status')
                logger.success(f"TRADE COMPLETED: {asset} result is {status}")
                self.trades_taken_today += 1
                if status == 'win':
                    self.current_losses_streak = 0
                else:
                    self.current_losses_streak += 1
        except Exception as e:
            logger.error(f"Trade execution failed on {asset}: {e}")
        finally:
            # Ensure asset returns to IDLE after cooldown is handled in WS stream
            self.asset_states[asset] = "IDLE"
