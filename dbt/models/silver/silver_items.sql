/*
  Item metadata with the Zabbix key parsed into usable columns.

  A raw key looks like:      vfs.fs.dependent.size[C:,pused]
  Which is really three things glued together:
      metric_family  vfs.fs.dependent.size
      parameters     C:,pused
      -> filesystem  C:
      -> mode        pused   (percent used)

  Downstream models should never have to pattern-match against key strings.
  That is the whole point of this layer: ask "how full is C: on host X",
  not "which LIKE pattern does Zabbix 7.0 use for filesystem items this
  week".
*/

with raw as (

    select
        itemid,
        hostid,
        host,
        name,
        key_,
        units,
        value_type,
        delay
    from {{ source('bronze', 'bronze_items') }}

),

parsed as (

    select
        itemid,
        hostid,
        host,
        name,
        key_,
        nullif(units, '')                          as units,
        value_type,
        delay,

        -- Everything before the first bracket.
        split_part(key_, '[', 1)                   as metric_family,

        -- Everything between the brackets, or null when there are none.
        regexp_substr(key_, '\\[(.*)\\]', 1, 1, 'e', 1) as parameters

    from raw

)

select
    itemid,
    hostid,
    host,
    name,
    key_,
    units,
    value_type,
    delay,
    metric_family,
    parameters,

    -- Filesystem items carry "<mount>,<mode>". Only populate these for
    -- items that actually are filesystem items, so an unrelated key with a
    -- comma in its parameters does not masquerade as a mount point.
    case
        when metric_family = 'vfs.fs.dependent.size'
        then split_part(parameters, ',', 1)
    end                                            as filesystem,

    case
        when metric_family = 'vfs.fs.dependent.size'
        then split_part(parameters, ',', 2)
    end                                            as measurement_mode,

    -- Zabbix's own internal metrics use the zabbix[...] family and describe
    -- the monitoring server, not a monitored host. They share suffixes with
    -- real metrics (zabbix[wcache,history,pused]) and will pollute any
    -- capacity mart that filters on '%pused%'.
    metric_family ilike 'zabbix[%'                 as is_zabbix_internal

from parsed
