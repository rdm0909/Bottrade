"""
bot.py -- PolyBot BTC 5-Min Hausse/Baisse
Bot de trading automatique pour les marches Polymarket BTC 5-minutes

Usage:
  python bot.py --dry-run --mode safe      # Simulation
  python bot.py --mode safe                # Live trading
  python bot.py --mode degen               # Tout ou rien
  python bot.py --dry-run --max-trades 20  # Limite
"""
import os
import sys
import time
import json
import requests
import argparse
from datetime import datetime
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

from strategy import (
    analyze,
    get_btc_candles,
    get_btc_price,
    estimate_token_price,
)

load_dotenv()

HOST = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CHAIN_ID = 137
PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")
FUNDER = os.getenv("POLY_FUNDER_ADDRESS", "")
SIG_TYPE = int(os.getenv("POLY_SIGNATURE_TYPE", "1"))
STARTING_BANKROLL = float(os.getenv("STARTING_BANKROLL", "10.0"))
MIN_BET = float(os.getenv("MIN_BET", "5.0"))
BOT_MODE = os.getenv("BOT_MODE", "safe")
MIN_CONFIDENCE = {"safe": 0.3, "aggressive": 0.2, "degen": 0.0}


def init_client():
    client = ClobClient(
        HOST,
        key=PRIVATE_KEY,
        chain_id=CHAIN_ID,
        signature_type=SIG_TYPE,
        funder=FUNDER,
    )
    try:
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        print("Client Polymarket initialise")
    except Exception as e:
        print("Credentials error: " + str(e))
    return client


def get_window_ts(ts=None):
    t = ts or int(time.time())
    return t - (t % 300)


def get_market(window_ts):
    slug = "btc-updown-5m-" + str(window_ts)
    url = GAMMA_API + "/events?slug=" + slug
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if not data:
            return None
        event = data[0]
        markets = event.get("markets", [])
        result = {"up_token": None, "down_token": None, "up_price": 0.5, "down_price": 0.5}
        for m in markets:
            outcome = m.get("outcomes", [""])[0].upper()
            token_ids = json.loads(m.get("clobTokenIds", "[]"))
            if "UP" in outcome and token_ids:
                result["up_token"] = token_ids[0]
                result["up_price"] = float(m.get("outcomePrices", ["0.5"])[0])
            elif "DOWN" in outcome and token_ids:
                result["down_token"] = token_ids[0]
                result["down_price"] = float(m.get("outcomePrices", ["0.5"])[0])
        return result
    except Exception as e:
        print("Market fetch error: " + str(e))
        return None


def calculate_bet(bankroll, mode):
    if mode == "safe":
        return max(MIN_BET, bankroll * 0.25)
    elif mode == "aggressive":
        profit = bankroll - STARTING_BANKROLL
        return max(MIN_BET, profit) if profit > MIN_BET else MIN_BET
    else:
        return max(MIN_BET, bankroll)


def place_trade(client, token_id, amount, dry_run):
    if dry_run:
        print("  [DRY RUN] Ordre simule: %.2f USDC sur token %s..." % (amount, token_id[:12]))
        return True
    try:
        order = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
            side=BUY,
            order_type=OrderType.FOK,
        )
        signed = client.create_market_order(order)
        resp = client.post_order(signed, OrderType.FOK)
        print("  Ordre place: " + str(resp))
        return True
    except Exception as e:
        print("  FOK echoue: " + str(e) + " -- Tentative Limit Order...")
        try:
            limit_order = OrderArgs(
                token_id=token_id,
                price=0.95,
                size=max(5, amount / 0.95),
                side=BUY,
            )
            signed = client.create_order(limit_order)
            resp = client.post_order(signed, OrderType.GTC)
            print("  Limit order place @ $0.95: " + str(resp))
            return True
        except Exception as e2:
            print("  Limit order echoue: " + str(e2))
            return False


def check_result(window_ts):
    """Verifie sur Binance si BTC a monte ou baisse."""
    close_ts = window_ts + 300
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={
                "symbol": "BTCUSDT",
                "interval": "1m",
                "startTime": window_ts * 1000,
                "endTime": (close_ts + 60) * 1000,
                "limit": 10,
            },
            timeout=10,
        )
        candles = resp.json()
        if not candles:
            return "UNKNOWN"
        open_price = float(candles[0][1])
        close_price = float(candles[-1][4])
        result = "UP" if close_price >= open_price else "DOWN"
        print("  Resolution: BTC %.2f -> %.2f = %s" % (open_price, close_price, result))
        return result
    except Exception as e:
        print("  Resolution Binance error: " + str(e))
        return "UNKNOWN"


