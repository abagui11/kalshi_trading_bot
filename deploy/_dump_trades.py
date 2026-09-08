"""Dump eva_wick positions + ALL opened decisions + skip stats from the VPS ledger."""
import json
import sqlite3

con = sqlite3.connect("/opt/kalshi-15m-bot/ledger.db")
con.row_factory = sqlite3.Row

out = {}

out["positions"] = [
    dict(r) for r in con.execute("SELECT * FROM paper_positions ORDER BY id")
]
out["trades"] = [
    dict(r) for r in con.execute("SELECT * FROM paper_trades ORDER BY id")
]
out["decisions_opened"] = [
    dict(r)
    for r in con.execute("SELECT * FROM kalshi_decisions WHERE opened=1 ORDER BY id")
]
out["skip_counts"] = [
    dict(r)
    for r in con.execute(
        "SELECT bot_id, skip_codes, COUNT(*) AS n FROM kalshi_decisions "
        "WHERE opened=0 GROUP BY bot_id, skip_codes ORDER BY n DESC"
    )
]
out["paper_state"] = [dict(r) for r in con.execute("SELECT * FROM paper_state")]

print(json.dumps(out, default=str))
