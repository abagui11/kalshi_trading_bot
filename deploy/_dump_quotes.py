"""Dump lightweight quote history from kalshi_decisions (runs on the VPS)."""
import json
import sqlite3

con = sqlite3.connect("/opt/kalshi-15m-bot/ledger.db")
con.row_factory = sqlite3.Row
rows = [
    dict(r)
    for r in con.execute(
        "SELECT ts, market_ticker, product_id, yes_mid_cents, seconds_to_expiry, "
        "spot, strike, prior_15m_ret, prior_1h_ret FROM kalshi_decisions "
        "WHERE yes_mid_cents IS NOT NULL AND bot_id='eva_wick' ORDER BY id"
    )
]
print(json.dumps(rows))
