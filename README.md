# 🎸 India Metal Gigs Bot

Automated Discord bot that posts upcoming metal, rock, punk, and hardcore gigs across India.

## How it works

- Scrapes [Bandsintown](https://www.bandsintown.com) daily for events in 7 Indian cities
- Compares against previously posted events to avoid duplicates
- Posts new gigs to Discord via webhook with artist image, venue, date, and ticket link
- Runs on GitHub Actions — no server needed

## Cities monitored

Bengaluru • Mumbai • Delhi • Chennai • Hyderabad • Pune • Kolkata

## Setup

1. **Fork/clone** this repo
2. **Create a Discord webhook** in your server (`Server Settings → Integrations → Webhooks`)
3. **Add GitHub Secret**: `Settings → Secrets → Actions → New` → Name: `DISCORD_WEBHOOK`, Value: your webhook URL
4. **Push to GitHub** — the bot runs daily at 9:00 AM IST automatically
5. **Manual run**: Go to `Actions` tab → `India Metal Gigs Bot` → `Run workflow`

## Project structure

```
├── scraper.py              # Main script: fetch, parse, dedupe, post
├── requirements.txt        # Python dependencies
├── posted_events.json      # State file (auto-updated by bot)
└── .github/workflows/
    └── gigs.yml            # GitHub Actions workflow (daily cron)
```

## Local testing

```bash
export DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."
pip install -r requirements.txt
python scraper.py
```
