# FACEIT CS2 Stats Bot

A Telegram bot that fetches a player's CS2 statistics using the FACEIT Data API v4. Fully Dockerized with Redis caching and rate-limit protection.

## Features

- `/stats <nickname>` — average stats for the last 20 CS2 matches
- `/matches <nickname>` — per-match stats for the last 10 matches (one line per match)
- `/start` / `/help` — welcome message with usage examples
- **Bot Menu** — commands auto-register on startup so Telegram shows native autocomplete
- **Smart caching** — individual match stats cached for 7 days, formatted responses cached for 15 min
- **Rate-limit safe** — exponential backoff on 429 + concurrency semaphore (max 3 parallel requests)
- **Edge-case handling** — player not found, fewer matches than requested, no CS2 data

## Quick Start

### 1. Clone & configure

```bash
cp .env.example .env
# Edit .env and fill in your real tokens:
#   TELEGRAM_BOT_TOKEN  — from @BotFather
#   FACEIT_API_KEY       — from https://developers.faceit.com/
```

### 2. Run with Docker Compose

```bash
docker compose up --build -d
```

This starts:
- **redis** — `redis:alpine` on port 6379
- **bot** — the Python bot container

### 3. Talk to your bot

Open Telegram, find your bot, and type `/` to see the command menu.

#### `/stats s1mple`

```
📊 CS2 Stats for s1mple
🎯 Avg Kills: XX.XX
⚔️ Avg K/D: XX.XX
💀 Avg K/R: XX.XX
💥 Avg ADR: XX.XX
🏆 Winrate for last 20 matches: XX%
```

#### `/matches s1mple`

```
🎮 Last 10 Matches for s1mple:
1. [W] 🎯 K: XX | ⚔️ K/D: X.XX | 💀 K/R: X.XX | 💥 ADR: XX.XX
2. [L] 🎯 K: XX | ⚔️ K/D: X.XX | 💀 K/R: X.XX | 💥 ADR: XX.XX
...
```

## Project Structure

```
faceitbot/
├── app/
│   ├── config.py            # Environment-based configuration
│   ├── api/
│   │   └── faceit.py        # Async FACEIT API client w/ retry
│   ├── services/
│   │   └── stats.py         # Stats aggregation + Redis caching
│   └── bot/
│       └── handlers.py      # /start, /help, /stats, /matches handlers
├── main.py                  # Entry point
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

## Running Locally (without Docker)

```bash
pip install -r requirements.txt

# Make sure Redis is running on localhost:6379
# Update REDIS_URL in .env to redis://localhost:6379/0

python main.py
```

## License

MIT
