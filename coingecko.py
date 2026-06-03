"""
CoinGecko üzerinden her borsa için {base_sembol: coin_id} map'i oluşturur.

Strateji (hafif — /coins/list indirmez):
  1. Aktif her sembol için /search?query={sym} → aynı ticker'a sahip coin listesi
  2. Birden fazla coin aynı ticker'ı paylaşıyorsa (belirsiz) her coin için
     /coins/{id}/tickers?exchange_ids=... ile hangi borsanın hangisini listelediği bulunur
  3. Sonuç 24 saat cache'lenir

Dönüş: {exchange_name: {base_symbol: coin_id}}
"""

import asyncio
import html
import json
import os
import re
import time

import aiohttp

import config
from cex import _make_connector, _SSL

_CACHE_FILE = ".coingecko_cache.json"
_CACHE_TTL  = 86_400  # 24 saat
_BASE       = "https://api.coingecko.com/api/v3"
_HEADERS    = {
    "Accept":     "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; corbitraj/1.0)",
}


# ── Yardımcı ──────────────────────────────────────────────────────────────────

async def _get(
    session: aiohttp.ClientSession,
    url: str,
    params: dict | None = None,
) -> dict | list | None:
    try:
        async with session.get(
            url, params=params,
            timeout=aiohttp.ClientTimeout(total=20),
            headers=_HEADERS,
            ssl=_SSL,
        ) as resp:
            if resp.status == 429:
                print(config.ui_text(
                    "[CoinGecko] Rate limit — 60s bekleniyor…",
                    "[CoinGecko] Rate limit — waiting 60s...",
                ))
                await asyncio.sleep(60)
                return None
            if resp.status != 200:
                print(f"[CoinGecko] HTTP {resp.status}: {url}")
                return None
            return json.loads(await resp.text())
    except Exception as e:
        print(config.ui_text(
            f"[CoinGecko] İstek hatası: {e}",
            f"[CoinGecko] Request error: {e}",
        ))
        return None


