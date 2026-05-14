"""
strategy.py -- Analyse technique composite (7 indicateurs)
Bot Polymarket BTC 5-min Hausse/Baisse
"""
import requests
import time
from typing import Optional


BINANCE_API = "https://api.binance.com"


def get_btc_candles(interval="1m", limit=30):
    """Recupere les bougies BTC depuis Binance."""
    try:
        resp = requests.get(
            BINANCE_API + "/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": interval, "limit": limit},
            timeout=5,
        )
        data = resp.json()
        return [
            {
                "time": c[0],
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
            }
            for c in data
        ]
    except Exception as e:
        print("Binance candles error: " + str(e))
        return []


def get_btc_price():
    """Prix BTC actuel."""
    try:
        resp = requests.get(
            BINANCE_API + "/api/v3/ticker/price",
            params={"symbol": "BTCUSDT"},
            timeout=3,
        )
        return float(resp.json()["price"])
    except Exception as e:
        print("Binance price error: " + str(e))
        return None


def compute_ema(values, period):
    k = 2 / (period + 1)
    emas = [values[0]]
    for v in values[1:]:
        emas.append(v * k + emas[-1] * (1 - k))
    return emas


def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = 0.0, 0.0
    for i in range(len(closes) - period, len(closes)):
        d = closes[i] - closes[i - 1]
        if d > 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - 100 / (1 + rs)


def estimate_token_price(delta_pct):
    """Prix estime du token gagnant base sur le delta BTC."""
    abs_delta = abs(delta_pct)
    if abs_delta < 0.005:
        return 0.50
    elif abs_delta < 0.02:
        return 0.50 + (abs_delta - 0.005) / (0.02 - 0.005) * 0.05
    elif abs_delta < 0.05:
        return 0.55 + (abs_delta - 0.02) / (0.05 - 0.02) * 0.10
    elif abs_delta < 0.10:
        return 0.65 + (abs_delta - 0.05) / (0.10 - 0.05) * 0.15
    elif abs_delta < 0.15:
        return 0.80 + (abs_delta - 0.10) / (0.15 - 0.10) * 0.12
    else:
        return min(0.97, 0.92 + (abs_delta - 0.15) * 0.5)


