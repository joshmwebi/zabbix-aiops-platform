/*
  Daily filesystem utilisation per host and volume.

  The grain deliberately drops from hourly to daily: capacity questions are
  asked in days and weeks, and a daily grain makes the growth regression in
  gold_host_headroom stable against hourly noise (a temp file written and
  deleted inside an hour should not register as growth).

  Zabbix internal metrics are excluded here rather than downstream, because
  zabbix[wcache,history,pused] would otherwise appear as a "filesystem"
  named wcache on the monitoring server.
*/

select
    host,
    hostid,
    filesystem,
    day,

    avg(value_avg)                  as pct_used_avg,
    max(value_max)                  as pct_used_max,
    min(value_min)                  as pct_used_min,

    -- Last observation of the day, which is what "how full is it" means.
    max_by(value_avg, hour_ts)      as pct_used_last,

    count(*)                        as hours_observed,
    max(hour_ts)                    as last_seen_at

from {{ ref('silver_metric_hours') }}
where metric_family = 'vfs.fs.dependent.size'
  and measurement_mode = 'pused'
  and not is_zabbix_internal
  and filesystem is not null
group by host, hostid, filesystem, day
