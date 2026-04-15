"""
╔══════════════════════════════════════════════════════════════╗
║         FOREX BOT — SECURED PRODUCTION VERSION              ║
║                                                              ║
║  DEMO MODE : 7 days paper trading, no real money            ║
║  LIVE MODE : Set LIVE_MODE = True after demo validates       ║
║                                                              ║
║  Scoring (100 pts total):                                    ║
║    Technical  (EMA/RSI/MACD)  : 30 pts                      ║
║    Session Momentum           : 25 pts                       ║
║    Volume/Spread Filter       : 15 pts                       ║
║    News Sentiment             : 15 pts                       ║
║    Trend Strength (ADX)       : 15 pts                       ║
║                                                              ║
║  DEMO: Buy >= 55  |  LIVE: Buy >= 70                        ║
║  Sell: score < 30 OR stop loss OR trailing stop             ║
║                                                              ║
║  Run forex_setup_keys.py FIRST                              ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, time, csv, logging, requests, hmac, hashlib, json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from cryptography.fernet import Fernet
import pytz

try:
    import oandapyV20
    import oandapyV20.endpoints.instruments as instruments
    import oandapyV20.endpoints.orders as orders
    import oandapyV20.endpoints.positions as positions_ep
    import oandapyV20.endpoints.accounts as accounts_ep
    import oandapyV20.endpoints.trades as trades_ep
    from oandapyV20.exceptions import V20Error
except ImportError:
    print("Installing oandapyV20...")
    os.system("pip install oandapyV20")
    import oandapyV20
    import oandapyV20.endpoints.instruments as instruments
    import oandapyV20.endpoints.orders as orders
    import oandapyV20.endpoints.positions as positions_ep
    import oandapyV20.endpoints.accounts as accounts_ep
    import oandapyV20.endpoints.trades as trades_ep
    from oandapyV20.exceptions import V20Error

# ══════════════════════════════════════════════════════════════
#   MODE SWITCH — only line you ever need to change
# ══════════════════════════════════════════════════════════════
LIVE_MODE  = False   # False = demo | True = real money
DEMO_DAYS  = 7
# ══════════════════════════════════════════════════════════════

# ── Risk settings ──────────────────────────────────────────────
RISK_PER_TRADE   = 0.01   # 1% per trade
MAX_POSITIONS    = 5      # max pairs at once
DAILY_LOSS_LIMIT = 0.03   # stop if down 3% today
HARD_STOP_LOSS   = 0.02   # 2% hard stop (tighter for forex)
TRAILING_AFTER   = 0.03   # trailing stop after +3%
TRAILING_STOP    = 0.015  # trail 1.5% from peak
UNITS_BASE       = 1000   # base trade size (1000 units)

# ── Scoring thresholds ─────────────────────────────────────────
BUY_THRESHOLD  = 55 if not LIVE_MODE else 70
SELL_THRESHOLD = 30 if not LIVE_MODE else 40

# ── Paths ──────────────────────────────────────────────────────
LOG_DIR = "./logs"
KEY_FILE  = os.path.join(LOG_DIR, ".forex_keys")
ENC_FILE  = os.path.join(LOG_DIR, ".forex_encrypted_config")
TRADE_LOG = os.path.join(LOG_DIR, "forex_trade_log.csv")
BOT_LOG   = os.path.join(LOG_DIR, "forex_bot.log")
DEMO_FILE = os.path.join(LOG_DIR, "forex_demo_start.txt")
os.makedirs(LOG_DIR, exist_ok=True)

# ── All Oanda tradeable pairs ──────────────────────────────────
ALL_PAIRS = [
    # Majors
    "EUR_USD","GBP_USD","USD_JPY","USD_CHF","AUD_USD",
    "USD_CAD","NZD_USD",
    # Minors
    "EUR_GBP","EUR_JPY","EUR_CHF","EUR_AUD","EUR_CAD",
    "GBP_JPY","GBP_CHF","GBP_AUD","GBP_CAD","GBP_NZD",
    "AUD_JPY","AUD_CAD","AUD_CHF","AUD_NZD",
    "CAD_JPY","CAD_CHF",
    "NZD_JPY","NZD_CAD","NZD_CHF",
    "CHF_JPY",
    # Exotics
    "EUR_NOK","EUR_SEK","EUR_DKK","EUR_PLN","EUR_HUF",
    "EUR_CZK","EUR_TRY","EUR_ZAR","EUR_SGD","EUR_HKD",
    "USD_NOK","USD_SEK","USD_DKK","USD_PLN","USD_HUF",
    "USD_CZK","USD_TRY","USD_ZAR","USD_SGD","USD_HKD",
    "USD_MXN","USD_THB","USD_CNH",
    "GBP_PLN","GBP_NOK","GBP_SEK","GBP_ZAR",
]

EST = pytz.timezone("US/Eastern")
UTC = pytz.UTC

# ══════════════════════════════════════════════════════════════
#   LOGGING
# ══════════════════════════════════════════════════════════════

logging.basicConfig(
    filename=BOT_LOG,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

def log(msg, level="info"):
    print(f"  {msg}")
    getattr(logging, level)(msg)

# ══════════════════════════════════════════════════════════════
#   SECURITY
# ══════════════════════════════════════════════════════════════
KEY_FILE = "/tmp/dummy"
ENC_FILE = "/tmp/dummy"

def load_keys():
    if not os.path.exists(KEY_FILE) or not os.path.exists(ENC_FILE):
        print("\n  ERROR: Keys not set up.")
        print("  Run: conda run -n tradingbot python forex_setup_keys.py\n")
        raise SystemExit(1)
    with open(KEY_FILE, "rb") as kf:
        fernet_key = kf.read()
    with open(ENC_FILE, "rb") as ef:
        encrypted = ef.read()
    f = Fernet(fernet_key)
    try:
        raw = f.decrypt(encrypted).decode()
    except Exception:
        print("\n  ERROR: Could not decrypt keys. Re-run forex_setup_keys.py\n")
        raise SystemExit(1)
    parts = raw.split("|||")
    if len(parts) != 3:
        print("\n  ERROR: Corrupted key file. Re-run forex_setup_keys.py\n")
        raise SystemExit(1)
    return parts[0], parts[1], parts[2]

def _get_hmac_secret():
    with open(KEY_FILE, "rb") as f:
        return hashlib.sha256(f.read()).digest()

def sign_score(pair, score_dict):
    secret  = _get_hmac_secret()
    payload = json.dumps({"pair": pair, "scores": score_dict},
                         sort_keys=True).encode()
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()

def verify_score(pair, score_dict, signature):
    return hmac.compare_digest(sign_score(pair, score_dict), signature)

def hash_trade(record):
    payload = json.dumps({k: v for k, v in record.items()
                          if k != "integrity"}, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()[:16]

def mask_key(key):
    if not key or len(key) < 8:
        return "****"
    return key[:4] + "*" * (len(key) - 8) + key[-4:]

# ── Load keys ──────────────────────────────────────────────────
OANDA_TOKEN = os.getenv("OANDA_TOKEN")
OANDA_ACCOUNT = os.getenv("OANDA_ACCOUNT")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")

# ── Oanda client ───────────────────────────────────────────────
OANDA_ENV = "practice" if not LIVE_MODE else "live"
client    = oandapyV20.API(access_token=OANDA_TOKEN, environment=OANDA_ENV)

# ══════════════════════════════════════════════════════════════
#   DEMO TRACKER
# ══════════════════════════════════════════════════════════════

def get_demo_day():
    if not os.path.exists(DEMO_FILE):
        with open(DEMO_FILE, "w") as f:
            f.write(datetime.now().isoformat())
        return 1
    with open(DEMO_FILE) as f:
        start = datetime.fromisoformat(f.read().strip())
    return min((datetime.now() - start).days + 1, DEMO_DAYS)

def demo_complete():
    return get_demo_day() >= DEMO_DAYS

# ══════════════════════════════════════════════════════════════
#   SESSION CHECK — only trade during best hours
# ══════════════════════════════════════════════════════════════

def get_active_session():
    """
    Returns session name and quality score.
    Best time = London/NY overlap (8am-12pm EST)
    """
    now  = datetime.now(EST)
    hour = now.hour

    if 8 <= hour < 12:
        return "London/NY Overlap", 25   # best liquidity
    elif 3 <= hour < 8:
        return "London Open", 18
    elif 12 <= hour < 17:
        return "NY Session", 15
    elif 18 <= hour < 22:
        return "Tokyo Open", 8
    else:
        return "Off Hours", 0

def is_good_trading_time():
    _, quality = get_active_session()
    return quality >= 8   # skip dead hours

# ══════════════════════════════════════════════════════════════
#   ACCOUNT HELPERS
# ══════════════════════════════════════════════════════════════

def get_account_balance():
    try:
        r    = accounts_ep.AccountSummary(OANDA_ACCOUNT)
        data = client.request(r)
        return float(data["account"]["balance"])
    except Exception as e:
        log(f"Balance error: {e}", "error")
        return 0

def get_open_trades():
    try:
        r    = trades_ep.TradesList(OANDA_ACCOUNT)
        data = client.request(r)
        return {t["instrument"]: t for t in data.get("trades", [])}
    except Exception as e:
        log(f"Trades error: {e}", "error")
        return {}

def get_nav():
    """Net asset value — portfolio value including open positions"""
    try:
        r    = accounts_ep.AccountSummary(OANDA_ACCOUNT)
        data = client.request(r)
        return float(data["account"]["NAV"])
    except Exception:
        return get_account_balance()

# ══════════════════════════════════════════════════════════════
#   MARKET DATA
# ══════════════════════════════════════════════════════════════

def get_candles(pair, granularity="H1", count=100):
    """Fetch OHLCV candles from Oanda"""
    try:
        params = {"granularity": granularity, "count": count}
        r      = instruments.InstrumentsCandles(pair, params=params)
        data   = client.request(r)
        candles = data["candles"]

        rows = []
        for c in candles:
            if not c["complete"]:
                continue
            rows.append({
                "time":   c["time"],
                "open":   float(c["mid"]["o"]),
                "high":   float(c["mid"]["h"]),
                "low":    float(c["mid"]["l"]),
                "close":  float(c["mid"]["c"]),
                "volume": int(c["volume"]),
            })

        df = pd.DataFrame(rows)
        if not df.empty:
            df["time"] = pd.to_datetime(df["time"])
            df.set_index("time", inplace=True)
        return df

    except Exception:
        return pd.DataFrame()

def get_current_price(pair):
    try:
        params = {"granularity": "M1", "count": 2}
        r      = instruments.InstrumentsCandles(pair, params=params)
        data   = client.request(r)
        last   = data["candles"][-1]["mid"]
        bid    = float(last["c"])
        return bid
    except Exception:
        return None

def get_spread(pair):
    """Get current bid/ask spread — avoid high spread pairs"""
    try:
        params = {"granularity": "S5", "count": 1}
        r      = instruments.InstrumentsCandles(pair, params=params)
        data   = client.request(r)
        c      = data["candles"][-1]
        ask    = float(c["ask"]["c"]) if "ask" in c else float(c["mid"]["c"])
        bid    = float(c["bid"]["c"]) if "bid" in c else float(c["mid"]["c"])
        return round(ask - bid, 5)
    except Exception:
        return 999   # return high spread on error to skip pair

# ══════════════════════════════════════════════════════════════
#   SCORING LAYERS
# ══════════════════════════════════════════════════════════════

def score_technical(pair):
    """
    EMA 9/21 crossover + RSI + MACD = 30 pts
    Uses H1 candles (1 hour) for signal generation
    """
    score = 0
    try:
        df = get_candles(pair, "H1", 100)
        if len(df) < 30:
            return 0, "neutral"

        close = df["close"]

        # EMA 9/21
        ema9  = close.ewm(span=9,  adjust=False).mean()
        ema21 = close.ewm(span=21, adjust=False).mean()

        # RSI
        delta  = close.diff()
        gain   = delta.where(delta > 0, 0).rolling(14).mean()
        loss   = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs     = gain / loss
        rsi    = 100 - (100 / (1 + rs))

        # MACD
        ema12  = close.ewm(span=12, adjust=False).mean()
        ema26  = close.ewm(span=26, adjust=False).mean()
        macd   = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()

        last  = df.index[-1]
        prev  = df.index[-2]

        # EMA trend (10 pts)
        if ema9.iloc[-1] > ema21.iloc[-1]:   score += 7
        if close.iloc[-1] > ema9.iloc[-1]:   score += 3

        # RSI zone (10 pts)
        r = rsi.iloc[-1]
        if   50 < r < 65:  score += 10
        elif 40 < r <= 50: score += 6
        elif 65 <= r < 75: score += 4
        elif r >= 75:      score += 1

        # MACD crossover (10 pts)
        if macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-2] <= signal.iloc[-2]:
            score += 10   # fresh bullish crossover
        elif macd.iloc[-1] > signal.iloc[-1]:
            score += 5

        direction = "buy" if ema9.iloc[-1] > ema21.iloc[-1] else "sell"

    except Exception as e:
        log(f"Technical score error {pair}: {e}", "warning")
        direction = "neutral"

    return min(score, 30), direction

def score_session(pair):
    """25 pts based on trading session quality"""
    session_name, quality = get_active_session()

    # Major pairs score higher in good sessions
    majors = {"EUR_USD","GBP_USD","USD_JPY","USD_CHF","AUD_USD","USD_CAD","NZD_USD"}
    is_major = pair in majors

    if is_major:
        return min(quality + 3, 25), session_name
    return quality, session_name

def score_spread(pair):
    """
    15 pts — reward tight spreads, penalize wide ones.
    Wide spreads = broker taking too much = bad trade.
    """
    try:
        spread = get_spread(pair)
        # Typical spreads: EUR_USD ~0.00010, exotics ~0.00100+
        if   spread <= 0.00015: return 15
        elif spread <= 0.00030: return 10
        elif spread <= 0.00060: return 5
        elif spread <= 0.00100: return 2
        return 0   # too wide — skip
    except Exception:
        return 5

def score_news_forex(pair):
    """
    15 pts — currency-specific news sentiment.
    Maps pair to base/quote currencies for targeted search.
    """
    try:
        if NEWS_API_KEY in ("NOT_SET", "YOUR_NEWSAPI_KEY_HERE", ""):
            return 8

        # Extract currencies from pair (EUR_USD → EUR, USD)
        base, quote = pair.split("_")

        # Search for news on both currencies
        query = f"{base} {quote} forex currency"
        url   = (f"https://newsapi.org/v2/everything?"
                 f"q={query}&language=en&sortBy=publishedAt"
                 f"&pageSize=8&apiKey={NEWS_API_KEY}")
        resp  = requests.get(url, timeout=5)
        if resp.status_code != 200:
            return 8

        articles = resp.json().get("articles", [])
        if not articles:
            return 8

        pos_words = ["rise","rally","strong","bullish","growth","beat",
                     "hawkish","rate hike","surplus","recovery","gains"]
        neg_words = ["fall","drop","weak","bearish","loss","miss",
                     "dovish","rate cut","deficit","recession","decline"]

        pos = neg = 0
        for a in articles[:6]:
            text = ((a.get("title") or "") + " " +
                    (a.get("description") or "")).lower()
            # Positive for base currency = good for pair
            if base.lower() in text:
                pos += sum(1 for w in pos_words if w in text)
                neg += sum(1 for w in neg_words if w in text)

        if neg > pos * 2:   return 0
        elif neg > pos:     return 4
        elif pos > neg * 2: return 15
        elif pos > neg:     return 10
        return 8

    except Exception:
        return 8

def score_trend_strength(pair):
    """
    15 pts — ADX (Average Directional Index).
    ADX > 25 = strong trend = good to trade.
    ADX < 20 = ranging market = avoid.
    """
    try:
        df = get_candles(pair, "H1", 50)
        if len(df) < 20:
            return 8

        high  = df["high"]
        low   = df["low"]
        close = df["close"]

        # True Range
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low  - close.shift()).abs()
        tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        # Directional movement
        up_move   = high.diff()
        down_move = (-low.diff())
        pos_dm    = up_move.where((up_move > down_move) & (up_move > 0), 0)
        neg_dm    = down_move.where((down_move > up_move) & (down_move > 0), 0)

        pos_di = 100 * pos_dm.rolling(14).mean() / atr
        neg_di = 100 * neg_dm.rolling(14).mean() / atr
        dx     = (100 * (pos_di - neg_di).abs() / (pos_di + neg_di)).fillna(0)
        adx    = dx.rolling(14).mean()

        val = adx.iloc[-1]
        if   val >= 35: return 15   # very strong trend
        elif val >= 25: return 10   # solid trend
        elif val >= 20: return 5    # weak trend
        return 0                    # ranging — avoid

    except Exception:
        return 8

def full_score(pair):
    """
    Score pair across all 5 layers.
    Returns (total, breakdown, direction, signature)
    """
    t, direction = score_technical(pair)
    s, session   = score_session(pair)
    v            = score_spread(pair)
    n            = score_news_forex(pair)
    a            = score_trend_strength(pair)

    breakdown = {
        "technical":  t,
        "session":    s,
        "spread":     v,
        "news":       n,
        "adx":        a,
        "total":      t + s + v + n + a,
        "session_name": session,
        "direction":  direction
    }

    sig = sign_score(pair, {k: v for k, v in breakdown.items()
                            if k not in ("session_name","direction")})
    return breakdown["total"], breakdown, direction, sig

# ══════════════════════════════════════════════════════════════
#   ORDER EXECUTION
# ══════════════════════════════════════════════════════════════

peak_prices = {}

def write_trade(record):
    record["integrity"] = hash_trade(record)
    exists = os.path.isfile(TRADE_LOG)
    with open(TRADE_LOG, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=record.keys())
        if not exists:
            w.writeheader()
        w.writerow(record)

def calculate_units(balance, price):
    """Calculate position size based on 1% risk"""
    risk_amount = balance * RISK_PER_TRADE
    units       = int(risk_amount / (price * 0.02))   # assume 2% move
    units       = max(units, UNITS_BASE)
    return units

def place_buy(pair, score, breakdown, sig):
    if not verify_score(pair, {k: v for k, v in breakdown.items()
                               if k not in ("session_name","direction")}, sig):
        log(f"SECURITY: Score integrity FAILED for {pair} — skipping", "warning")
        return

    price = get_current_price(pair)
    if price is None:
        return

    balance = get_account_balance()
    units   = calculate_units(balance, price)
    mode_tag = "LIVE" if LIVE_MODE else "DEMO"

    # Stop loss and take profit
    sl_pips = price * HARD_STOP_LOSS
    tp_pips = price * (HARD_STOP_LOSS * 2)   # 2:1 reward ratio
    sl      = round(price - sl_pips, 5)
    tp      = round(price + tp_pips, 5)

    data = {
        "order": {
            "type":         "MARKET",
            "instrument":   pair,
            "units":        str(units),
            "timeInForce":  "FOK",
            "positionFill": "DEFAULT",
            "stopLossOnFill":   {"price": str(sl)},
            "takeProfitOnFill": {"price": str(tp)},
        }
    }

    try:
        r    = orders.OrderCreate(OANDA_ACCOUNT, data=data)
        resp = client.request(r)
        peak_prices[pair] = price

        record = {
            "time":       datetime.now(EST).strftime("%Y-%m-%d %H:%M"),
            "mode":       mode_tag,
            "action":     "BUY",
            "pair":       pair,
            "price":      round(price, 5),
            "units":      units,
            "sl":         sl,
            "tp":         tp,
            "score":      score,
            "technical":  breakdown["technical"],
            "session":    breakdown["session"],
            "spread":     breakdown["spread"],
            "news":       breakdown["news"],
            "adx":        breakdown["adx"],
            "session_name": breakdown["session_name"],
            "reason":     f"Score {score}/100 verified",
        }
        write_trade(record)
        log(f"BUY [{mode_tag}] {pair} @ {price:.5f} | Units:{units} | "
            f"SL:{sl} TP:{tp} | Score:{score}/100 | "
            f"T:{breakdown['technical']} Sess:{breakdown['session']} "
            f"Sprd:{breakdown['spread']} N:{breakdown['news']} ADX:{breakdown['adx']}")

    except V20Error as e:
        log(f"Oanda order error {pair}: {e}", "error")
    except Exception as e:
        log(f"Buy error {pair}: {e}", "error")

def close_trade(pair, trade_id, score, reason):
    try:
        price = get_current_price(pair) or 0
        data  = {"units": "ALL"}
        r     = trades_ep.TradeClose(OANDA_ACCOUNT, tradeID=trade_id, data=data)
        client.request(r)
        peak_prices.pop(pair, None)

        record = {
            "time":         datetime.now(EST).strftime("%Y-%m-%d %H:%M"),
            "mode":         "LIVE" if LIVE_MODE else "DEMO",
            "action":       "CLOSE",
            "pair":         pair,
            "price":        round(price, 5),
            "units":        "",
            "sl":           "", "tp":           "",
            "score":        score,
            "technical":    "", "session":      "",
            "spread":       "", "news":         "",
            "adx":          "", "session_name": "",
            "reason":       reason,
        }
        write_trade(record)
        log(f"CLOSE {pair} @ {price:.5f} | {reason}")

    except Exception as e:
        log(f"Close error {pair}: {e}", "error")

# ══════════════════════════════════════════════════════════════
#   EXIT MANAGEMENT
# ══════════════════════════════════════════════════════════════

def manage_exits(open_trades):
    for pair, trade in list(open_trades.items()):
        try:
            trade_id    = trade["id"]
            entry_price = float(trade["price"])
            current     = get_current_price(pair)
            if current is None:
                continue

            unrealized  = float(trade.get("unrealizedPL", 0))
            pnl_pct     = (current - entry_price) / entry_price

            if pair not in peak_prices or current > peak_prices[pair]:
                peak_prices[pair] = current
            peak           = peak_prices[pair]
            drop_from_peak = (current - peak) / peak if peak > 0 else 0

            score, breakdown, direction, sig = full_score(pair)

            log(f"HOLD {pair} | P&L:{pnl_pct*100:+.2f}% | "
                f"Unrealized:{unrealized:+.2f} | Score:{score}")

            # Exit 1: Score-based (momentum dying)
            if score < SELL_THRESHOLD:
                close_trade(pair, trade_id, score,
                            f"Score fell to {score} — momentum dying")
                continue

            # Exit 2: Trailing stop
            if pnl_pct >= TRAILING_AFTER and drop_from_peak <= -TRAILING_STOP:
                close_trade(pair, trade_id, score,
                            f"Trailing stop: {peak:.5f}→{current:.5f} "
                            f"({drop_from_peak*100:.2f}% from peak)")
                continue

            # Exit 3: Hard stop (backup — Oanda SL should catch this first)
            if pnl_pct <= -HARD_STOP_LOSS:
                close_trade(pair, trade_id, score,
                            f"Hard stop {pnl_pct*100:.2f}%")
                continue

        except Exception as e:
            log(f"Exit management error {pair}: {e}", "error")

# ══════════════════════════════════════════════════════════════
#   DAILY SAFETY
# ══════════════════════════════════════════════════════════════

day_start_nav = None

def safety_ok():
    global day_start_nav
    nav = get_nav()
    if day_start_nav is None:
        day_start_nav = nav
        return True
    change = (nav - day_start_nav) / day_start_nav
    if change <= -DAILY_LOSS_LIMIT:
        log(f"DAILY LOSS LIMIT ({change*100:.1f}%) — pausing", "warning")
        return False
    return True

# ══════════════════════════════════════════════════════════════
#   DEMO REPORT
# ══════════════════════════════════════════════════════════════

def print_demo_report():
    print("\n" + "═"*60)
    print("  7-DAY FOREX DEMO COMPLETE — PERFORMANCE REPORT")
    print("═"*60)
    if not os.path.exists(TRADE_LOG):
        print("  No trades logged.")
        return
    df     = pd.read_csv(TRADE_LOG)
    buys   = df[df["action"] == "BUY"]
    closes = df[df["action"] == "CLOSE"]
    print(f"  Total trades : {len(df)}")
    print(f"  Trades opened: {len(buys)}")
    print(f"  Trades closed: {len(closes)}")
    print(f"  Log file     : {TRADE_LOG}")
    print(f"\n  TOP PAIRS TRADED:")
    if not buys.empty:
        top = buys["pair"].value_counts().head(5)
        for pair, count in top.items():
            print(f"    {pair}: {count} trades")
    print(f"\n  TO GO LIVE:")
    print(f"  1. Review {TRADE_LOG}")
    print(f"  2. Set LIVE_MODE = True at top of this file")
    print(f"  3. Fund your Oanda account")
    print(f"  4. Re-run the bot")
    print("═"*60)

# ══════════════════════════════════════════════════════════════
#   MAIN LOOP
# ══════════════════════════════════════════════════════════════

def run():
    global day_start_nav
    scan_count = 0

    mode_str = "LIVE" if LIVE_MODE else f"DEMO Day {get_demo_day()}/{DEMO_DAYS}"
    print("═"*60)
    print(f"  FOREX BOT — SECURED | {mode_str}")
    print(f"  Oanda token  : {mask_key(OANDA_TOKEN)}")
    print(f"  Account      : {OANDA_ACCOUNT}")
    print(f"  Environment  : {OANDA_ENV}")
    print(f"  Pairs        : {len(ALL_PAIRS)} total")
    print(f"  Buy threshold: {BUY_THRESHOLD}+")
    print(f"  Sell threshold: below {SELL_THRESHOLD}")
    print(f"  Risk/trade   : {RISK_PER_TRADE*100}%")
    print(f"  Max positions: {MAX_POSITIONS}")
    print(f"  Stop loss    : {HARD_STOP_LOSS*100}%")
    print(f"  Trailing stop: after +{TRAILING_AFTER*100}%, trail {TRAILING_STOP*100}%")
    print(f"  Logs         : {LOG_DIR}")
    print("═"*60)

    while True:
        try:
            now       = datetime.now(EST)
            demo_day  = get_demo_day()
            scan_count += 1

            print(f"\n{'═'*60}")
            print(f"[{now.strftime('%Y-%m-%d %H:%M')} EST] Scan #{scan_count} | "
                  f"{'LIVE' if LIVE_MODE else f'DEMO Day {demo_day}/{DEMO_DAYS}'}")
            print(f"{'═'*60}")

            # Demo complete check
            if not LIVE_MODE and demo_complete():
                print_demo_report()
                print("\n  Demo done. Set LIVE_MODE=True to go live.")
                break

            # Session check
            session_name, session_quality = get_active_session()
            if not is_good_trading_time():
                log(f"Session: {session_name} (quality:{session_quality}) — waiting")
                time.sleep(1800)
                continue

            log(f"Session: {session_name} (quality:{session_quality})")

            # Reset daily tracker
            if now.hour == 0 and now.minute < 35:
                day_start_nav = get_nav()
                log(f"Day start NAV: ${day_start_nav:,.2f}")

            # Safety check
            if not safety_ok():
                log("Sleeping 1 hour due to daily loss limit")
                time.sleep(3600)
                continue

            balance     = get_account_balance()
            open_trades = get_open_trades()
            nav         = get_nav()

            log(f"NAV: ${nav:,.2f} | Balance: ${balance:,.2f} | "
                f"Open trades: {len(open_trades)}/{MAX_POSITIONS}")

            # Manage exits
            if open_trades:
                log(f"Managing {len(open_trades)} open trades...")
                manage_exits(open_trades)
                open_trades = get_open_trades()

            # Scan for new entries
            slots = MAX_POSITIONS - len(open_trades)
            if slots > 0:
                log(f"Scanning {len(ALL_PAIRS)} pairs ({slots} slots open)...")
                candidates = []

                for pair in ALL_PAIRS:
                    if pair in open_trades:
                        continue
                    score, breakdown, direction, sig = full_score(pair)
                    if score >= BUY_THRESHOLD and direction == "buy":
                        candidates.append((pair, score, breakdown, direction, sig))

                candidates.sort(key=lambda x: x[1], reverse=True)

                if candidates:
                    log(f"Found {len(candidates)} pairs at {BUY_THRESHOLD}+")
                    for pair, score, breakdown, direction, sig in candidates[:slots]:
                        log(f"Entering {pair} | Score:{score}/100 | "
                            f"Session:{breakdown['session_name']}")
                        place_buy(pair, score, breakdown, sig)
                        time.sleep(1)
                        open_trades = get_open_trades()
                        if len(open_trades) >= MAX_POSITIONS:
                            break
                else:
                    log(f"No pairs hit {BUY_THRESHOLD}+ this scan")

            log("Done. Next scan in 30 minutes.")
            time.sleep(1800)

        except KeyboardInterrupt:
            print("\n  Stopped.")
            if not LIVE_MODE:
                print_demo_report()
            break
        except Exception as e:
            log(f"Error: {e}", "error")
            time.sleep(60)

if __name__ == "__main__":
    run()
