/*
  Fleet-level rollup: one row per day.

  Two uses. Day to day it answers "is the fleet as a whole getting
  fuller". Historically it makes reporting gaps visible — hosts_reporting
  dropping and staying down is exactly the signature of the June 2026 event
  where a third of the fleet went silent, and it is far easier to see as a
  time series than as a list of active problems.
*/

with per_host_day as (

    select
        day,
        host,
        max(pct_used_last) as worst_volume_pct
    from {{ ref('gold_filesystem_daily') }}
    group by day, host

)

select
    day,
    count(distinct host)                                    as hosts_reporting,
    round(avg(worst_volume_pct), 2)                         as avg_worst_volume_pct,
    round(max(worst_volume_pct), 2)                         as max_volume_pct,
    count_if(worst_volume_pct >= 95)                        as hosts_critical,
    count_if(worst_volume_pct >= 85 and worst_volume_pct < 95) as hosts_warning
from per_host_day
group by day
