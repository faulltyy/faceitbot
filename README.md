# FACEIT CS2 Stats Bot

A Telegram bot that fetches a player's CS2 statistics for their last 20 matches using the FACEIT Data API v4. Fully Dockerized with Redis caching and rate-limit protection.

## Features

- `/stats <nickname>` — look up any FACEIT player's recent CS2 performance
- **Smart caching** — individual match stats cached for 7 days, summary cached for 15 min
- **Rate-limit safe** — exponential backoff on 429 + concurrency semaphore (max 3 parallel requests)
- **Edge-case handling** — player not found, fewer than 20 matches, no CS2 data

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

Open Telegram, find your bot, and send:

```
/stats s1mple
```

Expected response:

```
📊 CS2 Stats for s1mple
🎯 Avg Kills: XX.XX
⚔️ Avg K/D: XX.XX
💀 Avg K/R: XX.XX
💥 Avg ADR: XX.XX
🏆 Winrate for last 20 matches: XX%
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
│       └── handlers.py      # /stats command handler
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
