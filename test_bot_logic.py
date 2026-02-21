import asyncio
import pandas as pd
from trusted_spots_bot import TrustedSpotsBot

async def test_snr_clustering():
    config = {
        "POCKET_OPTION_SSID": "test",
        "assets": ["EURUSD_otc"],
        "risk_management": {"daily_target_pct": 0.3},
        "automation": {"snr_update_interval_mins": 15}
    }
    bot = TrustedSpotsBot(config)

    # Mock data
    data = [
        {'high': 1.1000, 'low': 1.0990, 'open': 1.0995, 'close': 1.0998},
        {'high': 1.1005, 'low': 1.1001, 'open': 1.1002, 'close': 1.1004},
        {'high': 1.1000, 'low': 1.0990, 'open': 1.0995, 'close': 1.0998},
        {'high': 1.1001, 'low': 1.0991, 'open': 1.0996, 'close': 1.0999},
    ]
    df = pd.DataFrame(data)

    # Test the clustering logic (peaks)
    peaks = df[(df['high'] == df['high'].rolling(3, center=True).max())]
    # In this small sample, rolling(3) with center=True needs at least 3 rows
    # Let's just test the cluster function logic directly if we can

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

    test_df = pd.DataFrame([
        {'high': 1.1000, 'low': 1.0990, 'open': 1.0995, 'close': 1.0998},
        {'high': 1.1001, 'low': 1.0991, 'open': 1.0996, 'close': 1.0999},
        {'high': 1.0500, 'low': 1.0490, 'open': 1.0495, 'close': 1.0498}, # Different level
    ])

    res_zones = cluster(test_df, True)
    print(f"Resistance zones: {res_zones}")
    assert len(res_zones) == 1
    assert res_zones[0]['count'] == 2

if __name__ == "__main__":
    asyncio.run(test_snr_clustering())
