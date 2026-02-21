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
        self.start_balance = 0.0
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

        # Connect to PO
        connected = await self.client.connect()
        if not connected:
            raise ConnectionError("Failed to connect to PocketOption.")

        # Initial Balance
        balance_info = await self.client.get_balance()
        self.start_balance = balance_info.balance
        logger.info(f"Bot initialized. Start Balance: ${self.start_balance:.2f}")

        # Initial SNR Mappings and Subscriptions
        for asset in self.assets:
            await self.update_snr_zones(asset)
            # Subscribe to 5s stream for high-resolution tick tracking
            # Note: We use the internal changeSymbol logic
            msg = f'42["changeSymbol", {{"asset": "{asset}", "period": 5}}]'
            await self.client.send_message(msg)
            logger.info(f"Subscribed to 5s stream for {asset}")
            await asyncio.sleep(0.5) # Avoid spamming the server

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

        # Period check (we subscribed to 5s)
        # PocketOption stream updates can contain multiple candles or a single tick
        candles_data = data.get("data") or data.get("candles") or []
        if not candles_data:
            return

        # Get latest tick/candle from the stream
        # Format can be [time, open, close, high, low] or dict
        last_item = candles_data[-1]
        current_price = 0.0
        if isinstance(last_item, dict):
            current_price = float(last_item.get("close", 0))
        elif isinstance(last_item, (list, tuple)) and len(last_item) >= 3:
            current_price = float(last_item[2]) # Close is at index 2

        self.asset_last_price[asset] = current_price

        # Logic Flow:
        # 1. Payout Check
        if self.asset_payouts.get(asset, 0) < self.min_payout:
            return

        # 2. Cooldown Check
        if time.time() < self.cooldown_until[asset]:
            return

        # 3. State Management
        state = self.asset_states[asset]

        if state == "IDLE":
            # Look for SNR Zone Touch
            target_dir = self._check_zone_touch(asset, current_price)
            if target_dir:
                logger.info(f"[{asset}] Zone Touch Detected. Price: {current_price}. Entering TOUCHING state.")
                self.asset_states[asset] = "TOUCHING"
                self.touch_start_time[asset] = time.time()
                # Store the direction we are looking for (CALL at support, PUT at resistance)
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

    def _check_zone_touch(self, asset: str, price: float) -> Optional[OrderDirection]:
        # Check Resistance
        for zone in self.resistance_zones[asset]:
            if zone['lower'] <= price <= zone['upper']:
                return OrderDirection.PUT
        # Check Support
        for zone in self.support_zones[asset]:
            if zone['lower'] <= price <= zone['upper']:
                return OrderDirection.CALL
        return None

    async def _check_rejection_confirmed(self, asset: str, stream_data: Dict, direction: OrderDirection) -> bool:
        """Analyze the stream data for a rejection pattern"""
        # stream_data contains the latest 5s candles
        candles = self.client._parse_stream_candles(stream_data, asset, 5)
        if len(candles) < 2:
            return False

        last = candles[-1]
        prev = candles[-2]
        body = abs(last.close - last.open)
        wick_top = last.high - max(last.open, last.close)
        wick_bottom = min(last.open, last.close) - last.low
        total_size = last.high - last.low if last.high > last.low else 0.0001

        # 1. Opposite Move (Fast Rejection)
        if direction == OrderDirection.PUT and last.close < last.open and prev.close > prev.open:
            logger.info(f"[{asset}] 5s Bearish Rejection confirmed.")
            return True
        if direction == OrderDirection.CALL and last.close > last.open and prev.close < prev.open:
            logger.info(f"[{asset}] 5s Bullish Rejection confirmed.")
            return True

        # 2. Long Wick (Spike & Pull)
        if direction == OrderDirection.PUT and wick_top > body * 1.5:
            logger.info(f"[{asset}] 5s Upper Wick Rejection confirmed.")
            return True
        if direction == OrderDirection.CALL and wick_bottom > body * 1.5:
            logger.info(f"[{asset}] 5s Lower Wick Rejection confirmed.")
            return True

        # 3. Momentum Check (Invalidation)
        if body / total_size > 0.9 and ((direction == OrderDirection.PUT and last.close > last.open) or (direction == OrderDirection.CALL and last.close < last.open)):
            logger.info(f"[{asset}] Strong momentum detected. Setup invalidated.")
            self.asset_states[asset] = "IDLE"
            return False

        return False

    async def get_stake(self) -> float:
        balance_info = await self.client.get_balance()
        balance = balance_info.balance
        if balance < 20.0:
            return 1.0
        return round(balance * 0.05, 2)

    async def check_kill_switch(self) -> bool:
        balance_info = await self.client.get_balance()
        current_balance = balance_info.balance
        profit = current_balance - self.start_balance

        if profit >= self.start_balance * self.daily_target_pct:
            logger.success(f"DAILY GOAL MET: +${profit:.2f}. Kill Switch ON.")
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