def run_bot(dry_run=False, mode=None, max_trades=None, once=False):
    mode = mode or BOT_MODE
    bankroll = STARTING_BANKROLL
    trade_count = 0
    win_count = 0
    total_profit = 0.0
    ticks = []

    print("")
    print("=" * 44)
    print("  PolyBot BTC 5-Min -- " + ("DRY RUN" if dry_run else "LIVE TRADING"))
    print("  Mode: " + mode.upper() + "  Bankroll: $" + str(bankroll))
    print("=" * 44)
    print("")

    if not dry_run and (not PRIVATE_KEY or not FUNDER):
        print("POLY_PRIVATE_KEY et POLY_FUNDER_ADDRESS requis pour le mode live!")
        sys.exit(1)

    client = None if dry_run else init_client()

    while True:
        if max_trades and trade_count >= max_trades:
            print("Max trades atteint (%d). Arret." % max_trades)
            break

        if bankroll < MIN_BET:
            print("Bankroll (%.2f) < Min Bet (%.2f)." % (bankroll, MIN_BET))
            if mode == "degen":
                bankroll = STARTING_BANKROLL
                print("Bankroll reset a $%.2f" % bankroll)
            else:
                break

        now = int(time.time())
        window_ts = get_window_ts(now)
        close_time = window_ts + 300
        remaining = close_time - now

        ts_str = datetime.now().strftime("%H:%M:%S")
        print("[%s] Fenetre: %d | Fermeture dans: %ds" % (ts_str, window_ts, remaining))

        price = get_btc_price()
        if price:
            ticks.append(price)
            ticks = ticks[-60:]

        if remaining > 10:
            wait = remaining - 10
            print("  Attente %ds avant analyse..." % min(wait, 30))
            time.sleep(min(wait, 2))
            continue

        print("  ZONE DE SNIPE -- %ds restant" % remaining)

        candles = get_btc_candles(limit=30)
        if not candles:
            time.sleep(1)
            continue

        window_open = candles[0]["open"]
        for c in candles:
            if c["time"] <= window_ts * 1000:
                window_open = c["open"]

        current_price = price or candles[-1]["close"]
        best_signal = None
        best_score = 0.0

        while int(time.time()) < close_time - 5:
            price = get_btc_price()
            if price:
                ticks.append(price)
                ticks = ticks[-60:]
                current_price = price

            analysis = analyze(candles, ticks, window_open, current_price, verbose=False)
            print("  Score: %.2f | %s | Conf: %.0f%%" % (
                analysis["score"], analysis["direction"], analysis["confidence"]*100))

            if best_signal is None or abs(analysis["score"]) > abs(best_score):
                best_signal = analysis
                best_score = analysis["score"]

            min_conf = MIN_CONFIDENCE[mode]
            if analysis["confidence"] >= min_conf:
                print("  Confiance suffisante! On trade.")
                best_signal = analysis
                break

            remaining_now = close_time - int(time.time())
            if remaining_now <= 5:
                print("  T-5s: Trade force avec le meilleur signal")
                break

            time.sleep(2)

        if best_signal is None:
            print("  Aucun signal -- fenetre manquee")
            time.sleep(5)
            continue

        analyze(candles, ticks, window_open, current_price, verbose=True)

        bet_amount = calculate_bet(bankroll, mode)
        bet_amount = min(bet_amount, bankroll)

        market = get_market(window_ts)
        if not market or (not market["up_token"] and not market["down_token"]):
            print("  Marche Polymarket introuvable pour cette fenetre")
            time.sleep(5)
            continue

        direction = best_signal["direction"]
        token_id = market["up_token"] if direction == "UP" else market["down_token"]
        token_price_poly = market["up_price"] if direction == "UP" else market["down_price"]
        delta_pct = abs((current_price - window_open) / window_open * 100)
        token_price_est = estimate_token_price(delta_pct)

        print("")
        print("  TRADE: %s | Mise: $%.2f USDC" % (direction, bet_amount))
        print("  Prix token Poly: $%.3f | Estime: $%.3f" % (token_price_poly, token_price_est))

        if token_id:
            success = place_trade(client, token_id, bet_amount, dry_run)
        else:
            print("  Token ID manquant")
            success = False

        if success:
            bankroll -= bet_amount
            trade_count += 1
            print("  Bankroll restante: $%.2f" % bankroll)

            wait_for_close = close_time - int(time.time()) + 3
            if wait_for_close > 0:
                print("  Attente resolution (%ds)..." % wait_for_close)
                time.sleep(wait_for_close)

            result = check_result(window_ts)
            won = result == direction

            if result != "UNKNOWN":
                token_px = token_price_poly if token_price_poly > 0 else 0.5
                profit = bet_amount * (1 / token_px - 1) if won else -bet_amount
                bankroll += bet_amount + profit
                total_profit += profit
                if won:
                    win_count += 1
                    print("  WIN! Profit: +$%.2f | Bankroll: $%.2f" % (profit, bankroll))
                else:
                    print("  LOSS! Perte: $%.2f | Bankroll: $%.2f" % (profit, bankroll))

                win_rate = win_count / trade_count * 100 if trade_count > 0 else 0
                roi = (bankroll - STARTING_BANKROLL) / STARTING_BANKROLL * 100
                print("")
                print("  Stats: %d trades | WR: %.1f%% | P&L: $%.2f | ROI: %.1f%%" % (
                    trade_count, win_rate, total_profit, roi))

        if once:
            print("Mode --once: arret apres 1 trade.")
            break

        time.sleep(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PolyBot BTC 5-Min Trading Bot")
    parser.add_argument("--dry-run", action="store_true", help="Simulation sans trades reels")
    parser.add_argument("--mode", choices=["safe", "aggressive", "degen"], default=BOT_MODE)
    parser.add_argument("--max-trades", type=int, default=None)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    try:
        run_bot(
            dry_run=args.dry_run,
            mode=args.mode,
            max_trades=args.max_trades,
            once=args.once,
        )
    except KeyboardInterrupt:
        print("Bot arrete manuellement.")