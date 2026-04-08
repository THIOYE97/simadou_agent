"""
Shared HTTP client with automatic retry and exponential backoff.
All fetchers use this instead of raw httpx.get().
"""
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Retry config
MAX_RETRIES = 4
BACKOFF_BASE = 2          # seconds  → 2, 4, 8, 16
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


def fetch_bytes(url: str, timeout: int = 120, source: str = "") -> Optional[bytes]:
    """
    Download *url* and return raw bytes.
    Retries up to MAX_RETRIES times with exponential backoff.
    Returns None if all attempts fail.
    """
    tag = f"[{source}]" if source else ""
    attempt = 0

    while attempt <= MAX_RETRIES:
        try:
            logger.debug("%s GET %s (attempt %d)", tag, url, attempt + 1)
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.get(url)

            if resp.status_code in RETRY_STATUS_CODES:
                raise httpx.HTTPStatusError(
                    f"Retryable HTTP {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )

            resp.raise_for_status()
            logger.info("%s downloaded %s bytes from %s", tag, len(resp.content), url)
            return resp.content

        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as exc:
            wait = BACKOFF_BASE ** attempt
            attempt += 1
            if attempt > MAX_RETRIES:
                logger.error("%s all %d attempts failed for %s: %s", tag, MAX_RETRIES + 1, url, exc)
                return None
            logger.warning("%s attempt %d failed (%s) – retrying in %ds …", tag, attempt, exc, wait)
            time.sleep(wait)

        except Exception as exc:
            logger.error("%s unexpected error fetching %s: %s", tag, url, exc)
            return None

    return None
