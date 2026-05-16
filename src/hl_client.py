"""Hyperliquid Info API client — read-only, no signing required.

All endpoints documented at
https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint

Address pitfall: HL is case-sensitive in some flows; we lowercase to be safe.
Agent wallets return empty — pass master/sub addresses only.
"""
from __future__ import annotations

import time
from typing import Optional

import requests

INFO_URL = "https://api.hyperliquid.xyz/info"
DEFAULT_TIMEOUT = 15
MIN_REQUEST_INTERVAL_SEC = 0.2  # gentle pacing; HL token bucket allows much more


class HLAPIError(Exception):
    """Raised on any HL Info API failure (HTTP error, bad JSON, etc)."""


class HLClient:
    """Stateless client; spotMeta is cached for the lifetime of the instance."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT, session: Optional[requests.Session] = None):
        self.timeout = timeout
        self._session = session or requests.Session()
        self._spot_meta_cache: Optional[dict] = None
        self._spot_name_map: Optional[dict[str, str]] = None
        self._last_request_ts: float = 0.0

    # ------------------------------------------------------------------ private

    def _post(self, payload: dict) -> dict | list:
        # gentle rate limiting between sequential calls
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < MIN_REQUEST_INTERVAL_SEC:
            time.sleep(MIN_REQUEST_INTERVAL_SEC - elapsed)

        try:
            resp = requests.post(INFO_URL, json=payload, timeout=self.timeout)
        except requests.RequestException as e:
            raise HLAPIError(f"network error: {e}") from e
        finally:
            self._last_request_ts = time.monotonic()

        if resp.status_code >= 400:
            raise HLAPIError(f"HTTP {resp.status_code}: {getattr(resp, 'text', '')[:200]}")

        try:
            return resp.json()
        except (ValueError, requests.JSONDecodeError) as e:
            raise HLAPIError(f"invalid JSON response: {e}") from e

    @staticmethod
    def _norm_addr(addr: str) -> str:
        if not isinstance(addr, str) or not addr.startswith("0x") or len(addr) != 42:
            raise ValueError(f"invalid address: {addr!r}")
        return addr.lower()

    # ------------------------------------------------------------------ public

    def get_clearinghouse_state(self, address: str) -> dict:
        """Perp account summary: marginSummary, assetPositions, time, withdrawable."""
        return self._post({"type": "clearinghouseState", "user": self._norm_addr(address)})

    def get_spot_clearinghouse_state(self, address: str) -> dict:
        """Spot balances: {balances: [{coin, token, total, hold, entryNtl}, ...]}."""
        return self._post({"type": "spotClearinghouseState", "user": self._norm_addr(address)})

    def get_user_fills(self, address: str) -> list:
        """Last ~2000 fills for the user."""
        return self._post({"type": "userFills", "user": self._norm_addr(address)})

    def get_frontend_open_orders(self, address: str) -> list:
        """Open orders with frontend-friendly metadata (orderType, triggerCondition, etc).

        Used by sl_visibility to detect stop-loss triggers on the user's
        active positions.
        """
        result = self._post({
            "type": "frontendOpenOrders",
            "user": self._norm_addr(address),
        })
        return result if isinstance(result, list) else []

    def get_user_fills_by_time(
        self,
        address: str,
        start_time_ms: int,
        end_time_ms: int | None = None,
    ) -> list:
        """Fills in a time window. Used for incremental whale tracking.

        HL returns at most 2000 fills per response. For 4h windows on a single
        whale this is comfortably under the limit.
        """
        payload = {
            "type": "userFillsByTime",
            "user": self._norm_addr(address),
            "startTime": int(start_time_ms),
        }
        if end_time_ms is not None:
            payload["endTime"] = int(end_time_ms)
        result = self._post(payload)
        return result if isinstance(result, list) else []

    def get_spot_meta(self) -> dict:
        """Cached spot universe + token table.

        Returned shape:
        {
          'tokens':   [{'name': 'HYPE', 'index': 150, ...}, ...],
          'universe': [{'name': '@107', 'tokens': [150, 0], 'index': 107, ...}, ...]
        }
        """
        if self._spot_meta_cache is None:
            self._spot_meta_cache = self._post({"type": "spotMeta"})
            self._build_spot_name_map()
        return self._spot_meta_cache

    def _build_spot_name_map(self) -> None:
        """Map raw spot 'coin' field ('@107', 'PURR/USDC') -> human token name."""
        assert self._spot_meta_cache is not None
        tokens_by_index: dict[int, str] = {
            int(t["index"]): t["name"] for t in self._spot_meta_cache.get("tokens", [])
        }
        name_map: dict[str, str] = {}
        for pair in self._spot_meta_cache.get("universe", []):
            pair_name = pair["name"]  # '@107' or 'PURR/USDC'
            # base token = first of two; second is usually USDC
            base_token_idx = pair["tokens"][0]
            base_name = tokens_by_index.get(int(base_token_idx))
            if base_name:
                name_map[pair_name] = base_name
        self._spot_name_map = name_map

    def resolve_spot_coin(self, symbol: str) -> str:
        """Resolve a raw spot 'coin' field ('@107', 'PURR/USDC') to token name ('HYPE').

        Unknown symbols pass through unchanged.
        """
        if self._spot_name_map is None:
            self.get_spot_meta()
        assert self._spot_name_map is not None
        return self._spot_name_map.get(symbol, symbol)
