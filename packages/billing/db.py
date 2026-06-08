from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from packages.billing.store import BillingStore

logger = logging.getLogger("ai_platform.billing.db")

_store_lock = threading.Lock()
_store: BillingStore | None = None
_store_url: str | None = None
_reachable_url: str | None | bool = False


def get_effective_database_url(database_url: str | None) -> str | None:
    global _reachable_url
    url = (database_url or "").strip()
    if not url:
        return None
    if _reachable_url is False:
        try:
            import psycopg

            with psycopg.connect(url, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            _reachable_url = url
            logger.info("postgres connected")
        except Exception as e:
            _reachable_url = None
            logger.warning("DATABASE_URL 不可达，跳过 token 计费: %s", e)
    return _reachable_url if isinstance(_reachable_url, str) else None


def get_billing_store(database_url: str | None) -> BillingStore | None:
    global _store, _store_url
    url = get_effective_database_url(database_url)
    if not url:
        return None
    with _store_lock:
        if _store is not None and _store_url == url:
            return _store
        from packages.billing.store import BillingStore

        _store = BillingStore(url)
        _store_url = url
        return _store


def reset_billing_store_for_tests() -> None:
    global _store, _store_url, _reachable_url
    with _store_lock:
        _store = None
        _store_url = None
        _reachable_url = False