def _load_cache() -> dict:
    if os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_cache(data: dict) -> None:
    try:
        with open(_CACHE_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def _plain_text(value: str, limit: int = 700) -> str:
    value = re.sub(r"<br\s*/?>|</p>", "\n", value or "", flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return value
    return value[:limit].rsplit(" ", 1)[0].strip() + "..."


def _homepage(details: dict) -> str:
    for url in (details.get("links") or {}).get("homepage") or []:
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url
    return ""


async def _resolve_coin_id(
    session: aiohttp.ClientSession,
    base: str,
    exchange: str,
    candidates: list[dict],
    exchange_cg_ids: dict[str, str],
) -> str:
    cached_map = _load_cache().get("exchange_coin_map", {})
    cached_id = (cached_map.get(exchange) or {}).get(base)
    if cached_id:
        return cached_id

    cg_exchange_id = exchange_cg_ids.get(exchange)
    if cg_exchange_id and len(candidates) > 1:
        for coin in candidates[:8]:
            coin_id = coin.get("id", "")
            if not coin_id:
                continue
            tickers_data = await _get(
                session,
                f"{_BASE}/coins/{coin_id}/tickers",
                {"exchange_ids": cg_exchange_id},
            )
            if not tickers_data:
                await asyncio.sleep(0.4)
                continue
            for ticker in tickers_data.get("tickers", []):
                if ticker.get("base", "").upper() != base:
                    continue
                market_id = ticker.get("market", {}).get("identifier", "")
                if market_id == cg_exchange_id:
                    return coin_id
            await asyncio.sleep(0.4)

    return candidates[0].get("id", "")


async def get_coin_info(
    symbol: str,
    exchange: str = "",
    exchange_cg_ids: dict[str, str] | None = None,
) -> dict | None:
    base = (symbol.split("/")[0] if symbol else "").strip().upper()
    exchange = (exchange or "").strip().lower()
    if not base:
        return None

    cache = _load_cache()
    info_cache: dict = cache.get("coin_info", {})
    cache_key = f"{exchange}:{base}" if exchange else base
    cached = info_cache.get(cache_key)
    if cached and time.time() - cached.get("ts", 0) < _CACHE_TTL:
        return cached.get("data")

    async with aiohttp.ClientSession(connector=_make_connector(limit=3)) as session:
        search_data = await _get(session, f"{_BASE}/search", {"query": base})
        if not search_data:
            return None

        candidates = [
            c for c in search_data.get("coins", [])
            if c.get("symbol", "").upper() == base
        ]
        if not candidates:
            return None

        coin_id = await _resolve_coin_id(
            session,
            base,
            exchange,
            candidates,
            exchange_cg_ids or {},
        )
        if not coin_id:
            return None

        details = await _get(
            session,
            f"{_BASE}/coins/{coin_id}",
            {
                "localization": "false",
                "tickers": "false",
                "market_data": "false",
                "community_data": "false",
                "developer_data": "false",
                "sparkline": "false",
            },
        )
        if not isinstance(details, dict):
            return None

    data = {
        "id": details.get("id", coin_id),
        "symbol": base,
        "name": details.get("name") or base,
        "description": _plain_text((details.get("description") or {}).get("en", "")),
        "homepage": _homepage(details),
        "source_url": f"https://www.coingecko.com/en/coins/{coin_id}",
    }
    info_cache[cache_key] = {"ts": time.time(), "data": data}
    cache["coin_info"] = info_cache
    _save_cache(cache)
    return data


# ── Ana fonksiyon ──────────────────────────────────────────────────────────────

async def build_coin_map(
    active_bases: set[str],
    exchange_cg_ids: dict[str, str],  # {"binance": "binance", "kucoin": "kucoin", ...}
) -> dict[str, dict[str, str]]:
    """
    Döner: {exchange_name: {base_symbol: coin_id}}
    Sadece aynı ticker'ı paylaşan (belirsiz) semboller için exchange-level doğrulama yapılır.
    """
    cache = _load_cache()
    ex_map: dict[str, dict[str, str]] = cache.get("exchange_coin_map", {})
    ts: float = cache.get("exchange_coin_map_ts", 0)

    if ex_map and time.time() - ts < _CACHE_TTL:
        return ex_map

    # CoinGecko exchange ID → bot exchange adı (ters harita)
    cg_to_name: dict[str, str] = {v: k for k, v in exchange_cg_ids.items()}
    ex_ids_param = ",".join(set(exchange_cg_ids.values()))

    ex_map = {}
    updated = False

    async with aiohttp.ClientSession(connector=_make_connector(limit=3)) as session:
        for base in sorted(active_bases):
            # 1) Bu ticker'a sahip tüm coinleri bul
            data = await _get(session, f"{_BASE}/search", {"query": base})
            if not data:
                continue

            # Tam ticker eşleşmesi (büyük/küçük harf duyarsız)
            candidates = [
                c for c in data.get("coins", [])
                if c.get("symbol", "").upper() == base
            ]
            if len(candidates) <= 1:
                continue  # belirsizlik yok, atla

            print(config.ui_text(
                f"[CoinGecko] '{base}' için {len(candidates)} farklı coin — borsalar sorgulanıyor…",
                f"[CoinGecko] {len(candidates)} different coins for '{base}' — querying exchanges...",
            ))
            await asyncio.sleep(0.4)

            # 2) Her aday coin için ilgili borsaların ticker'larını çek
            for coin in candidates:
                coin_id = coin.get("id", "")
                if not coin_id:
                    continue
                tickers_data = await _get(
                    session,
                    f"{_BASE}/coins/{coin_id}/tickers",
                    {"exchange_ids": ex_ids_param},
                )
                if not tickers_data:
                    await asyncio.sleep(0.4)
                    continue
                for t in tickers_data.get("tickers", []):
                    if t.get("base", "").upper() != base:
                        continue
                    cg_ex_id = t.get("market", {}).get("identifier", "")
                    ex_name  = cg_to_name.get(cg_ex_id)
                    if ex_name and coin_id:
                        ex_map.setdefault(ex_name, {})[base] = coin_id
                updated = True
                await asyncio.sleep(0.4)

    if updated:
        cache["exchange_coin_map"]    = ex_map
        cache["exchange_coin_map_ts"] = time.time()
        _save_cache(cache)

    return ex_map
