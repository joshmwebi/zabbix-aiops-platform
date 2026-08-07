/*
  Current utilisation, growth rate, and projected time to full, per volume.

  This is the model that answers the question monitoring cannot: not "is
  this disk over 80% right now" but "how fast is it filling and when does it
  run out". A volume flat at 97% is a different problem from one at 82%
  climbing a point every three days — the first is occupied, the second is
  a deadline.

  Growth is a least-squares slope over a trailing window rather than a
  first-to-last difference, so a single anomalous day cannot dominate the
  projection.
*/

{% set trend_window_days = 14 %}

with daily as (

    select
        host,
        hostid,
        filesystem,
        day,
        pct_used_last as pct_used
    from {{ ref('gold_filesystem_daily') }}
    where day >= dateadd(day, -{{ trend_window_days }}, current_date())

),

trend as (

    select
        host,
        hostid,
        filesystem,

        count(*)                                                as days_observed,
        max(day)                                                as last_day,
        max_by(pct_used, day)                                   as pct_used_current,

        -- regr_slope returns change in y per unit of x. x is epoch seconds,
        -- so multiply by 86400 to express it as percentage points per day.
        regr_slope(pct_used, date_part(epoch_second, day)) * 86400
                                                                as pct_per_day

    from daily
    group by host, hostid, filesystem

)

select
    host,
    hostid,
    filesystem,
    last_day,
    days_observed,

    round(pct_used_current, 2)                                  as pct_used_current,
    round(100 - pct_used_current, 2)                            as pct_free,
    round(pct_per_day, 4)                                       as pct_per_day,

    -- Only meaningful when the volume is actually growing and there is
    -- enough history for the slope to mean anything.
    case
        when pct_per_day > 0.01 and days_observed >= 5
        then round((100 - pct_used_current) / pct_per_day, 1)
    end                                                         as days_to_full,

    case
        when pct_used_current >= 95 then 'critical'
        when pct_used_current >= 85 then 'warning'
        when pct_per_day > 0.01
             and days_observed >= 5
             and (100 - pct_used_current) / pct_per_day <= 60 then 'trending'
        else 'ok'
    end                                                         as status

from trend
