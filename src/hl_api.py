"""Hyperliquid info API — публичный, без авторизации.

Документация: POST https://api.hyperliquid.xyz/info с body {"type": "..."}
Используем 3 endpoint'а:
  - "metaAndAssetCtxs": метаданные universe + текущие mark/funding/OI
  - "candleSnapshot":   OHLC для расчёта RSI/EMA/ATR
"""
from __future__ import annotations

import time
from typing import Any

import requests

_BASE_URL = "https://api.hyperliquid.xyz/info"
_TIMEOUT = 30


class HyperliquidError(RuntimeError):
    pass


def _post(body: dict[str, Any]) -> Any:
    for attempt in range(3):
        try:
            r = requests.post(
                _BASE_URL,
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                raise HyperliquidError(f"HL info {body.get('type')}: {e}") from e
            time.sleep(2 ** (attempt + 1))


def fetch_meta_and_ctxs() -> dict[str, dict[str, Any]]:
    """Возвращает {symbol: {mark, mid, oracle, funding_apr, open_interest, day_volume}}.

    HL отдаёт funding как *часовой* rate (e.g. 0.0001 = 0.01% в час).
    Конвертируем в годовой APR: rate * 24 * 365 * 100.
    """
    data = _post({"type": "metaAndAssetCtxs"})
    if not (isinstance(data, list) and len(data) == 2):
        raise HyperliquidError(f"Неожиданный формат metaAndAssetCtxs: {type(data)}")

    universe = data[0].get("universe", [])
    ctxs = data[1] or []

    out: dict[str, dict[str, Any]] = {}
    for asset, ctx in zip(universe, ctxs):
        sym = asset.get("name")
        if not sym or not ctx:
            continue
        try:
            funding_hourly = float(ctx.get("funding") or 0.0)
        except (TypeError, ValueError):
            funding_hourly = 0.0
        out[sym] = {
            "mark": _maybe_float(ctx.get("markPx")),
            "mid": _maybe_float(ctx.get("midPx")),
            "oracle": _maybe_float(ctx.get("oraclePx")),
            "funding_hourly": funding_hourly,
            "funding_apr_pct": funding_hourly * 24 * 365 * 100,
            "open_interest": _maybe_float(ctx.get("openInterest")),
            "day_volume": _maybe_float(ctx.get("dayBaseVlm")),
            "max_leverage": asset.get("maxLeverage"),
            "sz_decimals": asset.get("szDecimals"),
        }
    return out


def fetch_candles(symbol: str, interval: str = "1d", lookback_days: int = 220) -> list[dict[str, Any]]:
    """OHLC свечи. interval: '1m','5m','15m','1h','4h','1d'.

    Возвращает список dict с ключами: t (epoch ms), o, h, l, c, v.
    """
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - lookback_days * 24 * 60 * 60 * 1000
    body = {
        "type": "candleSnapshot",
        "req": {
            "coin": symbol,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
        },
    }
    raw = _post(body) or []
    out = []
    for c in raw:
        try:
            out.append({
                "t": int(c["t"]),
                "o": float(c["o"]),
                "h": float(c["h"]),
                "l": float(c["l"]),
                "c": float(c["c"]),
                "v": float(c.get("v", 0)),
            })
        except (TypeError, ValueError, KeyError):
            continue
    return out


def resolve_symbol(requested: str, meta: dict[str, dict[str, Any]]) -> str | None:
    """Сопоставляет токен whitelist'а реальному символу на HL.

    HL для мемкоинов и низкоценовых альтов часто использует префиксы:
      PEPE → kPEPE (×1000)
      SHIB → kSHIB
      BONK → kBONK
    Возвращает имя или None если не найден.
    """
    if requested in meta:
        return requested
    # Префикс k- для масштабированных контрактов
    if f"k{requested}" in meta:
        return f"k{requested}"
    # Иногда префикс 1000-
    if f"1000{requested}" in meta:
        return f"1000{requested}"
    return None


def _maybe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
