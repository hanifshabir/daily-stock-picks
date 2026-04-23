# Free Daily NYSE Stock Picks MVP

This project gives you a free, automatic daily stock-pick workflow.

What it does:
- runs automatically on weekday mornings using GitHub Actions
- scores a watchlist of U.S. stocks
- writes a daily report
- optionally sends the top picks to Telegram for free

What it does not do:
- it does not place trades automatically
- it does not use official real-time exchange data
- it should be treated as a watchlist generator, not financial advice

## Why this stack

This is the cheapest practical setup:
- `GitHub Actions`: free automation on a public repo
- `Python`: free and easy to customize
- `yfinance`: free market data library backed by Yahoo data
- `Telegram`: free alert delivery

Important:
- official real-time U.S. market-open data usually costs money
- this MVP uses free market data, so it is best for a pre-open candidate list or an opening-watchlist workflow

## Project layout

- `.github/workflows/daily-picks.yml`: automation schedule
- `src/run_daily.py`: main script
- `src/strategy.py`: ranking logic
- `watchlist.json`: stocks to scan
- `requirements.txt`: Python packages
- `output/`: report files written during a run

## Setup

### 1. Create a GitHub repository

Use a public repo if you want the simplest fully free GitHub Actions setup.

### 2. Add your watchlist

Edit `watchlist.json` and keep it to roughly `20` to `100` tickers for the first version.

Example:

```json
[
  "AAPL",
  "MSFT",
  "NVDA",
  "AMZN",
  "META",
  "GOOGL",
  "TSLA",
  "AMD"
]
```

### 3. Optional: add Telegram alerts

Create a Telegram bot with `@BotFather`, then add these repository secrets in GitHub:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

If you skip this, the workflow still runs and saves a report artifact.

### 4. Push the code

Commit the files and push them to the default branch.

### 5. Enable Actions

In GitHub:
- open the repo
- go to `Actions`
- allow workflows if GitHub asks

### 6. Test manually

Run the workflow with `workflow_dispatch` once before relying on the schedule.

## Schedule

The included workflow is set to run on weekdays at `9:23 AM America/New_York`.

That timing is intentional:
- NYSE core trading starts at `9:30 AM ET`
- GitHub notes scheduled workflows can be delayed, especially near the start of the hour
- running at `9:23` gives a little buffer

## Local run

Create a virtual environment, install dependencies, and run:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python src/run_daily.py
```

## How the strategy works

The default strategy is intentionally simple and free:
- checks whether price is above the `20-day` moving average
- checks whether price is above the `50-day` moving average
- rewards positive `5-day` and `20-day` momentum
- rewards stronger-than-average recent volume
- ranks candidates and returns the best few names

This is a starter model. You should paper-test it before using real money.

## Realistic timeline

### MVP timeline

- `Day 1`: set up repo, workflow, watchlist, first successful run
- `Day 2`: tune rules and score weights
- `Days 3-5`: paper-track results and remove bad signals

### Better version timeline

- `Week 2`: add market-regime filter like SPY trend
- `Week 2`: add stop-loss and position-sizing suggestions
- `Week 3`: add performance tracking and a dashboard
- `Week 3+`: switch to a paid official data source only if you need more precise opening data

## Best next improvements

- add a benchmark filter using `SPY`
- skip days when the market trend is weak
- save every run to a CSV log
- add backtesting
- add an earnings-event filter
- add premarket data later if you accept a paid data source or an unofficial one

## Safety note

Do not auto-buy from version 1.

Run it as a watchlist for at least `2 to 4 weeks`, track the picks, and only automate further if the results are consistently good.
