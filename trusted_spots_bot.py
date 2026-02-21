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
        self.asset = config.get("asset", "EURUSD_otc")
        self.is_demo = config.get("is_demo", True)

        self.client = AsyncPocketOptionClient(self.ssid, is_demo=self.is_demo)

        # Risk Management State
        rm_config = config.get("risk_management", {})
        self.start_balance = 0.0
        self.daily_target_pct = rm_config.get("daily_target_pct", 0.15)
        self.max_losses_streak = rm_config.get("max_losses_streak", 2)
        self.max_trades_per_day = rm_config.get("max_trades_per_day", 3)

        self.current_losses_streak = 0
        self.trades_taken_today = 0

        # Automation Params
        auto_config = config.get("automation", {})
        self.snr_update_interval = auto_config.get("snr_update_interval_mins", 15)
        self.rejection_window = auto_config.get("rejection_monitor_seconds", 30)

        # SNR Zones
        self.resistance_zones = []
        self.support_zones = []

        # Market Data
        self.latest_1m_candles = []
        self.latest_5s_candles = []

    async def initialize(self):
        await self.client.connect()
        balance_info = await self.client.get_balance()
        self.start_balance = balance_info.balance
        logger.info(f"Bot initialized. Start Balance: ${self.start_balance:.2f} on {self.asset}")

    async def get_stake(self) -> float:
        balance_info = await self.client.get_balance()
        balance = balance_info.balance
        if balance < 20.0:
            return 1.0
        return round(balance * 0.05, 2)

    async def check_kill_switch(self) -> bool:
        """Check if today's session should end ($10 to $10k Challenge Rules)"""
        balance_info = await self.client.get_balance()
        current_balance = balance_info.balance

        profit = current_balance - self.start_balance

        # 1. Daily Target (15%)
        if profit >= self.start_balance * self.daily_target_pct:
            logger.success(f"GOAL REACHED! Today's Profit: ${profit:.2f} (>= 15%). Kill Switch ON.")
            return True

        # 2. Daily Loss Limit (2 consecutive losses)
        if self.current_losses_streak >= self.max_losses_streak:
            logger.warning(f"STOP LOSS! {self.current_losses_streak} consecutive losses. Kill Switch ON.")
            return True

        # 3. Max Trade Limit (3 trades)
        if self.trades_taken_today >= self.max_trades_per_day:
            logger.info(f"SESSION COMPLETE! {self.trades_taken_today} trades taken. Kill Switch ON.")
            return True

        return False

    async def update_snr_zones(self):
        """Identify 'Trusted Spots' on 1m chart based on 5 criteria"""
        logger.info("Updating SNR zones...")
        # Get more candles to find 'Extreme' levels and 'Series of rejections'
        candles = await self.client.get_candles(self.asset, 60, count=100)
        if not candles:
            return

        self.resistance_zones = []
        self.support_zones = []

        df = pd.DataFrame([{
            'high': c.high, 'low': c.low,
            'open': c.open, 'close': c.close,
            'timestamp': c.timestamp
        } for c in candles])

        # 1. Extreme Levels (highest/lowest in 100 candles)
        max_high = df['high'].max()
        min_low = df['low'].min()

        # Find defining candles for extremes
        res_idx = df['high'].idxmax()
        sup_idx = df['low'].idxmin()

        # Resistance zone for max high
        self.resistance_zones.append({
            'upper': max_high,
            'lower': max(df['open'].iloc[res_idx], df['close'].iloc[res_idx]),
            'is_extreme': True
        })

        # Support zone for min low
        self.support_zones.append({
            'lower': min_low,
            'upper': min(df['open'].iloc[sup_idx], df['close'].iloc[sup_idx]),
            'is_extreme': True
        })

        # 2. Series of Rejections & Obviousness
        # Find pivots/peaks with at least 2 touches
        peaks = df[(df['high'] == df['high'].rolling(10, center=True).max())]
        troughs = df[(df['low'] == df['low'].rolling(10, center=True).min())]

        # Group close levels into zones
        def cluster_zones(levels, is_res=True):
            zones = []
            for idx, row in levels.iterrows():
                level_val = row['high'] if is_res else row['low']
                # Check if this level is near an existing zone
                found = False
                for zone in zones:
                    if abs(zone['ref_level'] - level_val) / level_val < 0.0005: # 0.05% tolerance
                        zone['touches'] += 1
                        found = True
                        break
                if not found:
                    if is_res:
                        zones.append({
                            'upper': row['high'],
                            'lower': max(row['open'], row['close']),
                            'ref_level': row['high'],
                            'touches': 1
                        })
                    else:
                        zones.append({
                            'lower': row['low'],
                            'upper': min(row['open'], row['close']),
                            'ref_level': row['low'],
                            'touches': 1
                        })
            return [z for z in zones if z['touches'] >= 2] # Require at least 2 touches for 'Trusted'

        self.resistance_zones.extend(cluster_zones(peaks, is_res=True))
        self.support_zones.extend(cluster_zones(troughs, is_res=False))

        logger.info(f"Updated: {len(self.resistance_zones)} Res Zones, {len(self.support_zones)} Sup Zones.")

    async def monitor_and_trade(self):
        """Main coordination loop for SNR detection and trade execution"""
        logger.info(f"Starting monitoring loop for {self.asset}...")

        while True:
            try:
                if await self.check_kill_switch():
                    logger.info("Session goals/limits met. Stopping bot.")
                    break

                # 1. Periodically refresh SNR zones
                now = datetime.now()
                if not self.resistance_zones or now.minute % self.snr_update_interval == 0:
                    await self.update_snr_zones()

                # 2. Monitor 1-minute price for zone entry
                # We use a fast candle check or tick data if available
                latest_candles = await self.client.get_candles(self.asset, 60, count=1)
                if not latest_candles:
                    await asyncio.sleep(1)
                    continue

                current_price = latest_candles[-1].close

                # 3. Detection & Execution
                target_direction = None

                # Check Resistance Zones
                for zone in self.resistance_zones:
                    if zone['lower'] <= current_price <= zone['upper']:
                        logger.info(f"TARGET DETECTED: Price in Resistance Zone {zone['lower']}-{zone['upper']}")
                        target_direction = OrderDirection.PUT
                        break

                # Check Support Zones (only if not already targeting resistance)
                if not target_direction:
                    for zone in self.support_zones:
                        if zone['lower'] <= current_price <= zone['upper']:
                            logger.info(f"TARGET DETECTED: Price in Support Zone {zone['lower']}-{zone['upper']}")
                            target_direction = OrderDirection.CALL
                            break

                if target_direction:
                    # 4. Multi-Timeframe Confirmation (5s)
                    confirmed = await self.confirm_5s_rejection(target_direction)
                    if confirmed:
                        # 5. Final Execution
                        await self.execute_trade(target_direction)
                        # Mandatory cooldown (wait for trade to finish + gap)
                        logger.info("Trade session cooling down...")
                        await asyncio.sleep(70)

                await asyncio.sleep(2) # Small delay between price checks

            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")
                await asyncio.sleep(5)

    async def confirm_5s_rejection(self, direction: OrderDirection) -> bool:
        """Monitor 5s candles for rejection patterns: Stall, Spike & Pull, or Reversal Candle"""
        logger.info(f"Monitoring 5s confirmation for {direction.value}...")
        start_time = time.time()

        while time.time() - start_time < self.rejection_window:
            candles_5s = await self.client.get_candles(self.asset, 5, count=4)
            if len(candles_5s) < 4:
                await asyncio.sleep(1)
                continue

            last = candles_5s[-1]
            prev = candles_5s[-2]

            body = abs(last.close - last.open)
            wick_top = last.high - max(last.open, last.close)
            wick_bottom = min(last.open, last.close) - last.low
            total_size = last.high - last.low if last.high > last.low else 0.0001

            # 1. Momentum Breakout Check (Invalidation)
            # If 2 consecutive 5s candles close strongly outside the zone boundary, it's a breakout.
            if direction == OrderDirection.PUT and last.close > last.open and body / total_size > 0.8:
                logger.info("5s High Momentum detected. Waiting for exhaustion.")
                # We don't immediately return False, we wait to see if it pulls back (The Trap)

            # 2. The Stall (2-3 small candles)
            if all(abs(c.close - c.open) < (c.high - c.low) * 0.4 for c in candles_5s[-3:]):
                logger.info("5s Stall detected at level.")
                return True

            # 3. Spike & Pull (Trap)
            # If current candle has a long wick in the direction of the level
            if direction == OrderDirection.PUT and wick_top > body * 1.5:
                logger.info("5s Upper Rejection Wick (Spike & Pull) detected.")
                return True
            if direction == OrderDirection.CALL and wick_bottom > body * 1.5:
                logger.info("5s Lower Rejection Wick (Spike & Pull) detected.")
                return True

            # 4. Fast Rejection (Opposite color candle)
            if direction == OrderDirection.PUT and last.close < last.open and prev.close > prev.open:
                 logger.info("5s Fast Rejection (Bearish Engulfing/Reversal) detected.")
                 return True
            if direction == OrderDirection.CALL and last.close > last.open and prev.close < prev.open:
                 logger.info("5s Fast Rejection (Bullish Engulfing/Reversal) detected.")
                 return True

            await asyncio.sleep(1)

        return False

    async def execute_trade(self, direction: OrderDirection):
        stake = await self.get_stake()
        logger.info(f"Placing {direction.value} order with stake ${stake}")

        try:
            order = await self.client.place_order(self.asset, stake, direction, 60)
            logger.info(f"Order placed: {order.order_id}")

            # Wait for result
            result = await self.client.check_win(order.order_id)
            if result:
                status = result.get('status')
                logger.info(f"Trade Result: {status}")

                self.trades_taken_today += 1
                if status == 'win':
                    self.current_losses_streak = 0
                else:
                    self.current_losses_streak += 1
        except Exception as e:
            logger.error(f"Failed to execute trade: {e}")
