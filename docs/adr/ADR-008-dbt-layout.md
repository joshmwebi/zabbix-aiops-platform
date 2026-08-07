# ADR-008: One schema with layer prefixes, and env-driven dbt profiles

**Status:** accepted · **Date:** 2026-08-06

## Context
Two decisions were needed before writing models.

**Where models materialise.** The medallion convention puts each layer in
its own schema. In this Snowflake project, `DEV_ZABBIX.TELEMETRY` is a
MANAGED ACCESS schema owned by `DEV_ZABBIX_DBADMIN`. Creating further
schemas requires a stored procedure and grants outside the pipeline role's
control, which would make a fresh clone depend on a provisioning request.

**Where credentials live.** dbt reads `profiles.yml`, conventionally in
`~/.dbt/` with literal credentials in it. The extractor already reads a
single `.env`. Two credential stores for one warehouse invites drift, and a
`profiles.yml` containing a key path and passphrase is a file that must
never be committed.

## Decision
Materialise every model into the existing `TELEMETRY` schema, with layer
prefixes (`silver_`, `gold_`) on the model names.

Write `profiles.yml` entirely as `env_var()` lookups and commit it. Run dbt
through `scripts/dbt.py`, which loads `.env` and passes through to the dbt
CLI with `--project-dir` and `--profiles-dir` pointed at the repo.

## Consequences
- No dependency on schema-creation privileges; a clone runs against the
  provisioned schema as-is.
- Layer separation is by naming convention rather than enforced by
  permissions. Granting read on gold but not silver is not possible without
  splitting schemas later — acceptable now, and the change would be
  configuration in `dbt_project.yml` rather than model rewrites.
- One credential store shared by extractor and dbt; `profiles.yml` is safe
  in version control.
- Bare `dbt run` will not work — it finds no environment variables. This is
  documented, and the wrapper reports which variables are missing rather
  than failing inside dbt.
