import os

# Telegram
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

# FACEIT
FACEIT_API_KEY: str = os.getenv("FACEIT_API_KEY", "")
FACEIT_BASE_URL: str = "https://open.faceit.com/data/v4"

# Redis
REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Cache TTLs (seconds)
MATCH_CACHE_TTL: int = 7 * 86_400   # 7 days — match stats never change
SUMMARY_CACHE_TTL: int = 900         # 15 minutes — anti-spam for same nickname

# Limits
MAX_MATCHES: int = 30

# ELO heuristic (used when per-match ELO is not available from the API)
DEFAULT_ELO_DIFF: int = 25

# Rate-limit / retry
MAX_RETRIES: int = 5
RETRY_BASE_DELAY: float = 2.0        # seconds, doubled on each retry
API_CONCURRENCY: int = 5             # max simultaneous FACEIT API requests
