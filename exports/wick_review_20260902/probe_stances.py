"""Probe local hub ledger.db for intel_stances covering Sep 2 2026 trade windows."""
import sqlite3

P = r"C:\Users\abagu\OneDrive\Documents\Republic\Agential Trading Product\trading_bot_MVP\ledger.db"

con = sqlite3.connect(P)
cur = con.cursor()
tabs = [r[0] for r in cur.execute("select name from sqlite_master where type='table'").fetchall()]
print("tables:", tabs)

if "intel_stances" in tabs:
    n = cur.execute("select count(*) from intel_stances").fetchone()[0]
    print("intel_stances rows:", n)
    print("\nnewest 12:")
    for r in cur.execute(
        "select cycle_ts, product_id, timeframe, stance, round(confidence,2), source "
        "from intel_stances order by created_at desc limit 12"
    ):
        print(r)
    print("\nSep 2 2026 UTC 17:00 -> Sep 3 03:00 rows:")
    for r in cur.execute(
        "select cycle_ts, product_id, timeframe, stance, round(confidence,2), source "
        "from intel_stances where cycle_ts >= '2026-09-02T17:00' and cycle_ts <= '2026-09-03T03:00' "
        "order by cycle_ts, product_id, timeframe"
    ):
        print(r)
