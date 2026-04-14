"""
╔══════════════════════════════════════════════════════════════╗
║         RUN ALL — CLOUD DEPLOYMENT MASTER SCRIPT            ║
║                                                              ║
║  Launches both bots simultaneously:                          ║
║    - elite_bot_secure.py  (stocks)                          ║
║    - forex_bot_secure.py  (forex)                           ║
║                                                              ║
║  Keys loaded from environment variables (set in Railway)    ║
║  No .keys files needed in cloud — fully secure              ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import time
import threading
import logging
from datetime import datetime

# ══════════════════════════════════════════════════════════════
#   CLOUD CONFIGURATION
#   These are read from Railway environment variables
#   Set them in Railway dashboard → Variables tab
# ══════════════════════════════════════════════════════════════

# Stock bot keys
ALPACA_API_KEY    = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
NEWS_API_KEY      = os.environ.get("NEWS_API_KEY", "")

# Forex bot keys
OANDA_TOKEN       = os.environ.get("OANDA_TOKEN", "")
OANDA_ACCOUNT     = os.environ.get("OANDA_ACCOUNT", "")

# Mode
LIVE_MODE         = os.environ.get("LIVE_MODE", "false").lower() == "true"

# ══════════════════════════════════════════════════════════════
#   VALIDATE KEYS
# ══════════════════════════════════════════════════════════════

def validate():
    missing = []
    if not ALPACA_API_KEY:    missing.append("ALPACA_API_KEY")
    if not ALPACA_SECRET_KEY: missing.append("ALPACA_SECRET_KEY")
    if not OANDA_TOKEN:       missing.append("OANDA_TOKEN")
    if not OANDA_ACCOUNT:     missing.append("OANDA_ACCOUNT")

    if missing:
        print("═"*60)
        print("  ERROR: Missing environment variables:")
        for m in missing:
            print(f"    - {m}")
        print("\n  Set these in Railway dashboard → Variables tab")
        print("═"*60)
        raise SystemExit(1)

    print("  All environment variables loaded successfully")
    print(f"  Mode: {'LIVE' if LIVE_MODE else 'PAPER/DEMO'}")

# ══════════════════════════════════════════════════════════════
#   LOGGING
# ══════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s"
)
stock_logger = logging.getLogger("STOCK")
forex_logger = logging.getLogger("FOREX")

# ══════════════════════════════════════════════════════════════
#   IMPORT BOTS
#   Both bots are modified to accept keys as parameters
#   instead of reading from encrypted files
# ══════════════════════════════════════════════════════════════

def run_stock_bot():
    """Run the stock bot in its own thread"""
    stock_logger.info("Starting stock bot...")
    try:
        # Inject keys into environment for the bot to use
        os.environ["ALPACA_API_KEY"]    = ALPACA_API_KEY
        os.environ["ALPACA_SECRET_KEY"] = ALPACA_SECRET_KEY
        os.environ["NEWS_API_KEY"]      = NEWS_API_KEY
        os.environ["LIVE_MODE"]         = str(LIVE_MODE).lower()

        import importlib.util, sys

        spec   = importlib.util.spec_from_file_location(
            "stock_bot", "/app/elite_bot_secure.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.run()

    except Exception as e:
        stock_logger.error(f"Stock bot crashed: {e}")
        stock_logger.info("Restarting stock bot in 60 seconds...")
        time.sleep(60)
        run_stock_bot()   # auto-restart

def run_forex_bot():
    """Run the forex bot in its own thread"""
    forex_logger.info("Starting forex bot...")
    try:
        os.environ["OANDA_TOKEN"]   = OANDA_TOKEN
        os.environ["OANDA_ACCOUNT"] = OANDA_ACCOUNT
        os.environ["NEWS_API_KEY"]  = NEWS_API_KEY
        os.environ["LIVE_MODE"]     = str(LIVE_MODE).lower()

        import importlib.util

        spec   = importlib.util.spec_from_file_location(
            "forex_bot", "/app/forex_bot_secure.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.run()

    except Exception as e:
        forex_logger.error(f"Forex bot crashed: {e}")
        forex_logger.info("Restarting forex bot in 60 seconds...")
        time.sleep(60)
        run_forex_bot()   # auto-restart

# ══════════════════════════════════════════════════════════════
#   HEALTH MONITOR
# ══════════════════════════════════════════════════════════════

def health_monitor(stock_thread, forex_thread):
    """Monitor both bots and restart if they crash"""
    while True:
        time.sleep(300)   # check every 5 minutes
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        stock_alive = stock_thread.is_alive()
        forex_alive = forex_thread.is_alive()

        print(f"[{now}] Health check | "
              f"Stock: {'OK' if stock_alive else 'DOWN'} | "
              f"Forex: {'OK' if forex_alive else 'DOWN'}")

        if not stock_alive:
            print(f"[{now}] Restarting stock bot...")
            new_stock = threading.Thread(target=run_stock_bot,
                                         name="StockBot", daemon=True)
            new_stock.start()
            stock_thread = new_stock

        if not forex_alive:
            print(f"[{now}] Restarting forex bot...")
            new_forex = threading.Thread(target=run_forex_bot,
                                          name="ForexBot", daemon=True)
            new_forex.start()
            forex_thread = new_forex

# ══════════════════════════════════════════════════════════════
#   MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("═"*60)
    print("  ELITE TRADING SYSTEM — CLOUD DEPLOYMENT")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  Mode: {'LIVE TRADING' if LIVE_MODE else 'PAPER/DEMO'}")
    print("═"*60)

    # Validate all keys present
    validate()

    # Launch stock bot thread
    stock_thread = threading.Thread(
        target=run_stock_bot,
        name="StockBot",
        daemon=True
    )
    stock_thread.start()
    print("  Stock bot started")

    # Small delay so both don't hammer APIs simultaneously
    time.sleep(30)

    # Launch forex bot thread
    forex_thread = threading.Thread(
        target=run_forex_bot,
        name="ForexBot",
        daemon=True
    )
    forex_thread.start()
    print("  Forex bot started")
    print("  Both bots running 24/7")
    print("═"*60)

    # Start health monitor (keeps main thread alive)
    health_monitor(stock_thread, forex_thread)
