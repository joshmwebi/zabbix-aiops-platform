/*
  One row per item per hour, deduplicated and timestamped.

  Two things happen here that bronze deliberately does not do:

  1. `clock` becomes a real timestamp. Bronze keeps the Unix epoch integer
     exactly as the API returned it.

  2. Duplicates are removed. The extractor resumes from a watermark equal to
     the newest timestamp it loaded, so the boundary hour can be pulled
     twice across consecutive runs, and a re-run of the same backfill would
     double every row. Deduplicating here means the loader stays simple and
     idempotency is enforced where it can be verified.
*/

with trends as (

    select
        itemid,
        clock,
        num,
        value_min,
        value_avg,
        value_max,
        _loaded_at
    from {{ source('bronze', 'bronze_trends') }}

),

deduped as (

    select *
    from trends
    qualify row_number() over (
        partition by itemid, clock
        -- Keep the most recently loaded copy: if a row was re-extracted,
        -- the later load reflects the more settled value.
        order by _loaded_at desc
    ) = 1

)

select
    d.itemid,
    i.hostid,
    i.host,
    i.name                                  as item_name,
    i.metric_family,
    i.filesystem,
    i.measurement_mode,
    i.units,
    i.is_zabbix_internal,

    to_timestamp_ntz(d.clock)               as hour_ts,
    date_trunc('day', to_timestamp_ntz(d.clock)) as day,
    d.clock                                 as clock_epoch,

    d.num                                   as sample_count,
    d.value_min,
    d.value_avg,
    d.value_max,
    d._loaded_at

from deduped d
join {{ ref('silver_items') }} i using (itemid)
