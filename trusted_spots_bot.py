import asyncio
import os
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Set, Callable
import pandas as pd
from loguru import logger

from pocketoptionapi_async import AsyncPocketOptionClient, OrderDirection, OrderStatus
from pocketoptionapi_async.models import Candle

class TrustedSpotsBot:
    def __init__(self, config: Dict, update_callback: Optional[Callable] = None):
        self.config = config
        self.update_callback = update_callback
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
        self.stop_event = asyncio.Event()

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
        self.current_day = datetime.now().date()
        self.asset_states: Dict[str, str] = {asset: "IDLE" for asset in self.assets} # IDLE, TOUCHING, COOLING_DOWN
        self.touch_start_time: Dict[str, float] = {asset: 0.0 for asset in self.assets}
        self.asset_last_price: Dict[str, float] = {asset: 0.0 for asset in self.assets}
        self.cooldown_until: Dict[str, float] = {asset: 0.0 for asset in self.assets}
        self.open_positions: Dict[str, Dict] = {} # order_id: details

    def _report_update(self):
        if self.update_callback:
            data = {
                "balance": self.current_balance_cached,
                "day_start_balance": self.day_start_balance,
                "profit": self.current_balance_cached - self.day_start_balance if self.day_start_balance else 0,
                "trades_today": self.trades_taken_today,
                "loss_streak": self.current_losses_streak,
                "active": not self.kill_switch_active and not self.stop_event.is_set(),
                "assets": {}
            }
            for asset in self.assets:
                data["assets"][asset] = {
                    "price": self.asset_last_price.get(asset, 0),
                    "payout": self.asset_payouts.get(asset, 0),
                    "state": self.asset_states.get(asset, "IDLE"),
                    "resistance": self.resistance_zones.get(asset, []),
                    "support": self.support_zones.get(asset, [])
                }
            data["open_positions"] = list(self.open_positions.values())
            self.update_callback(data)

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
            msg = f'42["changeSymbol", {{"asset": "{asset}", "period": 5}}]'
            await self.client.send_message(msg)
            logger.info(f"Subscribed to 5s stream for {asset}")
            await asyncio.sleep(0.3)

    async def start_new_session(self):
        """Reset daily counters and capture start-of-day balance"""
        balance_info = await self.client.get_balance()
        self.day_start_balance = balance_info.balance
        self.current_balance_cached = balance_info.balance
        self.trades_taken_today = 0
        self.current_losses_streak = 0
        self.kill_switch_active = False
        self.stop_event.clear()
        logger.info(f"--- NEW SESSION STARTED ---")
        logger.info(f"Start Balance: ${self.day_start_balance:.2f} | Target: +{self.daily_target_pct:.0%}")
        self._report_update()

    def _on_balance_updated(self, balance_obj):
        self.current_balance_cached = balance_obj.balance
        self._report_update()

    def _on_payout_update(self, data: Dict):
        symbol = data.get("symbol")
        payout = data.get("payout", 0) / 100.0
        if symbol in self.asset_payouts:
            self.asset_payouts[symbol] = payout
            self._report_update()

    async def _on_stream_update(self, data: Dict):
        """Websocket Event Handler for real-time price ticks"""
        if self.kill_switch_active or self.stop_event.is_set():
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
                if await self._is_approach_aggressive(asset, target_dir):
                    self.cooldown_until[asset] = time.time() + 30
                    return

                logger.info(f"[{asset}] Zone Touch: {current_price}")
                self.asset_states[asset] = "TOUCHING"
                self.touch_start_time[asset] = time.time()
                self.config[f"{asset}_target_dir"] = target_dir
                self._report_update()

        elif state == "TOUCHING":
            if time.time() - self.touch_start_time[asset] > self.rejection_window:
                self.asset_states[asset] = "IDLE"
                self._report_update()
                return

            target_dir = self.config.get(f"{asset}_target_dir")
            if await self._check_rejection_confirmed(asset, data, target_dir):
                self.asset_states[asset] = "COOLING_DOWN"
                asyncio.create_task(self.execute_trade(asset, target_dir))
                self.cooldown_until[asset] = time.time() + 70
                self._report_update()

    async def _is_approach_aggressive(self, asset: str, direction: OrderDirection) -> bool:
        try:
            candles = await self.client.get_candles(asset, 60, count=2)
            if len(candles) < 2: return False
            last = candles[-1]
            body = abs(last.close - last.open)
            total_range = last.high - last.low if last.high > last.low else 0.0001
            if (direction == OrderDirection.PUT and last.close > last.open and body/total_range > 0.8) or \
               (direction == OrderDirection.CALL and last.close < last.open and body/total_range > 0.8):
                return True
            return False
        except: return False

    def _check_zone_touch(self, asset: str, price: float) -> Optional[OrderDirection]:
        for zone in self.resistance_zones[asset]:
            if zone['lower'] <= price <= zone['upper']: return OrderDirection.PUT
        for zone in self.support_zones[asset]:
            if zone['lower'] <= price <= zone['upper']: return OrderDirection.CALL
        return None

    async def _check_rejection_confirmed(self, asset: str, stream_data: Dict, direction: OrderDirection) -> bool:
        candles = self.client._parse_stream_candles(stream_data, asset, 5)
        if len(candles) < 2: return False
        last, prev = candles[-1], candles[-2]
        last_body, prev_body = abs(last.close - last.open), abs(prev.close - prev.open)
        last_range = last.high - last.low if last.high > last.low else 0.0001
        wick_top, wick_bottom = last.high - max(last.open, last.close), min(last.open, last.close) - last.low

        if direction == OrderDirection.PUT and last.close < last.open and prev.close > prev.open and last_body > prev_body * 0.7: return True
        if direction == OrderDirection.CALL and last.close > last.open and prev.close < prev.open and last_body > prev_body * 0.7: return True
        if direction == OrderDirection.PUT and wick_top > last_body * 1.8: return True
        if direction == OrderDirection.CALL and wick_bottom > last_body * 1.8: return True
        if last_body < last_range * 0.1: return True
        return False

    async def get_stake(self) -> float:
        balance = self.current_balance_cached
        if balance < 20.0: return 1.0
        return max(1.0, round(balance * 0.05, 2))

    async def check_kill_switch(self) -> bool:
        profit = self.current_balance_cached - self.day_start_balance
        if profit >= self.day_start_balance * self.daily_target_pct:
            logger.success(f"GOAL MET: +${profit:.2f}. Kill Switch ON.")
            self.kill_switch_active = True
            self._report_update()
            return True
        if self.current_losses_streak >= self.max_losses_streak:
            logger.warning(f"LOSS LIMIT: {self.current_losses_streak} losses. Kill Switch ON.")
            self.kill_switch_active = True
            self._report_update()
            return True
        if self.trades_taken_today >= self.max_trades_per_day:
            logger.info(f"TRADE LIMIT: {self.trades_taken_today} trades. Kill Switch ON.")
            self.kill_switch_active = True
            self._report_update()
            return True
        return False

    async def update_snr_zones(self, asset: str):
        try:
            candles = await self.client.get_candles(asset, 60, count=100)
            if not candles: return
            self.resistance_zones[asset], self.support_zones[asset] = [], []
            df = pd.DataFrame([{'high': c.high, 'low': c.low, 'open': c.open, 'close': c.close} for c in candles])
            res_idx, sup_idx = df['high'].idxmax(), df['low'].idxmin()
            self.resistance_zones[asset].append({'upper': df['high'].iloc[res_idx], 'lower': max(df['open'].iloc[res_idx], df['close'].iloc[res_idx])})
            self.support_zones[asset].append({'lower': df['low'].iloc[sup_idx], 'upper': min(df['open'].iloc[sup_idx], df['close'].iloc[sup_idx])})
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
            self._report_update()
        except: pass

    async def monitor_and_trade(self):
        logger.info("Bot logic active.")
        while not self.stop_event.is_set():
            try:
                # Daily Reset Check
                if datetime.now().date() > self.current_day:
                    logger.info("New day detected. Resetting session...")
                    await self.start_new_session()
                    self.current_day = datetime.now().date()

                if self.kill_switch_active:
                    await asyncio.sleep(60)
                    continue

                for asset in self.assets:
                    if datetime.now() - self.last_snr_update[asset] > timedelta(minutes=self.snr_update_interval):
                        await self.update_snr_zones(asset)
                if await self.check_kill_switch(): break
                await asyncio.sleep(10)
                self._report_update()
            except Exception as e:
                logger.error(f"Maintenance error: {e}")
                await asyncio.sleep(5)
        logger.info("Bot logic stopped.")
        self._report_update()

    async def execute_trade(self, asset: str, direction: OrderDirection):
        stake = await self.get_stake()
        try:
            order = await self.client.place_order(asset, stake, direction, 60)
            order_id = order.order_id
            self.open_positions[order_id] = {
                "order_id": order_id, "asset": asset, "direction": direction.value,
                "stake": stake, "open_time": datetime.now().strftime("%H:%M:%S")
            }
            self._report_update()
            result = await self.client.check_win(order_id)
            if result:
                status = result.get('status')
                self.trades_taken_today += 1
                if status == 'win': self.current_losses_streak = 0
                else: self.current_losses_streak += 1
            if order_id in self.open_positions: del self.open_positions[order_id]
            self._report_update()
        except Exception as e:
            logger.error(f"Trade failed: {e}")
            if 'order_id' in locals() and order_id in self.open_positions: del self.open_positions[order_id]
            self._report_update()

    async def stop(self):
        self.stop_event.set()
        if self.client.is_connected:
            await self.client.disconnect()
        logger.info("Bot disconnected.")
        self._report_update()
