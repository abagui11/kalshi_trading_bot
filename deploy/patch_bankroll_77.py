from pathlib import Path

import paper

p = Path(".env")
text = p.read_text(encoding="utf-8")
reps = {
    "PORTFOLIO_VALUE": "77",
    "PAPER_PORTFOLIO_VALUE": "77",
    "KALSHI_MAX_CONTRACTS": "5",
    "KALSHI_BANKROLL_USD": "77",
    "KALSHI_DEPLOY_PCT": "0.05",
    "KALSHI_USE_LIVE_BALANCE": "true",
    "KALSHI_PAPER_ONLY": "true",
}
lines = text.splitlines()
out = []
seen: set[str] = set()
for line in lines:
    if (not line.strip()) or line.strip().startswith("#") or ("=" not in line):
        out.append(line)
        continue
    k = line.split("=", 1)[0].strip()
    if k in reps:
        out.append(f"{k}={reps[k]}")
        seen.add(k)
    else:
        out.append(line)
for k, v in reps.items():
    if k not in seen:
        out.append(f"{k}={v}")
p.write_text("\n".join(out) + "\n", encoding="utf-8")
print("linode env patched")
for k, v in reps.items():
    print(k, v)

paper.reset_book(77)
print(paper.get_stats())
