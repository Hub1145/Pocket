import asyncio
import os
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict
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

        # Automation Params
        auto_config = config.get("automation", {})
        self.snr_update_interval = auto_config.get("snr_update_interval_mins", 15)
        self.rejection_window = auto_config.get("rejection_monitor_seconds", 30)

        # State
        self.resistance_zones: Dict[str, List[Dict]] = {asset: [] for asset in self.assets}
        self.support_zones: Dict[str, List[Dict]] = {asset: [] for asset in self.assets}
        self.last_snr_update: Dict[str, datetime] = {asset: datetime.min for asset in self.assets}
        self.asset_payouts: Dict[str, float] = {asset: 0.0 for asset in self.assets}

    async def initialize(self):
        # Register payout handler
        self.client.add_event_callback("payout_update", self._on_payout_update)

        await self.client.connect()
        balance_info = await self.client.get_balance()
        self.start_balance = balance_info.balance
        logger.info(f"Bot initialized. Start Balance: ${self.start_balance:.2f} on {len(self.assets)} assets.")

    def _on_payout_update(self, data: Dict):
        symbol = data.get("symbol")
        payout = data.get("payout", 0) / 100.0 # Convert to decimal (e.g. 92 -> 0.92)
        if symbol in self.asset_payouts:
            self.asset_payouts[symbol] = payout
            # logger.debug(f"Updated payout for {symbol}: {payout:.0%}")

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
            logger.success(f"GOAL REACHED! Profit: ${profit:.2f}. Kill Switch ON.")
            return True
        if self.current_losses_streak >= self.max_losses_streak:
            logger.warning(f"STOP LOSS! {self.current_losses_streak} losses. Kill Switch ON.")
            return True
        if self.trades_taken_today >= self.max_trades_per_day:
            logger.info(f"MAX TRADES ({self.trades_taken_today}) reached. Kill Switch ON.")
            return True
        return False

    async def update_snr_zones(self, asset: str):
        """Identify 'Trusted Spots' on 1m chart based on 5 criteria"""
        logger.info(f"Updating SNR zones for {asset}...")
        candles = await self.client.get_candles(asset, 60, count=100)
        if not candles:
            return

        self.resistance_zones[asset] = []
        self.support_zones[asset] = []

        df = pd.DataFrame([{
            'high': c.high, 'low': c.low,
            'open': c.open, 'close': c.close,
            'timestamp': c.timestamp
        } for c in candles])

        # 1. Extreme Levels
        max_high = df['high'].max()
        min_low = df['low'].min()
        res_idx = df['high'].idxmax()
        sup_idx = df['low'].idxmin()

        self.resistance_zones[asset].append({
            'upper': max_high,
            'lower': max(df['open'].iloc[res_idx], df['close'].iloc[res_idx]),
            'is_extreme': True
        })
        self.support_zones[asset].append({
            'lower': min_low,
            'upper': min(df['open'].iloc[sup_idx], df['close'].iloc[sup_idx]),
            'is_extreme': True
        })

        # 2. Series of Rejections
        peaks = df[(df['high'] == df['high'].rolling(10, center=True).max())]
        troughs = df[(df['low'] == df['low'].rolling(10, center=True).min())]

        def cluster_zones(levels, is_res=True):
            zones = []
            for idx, row in levels.iterrows():
                level_val = row['high'] if is_res else row['low']
                found = False
                for zone in zones:
                    if abs(zone['ref_level'] - level_val) / level_val < 0.0005:
                        zone['touches'] += 1
                        found = True
                        break
                if not found:
                    if is_res:
                        zones.append({'upper': row['high'], 'lower': max(row['open'], row['close']), 'ref_level': row['high'], 'touches': 1})
                    else:
                        zones.append({'lower': row['low'], 'upper': min(row['open'], row['close']), 'ref_level': row['low'], 'touches': 1})
            return [z for z in zones if z['touches'] >= 2]

        self.resistance_zones[asset].extend(cluster_zones(peaks, is_res=True))
        self.support_zones[asset].extend(cluster_zones(troughs, is_res=False))
        self.last_snr_update[asset] = datetime.now()

    async def monitor_and_trade(self):
        logger.info(f"Monitoring {len(self.assets)} assets with min {self.min_payout:.0%} payout...")

        while True:
            try:
                if await self.check_kill_switch():
                    break

                for asset in self.assets:
                    # 1. Payout Check
                    payout = self.asset_payouts.get(asset, 0)
                    if payout < self.min_payout:
                        # logger.debug(f"Skipping {asset}: Payout {payout:.0%} too low.")
                        continue

                    # 2. SNR Update Check
                    if datetime.now() - self.last_snr_update[asset] > timedelta(minutes=self.snr_update_interval):
                        await self.update_snr_zones(asset)

                    # 3. Price Entry Detection
                    latest_candles = await self.client.get_candles(asset, 60, count=1)
                    if not latest_candles:
                        continue
                    current_price = latest_candles[-1].close

                    target_direction = None
                    for zone in self.resistance_zones[asset]:
                        if zone['lower'] <= current_price <= zone['upper']:
                            logger.info(f"[{asset}] Resistance Zone Touch @ {current_price}")
                            target_direction = OrderDirection.PUT
                            break

                    if not target_direction:
                        for zone in self.support_zones[asset]:
                            if zone['lower'] <= current_price <= zone['upper']:
                                logger.info(f"[{asset}] Support Zone Touch @ {current_price}")
                                target_direction = OrderDirection.CALL
                                break

                    if target_direction:
                        # 4. Multi-Timeframe Confirmation (5s)
                        if await self.confirm_5s_rejection(asset, target_direction):
                            await self.execute_trade(asset, target_direction)
                            logger.info("Session cooling down...")
                            await asyncio.sleep(70)
                            break # Re-evaluate balance and kill switch

                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(5)

    async def confirm_5s_rejection(self, asset: str, direction: OrderDirection) -> bool:
        logger.info(f"Waiting for 5s rejection on {asset}...")
        start_time = time.time()

        while time.time() - start_time < self.rejection_window:
            candles_5s = await self.client.get_candles(asset, 5, count=4)
            if len(candles_5s) < 4:
                await asyncio.sleep(1)
                continue

            last = candles_5s[-1]
            prev = candles_5s[-2]
            body = abs(last.close - last.open)
            wick_top = last.high - max(last.open, last.close)
            wick_bottom = min(last.open, last.close) - last.low
            total_size = last.high - last.low if last.high > last.low else 0.0001

            # Rejection: Opposite move or long wick
            if direction == OrderDirection.PUT:
                if (last.close < last.open and prev.close > prev.open) or (wick_top > body * 1.5):
                    logger.info(f"5s Rejection confirmed for {asset} PUT")
                    return True
            else: # CALL
                if (last.close > last.open and prev.close < prev.open) or (wick_bottom > body * 1.5):
                    logger.info(f"5s Rejection confirmed for {asset} CALL")
                    return True

            # Momentum Invalidation
            if body / total_size > 0.85 and ((direction == OrderDirection.PUT and last.close > last.open) or (direction == OrderDirection.CALL and last.close < last.open)):
                logger.info(f"{asset} strong momentum. Entry invalidated.")
                return False

            await asyncio.sleep(1)
        return False

    async def execute_trade(self, asset: str, direction: OrderDirection):
        stake = await self.get_stake()
        logger.info(f"EXECUTE: {direction.value} on {asset} | Stake: ${stake}")

        try:
            order = await self.client.place_order(asset, stake, direction, 60)
            result = await self.client.check_win(order.order_id)
            if result:
                status = result.get('status')
                logger.success(f"{asset} RESULT: {status}")
                self.trades_taken_today += 1
                if status == 'win':
                    self.current_losses_streak = 0
                else:
                    self.current_losses_streak += 1
        except Exception as e:
            logger.error(f"Trade failed on {asset}: {e}")