def analyze(candles, ticks, window_open, current_price, verbose=True):
    """
    Analyse composite -- retourne:
    {
        'score': float,        # positif = UP, negatif = DOWN
        'confidence': float,   # 0-1
        'direction': 'UP'|'DOWN',
        'indicators': list[dict]
    }
    """
    indicators = []
    total_score = 0.0

    # 1. Window Delta (poids 5-7)
    window_pct = (current_price - window_open) / window_open * 100
    if abs(window_pct) > 0.10:
        wd_weight = 7
    elif abs(window_pct) > 0.02:
        wd_weight = 5
    elif abs(window_pct) > 0.005:
        wd_weight = 3
    else:
        wd_weight = 1
    wd_signal = 1 if window_pct > 0 else -1
    wd_contrib = wd_signal * wd_weight
    total_score += wd_contrib
    indicators.append({
        "name": "Window Delta",
        "value": ("+%.4f%%" if window_pct >= 0 else "%.4f%%") % window_pct,
        "weight": wd_weight,
        "contribution": wd_contrib,
    })

    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]

    # 2. Micro Momentum (poids 2)
    if len(candles) >= 2:
        last = candles[-1]
        prev = candles[-2]
        mom_dir = 1 if last["close"] > last["open"] else -1
        mom_dir2 = 1 if prev["close"] > prev["open"] else -1
        mom_signal = mom_dir if mom_dir == mom_dir2 else 0
        mom_contrib = mom_signal * 2
        total_score += mom_contrib
        indicators.append({
            "name": "Micro Momentum",
            "value": "Bullish" if mom_signal > 0 else ("Bearish" if mom_signal < 0 else "Mixed"),
            "weight": 2,
            "contribution": mom_contrib,
        })

    # 3. Acceleration (poids 1.5)
    if len(candles) >= 3:
        last_move = candles[-1]["close"] - candles[-1]["open"]
        prev_move = candles[-2]["close"] - candles[-2]["open"]
        accel = 1 if abs(last_move) > abs(prev_move) else -0.5
        accel_dir = 1 if last_move > 0 else (-1 if last_move < 0 else 0)
        accel_contrib = accel_dir * accel * 1.5
        total_score += accel_contrib
        indicators.append({
            "name": "Acceleration",
            "value": "Accelerant" if accel > 0 else "Decelerant",
            "weight": 1.5,
            "contribution": accel_contrib,
        })

    # 4. EMA 9/21 (poids 1)
    if len(closes) >= 21:
        ema9 = compute_ema(closes, 9)
        ema21 = compute_ema(closes, 21)
        ema_signal = 1 if ema9[-1] > ema21[-1] else -1
        total_score += ema_signal
        indicators.append({
            "name": "EMA 9/21",
            "value": "EMA9=%.2f vs EMA21=%.2f" % (ema9[-1], ema21[-1]),
            "weight": 1,
            "contribution": ema_signal,
        })

    # 5. RSI 14 (poids 1-2)
    rsi = compute_rsi(closes)
    rsi_contrib = 0
    if rsi > 75:
        rsi_contrib = -2
    elif rsi < 25:
        rsi_contrib = 2
    total_score += rsi_contrib
    indicators.append({
        "name": "RSI 14",
        "value": "%.1f" % rsi,
        "weight": 2 if abs(rsi_contrib) > 0 else 0,
        "contribution": rsi_contrib,
    })

    # 6. Volume Surge (poids 1)
    if len(volumes) >= 6:
        recent3 = sum(volumes[-3:]) / 3
        prior3 = sum(volumes[-6:-3]) / 3
        surge = recent3 > prior3 * 1.5
        vol_dir = 1 if candles[-1]["close"] > candles[-1]["open"] else -1
        vol_contrib = vol_dir if surge else 0
        total_score += vol_contrib
        indicators.append({
            "name": "Volume Surge",
            "value": ("+%.0f%%" % ((recent3/prior3-1)*100)) if surge else "Normal",
            "weight": 1,
            "contribution": vol_contrib,
        })

    # 7. Real-Time Tick Trend (poids 2)
    if len(ticks) >= 5:
        recent_ticks = ticks[-10:]
        up_ticks = sum(1 for i in range(1, len(recent_ticks)) if recent_ticks[i] > recent_ticks[i-1])
        down_ticks = sum(1 for i in range(1, len(recent_ticks)) if recent_ticks[i] < recent_ticks[i-1])
        total_ticks = up_ticks + down_ticks
        if total_ticks > 0:
            tick_dir = 1 if up_ticks > down_ticks else -1
            consistency = max(up_ticks, down_ticks) / total_ticks
            tick_move_pct = abs((recent_ticks[-1] - recent_ticks[0]) / recent_ticks[0]) * 100
            tick_contrib = tick_dir * 2 if (consistency >= 0.6 and tick_move_pct > 0.005) else 0
            total_score += tick_contrib
            indicators.append({
                "name": "Tick Trend",
                "value": "%.0f%% %s" % (consistency*100, "up" if tick_dir > 0 else "down"),
                "weight": 2,
                "contribution": tick_contrib,
            })

    confidence = min(abs(total_score) / 7.0, 1.0)
    direction = "UP" if total_score >= 0 else "DOWN"

    if verbose:
        print("=" * 50)
        print("Signal: %s | Score: %.2f | Confiance: %.1f%%" % (direction, total_score, confidence*100))
        for ind in indicators:
            sign = "+" if ind["contribution"] >= 0 else ""
            print("  %-20s %-25s -> %s%.2f" % (ind["name"], ind["value"], sign, ind["contribution"]))
        print("=" * 50)

    return {
        "score": total_score,
        "confidence": confidence,
        "direction": direction,
        "indicators": indicators,
    }