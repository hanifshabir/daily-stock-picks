# Daily Stock Picks Setup Notes

## What We Built

We built a free automated stock-pick workflow that:
- runs on GitHub Actions every weekday morning
- scans a watchlist of stocks
- ranks the strongest candidates using simple momentum and volume rules
- saves a report artifact
- sends the daily report by email

This is a watchlist generator, not an auto-trading bot.

## GitHub Repo

- Repo URL: `https://github.com/hanifshabir/daily-stock-picks`

## Important Files

- `src/run_daily.py`: main runner that fetches data, scores stocks, and sends alerts
- `src/strategy.py`: stock scoring logic
- `watchlist.json`: stocks to scan each day
- `.github/workflows/daily-picks.yml`: GitHub Actions automation
- `README.md`: project overview

## Required GitHub Actions Secrets

Repository secrets used for email delivery:
- `EMAIL_FROM`: Gmail address used to send the email
- `EMAIL_TO`: email address that receives the daily picks
- `EMAIL_APP_PASSWORD`: Google app password for the Gmail sender account

Optional Telegram secrets:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

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
- add `Buy`, `Watch`, or `Skip` labels
- add suggested entry, stop loss, and target
- add SPY market trend filter
- make the email easier to read
- log daily picks to CSV for tracking performance

## Current Status

Current working features:
- GitHub Actions automation
- daily report artifact
- Gmail delivery of picks

## Notes For Future Reference

If this project is revisited later, start by checking:
1. repository secrets
2. `watchlist.json`
3. latest GitHub Actions run logs
4. latest email received
