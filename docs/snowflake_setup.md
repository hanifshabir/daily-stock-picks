# Snowflake Setup Backup

This document preserves the Snowflake setup for the stock-picks pipeline so the integration can be recreated later even if it is removed from the app code.

## Environment Variables

The workflow currently expects these secrets:

- `SNOWFLAKE_ACCOUNT`
- `SNOWFLAKE_USER`
- `SNOWFLAKE_PASSWORD`
- `SNOWFLAKE_WAREHOUSE`
- `SNOWFLAKE_DATABASE`
- `SNOWFLAKE_SCHEMA`
- `SNOWFLAKE_TABLE`

Current values in use:

- Database: `STOCK_PICKS`
- Schema: `PUBLIC`
- Default table: `STOCK_PICKS_DAILY`

## Recreation DDL

```sql
create database if not exists STOCK_PICKS;
create schema if not exists STOCK_PICKS.PUBLIC;

create table if not exists STOCK_PICKS.PUBLIC.STOCK_PICKS_DAILY (
  run_ts timestamp_ntz,
  symbol string,
  action string,
  score number,
  last_price float,
  entry float,
  stop_loss float,
  daily_target float,
  target float,
  from_open_pct float,
  vs_prior_close_pct float,
  return_5d_pct float,
  return_20d_pct float,
  daily_rsi14 float,
  relative_strength_5d float,
  relative_strength_20d float,
  daily_volume_ratio float,
  intraday_volume_ratio float,
  vwap_gap_pct float,
  reason string,
  close_return_same_day float,
  next_day_return float,
  forward_5d_return float
);
```

## Incremental Safety DDL

The loader currently uses additive schema migration logic. If the table already exists and needs to be brought up to the latest shape, these are the safe patch statements:

```sql
alter table STOCK_PICKS.PUBLIC.STOCK_PICKS_DAILY add column if not exists daily_target float;
alter table STOCK_PICKS.PUBLIC.STOCK_PICKS_DAILY add column if not exists daily_rsi14 float;
alter table STOCK_PICKS.PUBLIC.STOCK_PICKS_DAILY add column if not exists relative_strength_5d float;
alter table STOCK_PICKS.PUBLIC.STOCK_PICKS_DAILY add column if not exists relative_strength_20d float;
alter table STOCK_PICKS.PUBLIC.STOCK_PICKS_DAILY add column if not exists close_return_same_day float;
alter table STOCK_PICKS.PUBLIC.STOCK_PICKS_DAILY add column if not exists next_day_return float;
alter table STOCK_PICKS.PUBLIC.STOCK_PICKS_DAILY add column if not exists forward_5d_return float;
```

## Insert Shape

Each run inserts these fields:

- `run_ts`
- `symbol`
- `action`
- `score`
- `last_price`
- `entry`
- `stop_loss`
- `daily_target`
- `target`
- `from_open_pct`
- `vs_prior_close_pct`
- `return_5d_pct`
- `return_20d_pct`
- `daily_rsi14`
- `relative_strength_5d`
- `relative_strength_20d`
- `daily_volume_ratio`
- `intraday_volume_ratio`
- `vwap_gap_pct`
- `reason`

The outcome columns are backfilled after runs when enough later price data exists:

- `close_return_same_day`
- `next_day_return`
- `forward_5d_return`

## Loader Notes

- The writer lives in [src/run_daily.py](/Users/malikhanif/Documents/Codex/2026-05-05-can-you-access-my-github/repo/src/run_daily.py:643).
- Snowflake writes are optional and are skipped when required secrets are missing.
- The first run after adding outcome fields can take longer because it backfills prior rows.
- If the Snowflake integration is removed later, keep this file and the GitHub secrets list as the recreation reference.
