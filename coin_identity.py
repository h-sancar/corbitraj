"""
Exchange API'lerinden coin sözleşme adresi çeker ve karşılaştırır.
Aynı sembol için iki borsada farklı adres varsa → farklı coin.

Desteklenen borsalar (public endpoint):
  - kucoin  : /api/v2/currencies/{symbol}
  - gateio  : /api/v4/spot/currencies/{symbol}
  - lbank   : /v2/supplement/withdrawConfig.do
  - binance : /sapi/v1/capital/config/getall  (API key gerekir)
"""

import asyncio
import json
import os
import time

import aiohttp

import config

_CACHE_FILE = ".coin_identity_cache.json"
_CACHE_TTL  = 86_400  # 24 saat


# ── Exchange başına adres çekici ───────────────────────────────────────────────

async def _kucoin(session: aiohttp.ClientSession, symbol: str) -> str | None:
    try:
        async with session.get(
            f"https://api.kucoin.com/api/v2/currencies/{symbol}",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            data = (await resp.json()).get("data", {})
            for chain in data.get("chains", []):
                addr = chain.get("contractAddress", "")
                if addr and addr not in ("", "0x0000000000000000000000000000000000000000"):
                    return addr.lower()
    except Exception:
        pass
    return None


async def _gateio(session: aiohttp.ClientSession, symbol: str) -> str | None:
    try:
        async with session.get(
            f"https://api.gateio.ws/api/v4/spot/currencies/{symbol}",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data.get("contract_address", "").lower() or None
    except Exception:
        pass
    return None


async def _lbank(session: aiohttp.ClientSession, symbol: str) -> str | None:
    try:
        async with session.get(
            "https://api.lbank.info/v2/supplement/withdrawConfig.do",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            for item in data.get("data", []):
                if item.get("assetCode", "").upper() == symbol.upper():
                    for net in item.get("networkList", []) or [item]:
                        addr = net.get("contractAddress", "")
                        if addr and addr not in ("", "0x0000000000000000000000000000000000000000"):
                            return addr.lower()
    except Exception:
        pass
    return None


async def _binance(session: aiohttp.ClientSession, symbol: str) -> str | None:
    cfg = config.EXCHANGES.get("binance", {})
    api_key = cfg.get("apiKey", "")
    if not api_key:
        return None
    try:
        async with session.get(
            "https://api.binance.com/sapi/v1/capital/config/getall",
            headers={"X-MBX-APIKEY": api_key},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            coins = await resp.json()
            for coin in coins:
                if coin.get("coin", "").upper() == symbol.upper():
                    for net in coin.get("networkList", []):
                        addr = net.get("contractAddressUrl", "") or net.get("contractAddress", "")
                        if addr and "0x" in addr:
                            return addr.lower().split("/")[-1]  # URL içindeyse son parça
    except Exception:
        pass
    return None


_FETCHERS: dict[str, callable] = {
    "kucoin":  _kucoin,
    "gateio":  _gateio,
    "lbank":   _lbank,
    "binance": _binance,
}


# ── Cache yardımcıları ─────────────────────────────────────────────────────────

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


# ── Ana fonksiyon ──────────────────────────────────────────────────────────────

async def build_address_map(
    active_bases: set[str],
    exchanges: list[str],
) -> dict[str, dict[str, str]]:
    """
    Döner: {base_symbol: {exchange_name: contract_address}}
    Yalnızca public/auth API'si olan borsalar için veri gelir.
    Diğerleri için anahtar bulunmaz → o borsalar kontrol dışı tutulur (fırsat kaçırılmaz).
    """
    cache = _load_cache()
    address_map: dict[str, dict[str, str]] = cache.get("address_map", {})
    ts: float = cache.get("ts", 0)

    if address_map and time.time() - ts < _CACHE_TTL:
        return address_map

    address_map = {}
    async with aiohttp.ClientSession() as session:
        for sym in sorted(active_bases):
            sym_addrs: dict[str, str] = {}
            for ex_name in exchanges:
                fetcher = _FETCHERS.get(ex_name)
                if fetcher is None:
                    continue
                addr = await fetcher(session, sym)
                if addr:
                    sym_addrs[ex_name] = addr
                    print(f"[CoinIdentity] {ex_name} {sym} → {addr[:20]}…")
                await asyncio.sleep(0.15)
            if sym_addrs:
                address_map[sym] = sym_addrs

    cache["address_map"] = address_map
    cache["ts"] = time.time()
    _save_cache(cache)
    return address_map
