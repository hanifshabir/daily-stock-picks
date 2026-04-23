# Daily Stock Picks Setup Notes

## What We Built

We built an automated stock-pick workflow that:
- runs from GitHub Actions
- scans a watchlist of stocks
- ranks the full watchlist using daily trend plus intraday momentum, VWAP, and volume rules
- saves report artifacts in multiple formats
- sends the daily report by email
- can optionally send Telegram alerts
- is being extended to write each run into Snowflake

This is a watchlist generator, not an auto-trading bot.

## GitHub Repo

- Repo URL: `https://github.com/hanifshabir/daily-stock-picks`

## Important Files

- `src/run_daily.py`: main runner that fetches data, scores stocks, builds reports, sends alerts, and will write to Snowflake
- `src/strategy.py`: daily + intraday stock scoring logic
- `watchlist.json`: stocks to scan each day
- `.github/workflows/daily-picks.yml`: GitHub Actions automation
- `README.md`: project overview
- `SETUP_NOTES.md`: running history and setup reference
- `output/latest_report.md`: markdown report from the latest local run
- `output/latest_report.html`: HTML dashboard report from the latest local run
- `output/latest_picks.csv`: full latest watchlist output in table form
- `output/latest_picks.json`: full latest watchlist output in JSON form

## Required GitHub Actions Secrets

Repository secrets used for email delivery:
- `EMAIL_FROM`: Gmail address used to send the email
- `EMAIL_TO`: email address that receives the daily picks
- `EMAIL_APP_PASSWORD`: Google app password for the Gmail sender account

Optional Telegram secrets:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Snowflake secrets for database loading:
- `SNOWFLAKE_ACCOUNT`: account identifier only, for example `SULBUIM-XN26289`
- `SNOWFLAKE_USER`: Snowflake username
- `SNOWFLAKE_PASSWORD`: Snowflake password
- `SNOWFLAKE_WAREHOUSE`: warehouse name
- `SNOWFLAKE_DATABASE`: database name
- `SNOWFLAKE_SCHEMA`: schema name
- `SNOWFLAKE_TABLE`: optional, defaults to `STOCK_PICKS_DAILY`

## How To Rerun The Workflow

1. Open the repo on GitHub.
2. Go to `Actions`.
3. Click `Daily Stock Picks`.
4. Click `Run workflow`.
5. Wait for the run to finish.
6. Check the email inbox and spam folder.

## How To Update The Watchlist

1. Open `watchlist.json`.
2. Add or remove stock symbols.
3. Commit the change to `main`.
4. Run the workflow again.

## Current Scoring Model

The latest version uses both daily and intraday data:
- 6 months of daily history for longer trend checks
- 5-minute intraday candles for current session behavior

It scores stocks using:
- price vs 20-day average
- price vs 50-day average
- 5-day momentum
- 20-day momentum
- daily volume ratio
- move from today's open
- move vs prior close
- intraday volume ratio
- VWAP gap

Actions are labeled:
- `Buy Watch`
- `Watch`
- `Skip`

## Current Outputs

Each run currently produces:
- markdown summary report
- HTML dashboard email
- CSV export of the full watchlist
- JSON export of the full watchlist
- chart images for score ranking and momentum vs volume

The email now includes:
- summary cards for top names
- a full ranked table for all symbols
- charts embedded in the email

## Scheduling Notes

Important scheduling history:
- GitHub manual runs work
- GitHub's built-in scheduler was unreliable during testing
- a temporary `every 5 minutes` test schedule was used and then replaced locally

Current local workflow setting:
- `.github/workflows/daily-picks.yml` is set to `0 8 * * *`
- that means `08:00 UTC`
- during BST, that is `09:00 UK time`
- during GMT, that is `08:00 UK time`

This means GitHub cron is not truly "always 9:00 UK local time" year-round.
If exact local-time scheduling matters later, consider using an external scheduler instead of GitHub cron.

## Common Fixes

### Workflow succeeds but no email arrives

- check spam or junk folder
- confirm `EMAIL_FROM`, `EMAIL_TO`, and `EMAIL_APP_PASSWORD` are set in repository secrets
- confirm the Gmail account supports app passwords
- confirm 2-Step Verification is enabled on the Gmail account

### GitHub push fails with workflow permission error

Use a GitHub Personal Access Token with:
- `repo`
- `workflow`

### GitHub Actions shows Node 20 deprecation warning

Use newer action versions in `.github/workflows/daily-picks.yml`:
- `actions/checkout@v6`
- `actions/setup-python@v6`
- `actions/upload-artifact@v6`

### The report feels too basic

Possible next upgrades:
- add suggested entry, stop loss, and target
- add SPY market trend filter
- tune intraday score weights
- persist daily runs into Snowflake

### GitHub schedule does not fire reliably

- manual runs may still work even when the GitHub schedule is delayed
- GitHub scheduled workflows are not exact
- consider an external scheduler if reliable wall-clock execution matters

### Snowflake rows do not load

- confirm all Snowflake secrets are present
- confirm the account secret uses the account identifier, not the full URL
- confirm the Snowflake user has permission to create and insert into the target table
- confirm warehouse, database, and schema names are correct

## Current Status

Current working features:
- intraday scoring with full watchlist ranking
- markdown, HTML, CSV, and JSON output
- Gmail delivery of picks
- manual GitHub Actions runs

Current in-progress work:
- Snowflake table loading code has been added locally
- the Snowflake integration has not yet been pushed and verified end to end

Current local-only changes not yet confirmed on GitHub:
- Snowflake connector dependency
- Snowflake insert logic
- workflow env vars for Snowflake
- daily `0 8 * * *` schedule in the workflow file

## Notes For Future Reference

If this project is revisited later, start by checking:
1. repository secrets
2. `watchlist.json`
3. latest GitHub Actions run logs
4. latest email received
5. whether Snowflake integration has been pushed yet
