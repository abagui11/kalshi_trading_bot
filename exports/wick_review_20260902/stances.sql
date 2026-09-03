.mode list
.separator "|"
select cycle_ts, product_id, timeframe, stance, round(confidence,2), source, replace(rationale, char(10), ' ')
from intel_stances
where cycle_ts >= '2026-09-02T16:00' and cycle_ts <= '2026-09-03T04:00'
order by cycle_ts, product_id, timeframe;
select '=== MEDIUM ===';
select cycle_ts, replace(summary, char(10), ' ') from intel_medium
where cycle_ts >= '2026-09-02T16:00' and cycle_ts <= '2026-09-03T04:00'
order by cycle_ts;
select '=== MACRO ===';
select id, created_at, severity, category, eth_bias, substr(replace(headline, char(10), ' '),1,140)
from macro_events
where created_at >= '2026-09-02T16:00' and created_at <= '2026-09-03T04:00'
order by created_at;
