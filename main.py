import asyncio
import json
import os
from loguru import logger
from trusted_spots_bot import TrustedSpotsBot

async def run_bot():
    config_path = "config.json"

    if not os.path.exists(config_path):
        logger.error(f"Configuration file {config_path} not found.")
        return

    with open(config_path, "r") as f:
        config = json.load(f)

    ssid = config.get("POCKET_OPTION_SSID")
    if not ssid or "your_session_id_here" in ssid:
        logger.error("Please update POCKET_OPTION_SSID in config.json")
        return

    bot = TrustedSpotsBot(config)

    try:
        await bot.initialize()
        await bot.monitor_and_trade()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
    finally:
        if bot.client.is_connected:
            await bot.client.disconnect()
            logger.info("Disconnected from PocketOption.")

if __name__ == "__main__":
    asyncio.run(run_bot())
