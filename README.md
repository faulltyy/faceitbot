# FACEIT CS2 Stats Bot

A Telegram bot that fetches CS2 statistics using the FACEIT Data API v4 and the FaceitAnalyser API. Fully Dockerized with PostgreSQL, Redis caching, and rate-limit protection.

## Features

### Core Commands
- `/stats <nickname>` — average stats for the last 20 CS2 matches
- `/matches <nickname> [count]` — per-match table (1–30 matches, default 20)
- `/start` / `/help` — welcome message with usage examples

### FaceitAnalyser Commands
- `/overview <nickname>` — lifetime stats overview (K/D, ELO history, win rate, headshots)
- `/mapstats <nickname>` — per-map performance breakdown table
- `/highlights <nickname>` — best & worst match records across all metrics
- `/insights <nickname> [segment]` — stats breakdown by segment (default: `all`)

### Admin Commands
- `/admin` — admin panel (authorized only)
- `/astats` — bot analytics dashboard (admin only)

### Bot Features
- **Bot Menu** — commands auto-register so Telegram shows native autocomplete
- **Smart caching** — match data cached 7 days, summaries 15 min, FA data 1–24 hours
- **Rate-limit safe** — exponential backoff on 429 + concurrency semaphore
- **Edge-case handling** — player not found, no matches, API errors with clean messages

## Insights Segments

The `/insights` command accepts an optional segment to break down stats by:

| Segment | Description |
|---|---|
| `all` | Overall stats (default) |
| `map` | Per-map breakdown |
| `weekday` | Per day of the week |
| `hour` | Per hour of the day |
| `premade` | Solo vs premade party |
| `hub` | Per hub/league |
| `region` | Per region |
| `bestof` | Best-of-1 vs Best-of-3 |
| `win` | When winning vs losing |
| `gamemode` | Per game mode |
| `kills` | By kill count brackets |
| `deaths` | By death count brackets |
| `kdr` | By K/D ratio brackets |
| `krr` | By K/R ratio brackets |
| `assists` | By assist count brackets |
| `headshots` | By headshot count brackets |
| `headshotpercent` | By HS% brackets |
| `diff` | By K-D diff brackets |
| `rounds` | By round count |
| `aces` | By ace count |
| `quadras` | By 4K count |
| `triples` | By 3K count |
| `pentas` | By 5K count |
| `mvps` | By MVP count |
| `delta` | By ELO change |
| `finalscore` | By final score |
| `firsthalfscore` | By first half score |
| `secondhalfscore` | By second half score |
| `overtimerounds` | By overtime rounds |
| `team` | By team |
| `date` | By date |
| `nickname` | By nickname |

**Example:** `/insights faullty map`

## Quick Start

### 1. Clone & configure

```bash
cp .env.example .env
# Edit .env and fill in your tokens:
#   TELEGRAM_BOT_TOKEN       — from @BotFather
#   FACEIT_API_KEY            — from https://developers.faceit.com/
#   FACEIT_ANALYSER_API_KEY   — from https://faceitanalyser.com/
```

### 2. Run with Docker Compose

```bash
docker compose up --build -d
```

This starts:
- **postgres** — `postgres:16-alpine` on port 5432
- **redis** — `redis:alpine` on port 6379
- **bot** — the Python bot container

### 3. Talk to your bot

Open Telegram, find your bot, and type `/` to see the command menu.

## Project Structure

```
faceitbot/
├── app/
│   ├── config.py                # Environment-based configuration
│   ├── api/
│   │   ├── faceit.py            # Async FACEIT Data API v4 client
│   │   └── faceit_analyser.py   # Async FaceitAnalyser API client
│   ├── services/
│   │   ├── stats.py             # Stats aggregation + Redis caching
│   │   └── formatter.py         # Telegram <pre> table formatters
│   ├── bot/
│   │   └── handlers.py          # All command handlers
│   └── middleware/
│       └── analytics.py         # Auto-tracking middleware
├── main.py                      # Entry point
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

## Running Locally (without Docker)

```bash
pip install -r requirements.txt

# Make sure Redis and PostgreSQL are running locally
# Update REDIS_URL and DATABASE_URL in .env

python main.py
```

## License

MIT
