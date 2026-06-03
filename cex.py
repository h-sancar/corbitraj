"""
cex.py — Borsa fiyatlarını doğrudan HTTP ile çeker.
CCXT'nin load_markets() çağrısını tamamen atlar.

DNS çözümleme: UDP port 53 bloke olsa bile çalışır.
_DoHResolver — DNS over HTTPS (port 443 TCP) kullanır.
DoH sunucuları literal IP ile belirtilir → kendi DNS araması gerekmez.
"""

import asyncio
import socket
import ssl
from typing import Optional

import aiohttp
import aiohttp.abc
import certifi

import config

_SSL     = ssl.create_default_context(cafile=certifi.where())
_TIMEOUT = aiohttp.ClientTimeout(total=20)


# ── DoH Resolver ─────────────────────────────────────────────────────────────

class _DoHResolver(aiohttp.abc.AbstractResolver):
    """
    DNS over HTTPS resolver.
    DoH sunucuları literal IP — bağlanmak için DNS sorgusu gerekmez.
    Sonuçlar önbelleklenir: her hostname için sadece bir kez sorgu yapılır.
    """

    _SERVERS = [
        "https://1.1.1.1/dns-query",   # Cloudflare
        "https://8.8.8.8/dns-query",   # Google
        "https://9.9.9.9/dns-query",   # Quad9
    ]
    _cache: dict[str, list[str]] = {}

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[dict]:
        if host not in self._cache:
            ips = await self._lookup(host)
            if ips:
                self._cache[host] = ips

        ips = self._cache.get(host)
        if not ips:
            raise OSError(f"DoH ile çözümlenemedi: {host}")

        return [
            {"hostname": host, "host": ip, "port": port,
             "family": socket.AF_INET, "proto": 0, "flags": 0}
            for ip in ips
        ]

    async def _lookup(self, host: str) -> list[str]:
        """Her DoH sunucusunu dene; ilk başarılı sonucu döndür."""
        for server in self._SERVERS:
            try:
                # Bu connector sistem DNS'ini kullanır — ama server bir IP,
                # dolayısıyla DNS sorgusu yapılmaz.
                async with aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(),
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as sess:
                    async with sess.get(
                        server,
                        params={"name": host, "type": "A"},
                        headers={"Accept": "application/dns-json"},
                        ssl=_SSL,
                    ) as r:
                        data = await r.json(content_type=None)
                ips = [
                    a["data"] for a in data.get("Answer", [])
                    if a.get("type") == 1 and isinstance(a.get("data"), str)
                ]
                if ips:
                    return ips
            except Exception:
                continue
        return []

    async def close(self) -> None:
        pass


# Modül genelinde tek resolver örneği (önbellek paylaşımı için)
_DOH = _DoHResolver()


def _make_connector(limit: int = 10) -> aiohttp.TCPConnector:
    return aiohttp.TCPConnector(limit=limit, resolver=_DOH)


# ── Yardımcılar ───────────────────────────────────────────────────────────────

def _f(v) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


# Bilinen quote asset'ler, uzundan kısaya (yanlış ayrıştırmayı önler)
_QUOTES = sorted(
    ['FDUSD', 'TUSD', 'BUSD', 'USDT', 'USDC', 'BNB', 'ETH', 'BTC',
     'EUR', 'GBP', 'TRY', 'DAI', 'BIDR'],
    key=len, reverse=True,
)

def _sym(raw: str, sep: str = "") -> Optional[str]:
    """'BTCUSDT'→'BTC/USDT', 'BTC-ETH'→'BTC/ETH', 'BTC_BNB'→'BTC/BNB' vb."""
    if sep:
        a, found, b = raw.partition(sep)
        return f"{a}/{b}" if found and a and b else None
    for q in _QUOTES:
        if raw.endswith(q) and len(raw) > len(q):
            return f"{raw[:-len(q)]}/{q}"
    return None


def _alias_symbol(exchange: str, sym: str) -> str:
    if exchange in ("binance", "htx", "lbank") and sym == "AI/USDT":
        return "SLEEPLESSAI/USDT"
    return sym


async def _get(session: aiohttp.ClientSession, url: str):
    async with session.get(url, ssl=_SSL) as r:
        r.raise_for_status()
        return await r.json()


# ── Borsa fetcher'ları ────────────────────────────────────────────────────────

async def _binance(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    # ticker/24hr: bid/ask + hacim tek çağrıda
    data = await _get(s, "https://api.binance.com/api/v3/ticker/24hr")
    asks, bids, vols = {}, {}, {}
    for d in data:
        sym = _sym(d.get("symbol", ""))
        if not sym: continue
        sym = _alias_symbol("binance", sym)
        ask, bid = _f(d.get("askPrice")), _f(d.get("bidPrice"))
        vol = _f(d.get("quoteVolume"))
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol
    return "binance", asks, bids, vols


async def _binancetr(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    # Binance.com TRY çiftleri — btcturk/paribu ile TRY arbitrajı için
    data = await _get(s, "https://api.binance.com/api/v3/ticker/24hr")
    asks, bids, vols = {}, {}, {}
    for d in data:
        sym = _sym(d.get("symbol", ""))
        if not sym or not sym.endswith("/TRY"):
            continue
        ask, bid = _f(d.get("askPrice")), _f(d.get("bidPrice"))
        vol = _f(d.get("quoteVolume"))
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol
    return "binancetr", asks, bids, vols


async def _bybit(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    data = await _get(s, "https://api.bybit.com/v5/market/tickers?category=spot")
    asks, bids, vols = {}, {}, {}
    for t in data.get("result", {}).get("list", []):
        sym = _sym(t.get("symbol", ""))
        if not sym: continue
        ask, bid = _f(t.get("ask1Price")), _f(t.get("bid1Price"))
        vol = _f(t.get("turnover24h"))   # USDT cinsinden 24s hacim
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol
    return "bybit", asks, bids, vols


async def _bybiteu(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    # Bybit EU — aynı API endpoint'i, ayrı API key'leri
    data = await _get(s, "https://api.bybit.com/v5/market/tickers?category=spot")
    asks, bids, vols = {}, {}, {}
    for t in data.get("result", {}).get("list", []):
        sym = _sym(t.get("symbol", ""))
        if not sym: continue
        ask, bid, vol = _f(t.get("ask1Price")), _f(t.get("bid1Price")), _f(t.get("turnover24h"))
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol
    return "bybiteu", asks, bids, vols


async def _kucoin(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    data = await _get(s, "https://api.kucoin.com/api/v1/market/allTickers")
    asks, bids, vols = {}, {}, {}
    for t in data.get("data", {}).get("ticker", []):
        sym = _sym(t.get("symbol", ""), sep="-")
        if not sym: continue
        ask, bid = _f(t.get("sell")), _f(t.get("buy"))
        vol = _f(t.get("volValue"))      # USDT karşılığı hacim
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol
    return "kucoin", asks, bids, vols


async def _gateio(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    data = await _get(s, "https://api.gateio.ws/api/v4/spot/tickers")
    asks, bids, vols = {}, {}, {}
    for t in data:
        sym = _sym(t.get("currency_pair", ""), sep="_")
        if not sym: continue
        ask, bid = _f(t.get("lowest_ask")), _f(t.get("highest_bid"))
        vol = _f(t.get("quote_volume"))
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol
    return "gateio", asks, bids, vols


async def _mexc(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    data = await _get(s, "https://api.mexc.com/api/v3/ticker/24hr")
    asks, bids, vols = {}, {}, {}
    for d in data:
        sym = _sym(d.get("symbol", ""))
        if not sym: continue
        ask, bid = _f(d.get("askPrice")), _f(d.get("bidPrice"))
        vol = _f(d.get("quoteVolume"))
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol
    return "mexc", asks, bids, vols


async def _okx(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    data = await _get(s, "https://www.okx.com/api/v5/market/tickers?instType=SPOT")
    asks, bids, vols = {}, {}, {}
    for t in data.get("data", []):
        sym = _sym(t.get("instId", ""), sep="-")
        if not sym: continue
        ask, bid, vol = _f(t.get("askPx")), _f(t.get("bidPx")), _f(t.get("volCcy24h"))
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol
    return "okx", asks, bids, vols


async def _bitget(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    data = await _get(s, "https://api.bitget.com/api/v2/spot/market/tickers")
    asks, bids, vols = {}, {}, {}
    for t in data.get("data", []):
        sym = _sym(t.get("symbol", ""))
        if not sym: continue
        ask, bid, vol = _f(t.get("askPr")), _f(t.get("bidPr")), _f(t.get("usdtVolume"))
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol
    return "bitget", asks, bids, vols


async def _htx(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    data = await _get(s, "https://api.htx.com/market/tickers")
    asks, bids, vols = {}, {}, {}
    for t in data.get("data", []):
        sym = _sym(t.get("symbol", "").upper())
        if not sym: continue
        sym = _alias_symbol("htx", sym)
        ask, bid, vol = _f(t.get("ask")), _f(t.get("bid")), _f(t.get("amount"))
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol
    return "htx", asks, bids, vols


async def _whitebit(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    data = await _get(s, "https://whitebit.com/api/v4/public/ticker")
    asks, bids, vols = {}, {}, {}
    for raw_sym, t in data.items():
        sym = _sym(raw_sym, sep="_")
        if not sym: continue
        ask, bid, vol = _f(t.get("ask")), _f(t.get("bid")), _f(t.get("quote_volume"))
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol
    return "whitebit", asks, bids, vols


async def _bingx(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    data = await _get(s, "https://open-api.bingx.com/openApi/spot/v1/market/tickers")
    asks, bids, vols = {}, {}, {}
    for t in (data.get("data") or {}).get("tickers", []):
        sym = _sym(t.get("symbol", ""), sep="-")
        if not sym: continue
        ask, bid, vol = _f(t.get("askPrice")), _f(t.get("bidPrice")), _f(t.get("quoteVolume"))
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol
    return "bingx", asks, bids, vols


async def _cryptocom(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    data = await _get(s, "https://api.crypto.com/exchange/v1/public/get-tickers")
    asks, bids, vols = {}, {}, {}
    for t in (data.get("result") or {}).get("data", []):
        sym = _sym(t.get("i", ""), sep="_")
        if not sym: continue
        ask, bid, vol = _f(t.get("a")), _f(t.get("b")), _f(t.get("vv"))
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol
    return "cryptocom", asks, bids, vols


async def _bitmart(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    data = await _get(s, "https://api-cloud.bitmart.com/spot/v2/ticker")
    asks, bids, vols = {}, {}, {}
    for t in (data.get("data") or {}).get("tickers", []):
        sym = _sym(t.get("symbol", ""), sep="_")
        if not sym: continue
        ask, bid, vol = _f(t.get("best_ask")), _f(t.get("best_bid")), _f(t.get("quote_volume_24h"))
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol
    return "bitmart", asks, bids, vols


async def _poloniex(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    data = await _get(s, "https://api.poloniex.com/markets/ticker24h")
    asks, bids, vols = {}, {}, {}
    for t in data:
        sym = _sym(t.get("symbol", ""), sep="_")
        if not sym: continue
        ask, bid, vol = _f(t.get("ask")), _f(t.get("bid")), _f(t.get("amount"))
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol
    return "poloniex", asks, bids, vols


async def _bitfinex(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    # Format: [SYMBOL, BID, BID_SIZE, ASK, ASK_SIZE, ..., VOLUME, ...]
    # tBTCUST = BTC/USDT, tBTCUSD = BTC/USD
    data = await _get(s, "https://api-pub.bitfinex.com/v2/tickers?symbols=ALL")
    asks, bids, vols = {}, {}, {}
    for row in data:
        if not isinstance(row, list) or len(row) < 8: continue
        raw = str(row[0])
        if not raw.startswith("t"): continue
        code = raw[1:]
        # Map UST → USDT, USD → USDT (for comparison)
        if code.endswith("UST"):
            sym = code[:-3] + "/USDT"
        elif code.endswith("USD") and len(code) > 3:
            sym = code[:-3] + "/USDT"
        else:
            continue
        bid, ask, vol = _f(row[1]), _f(row[3]), _f(row[8])
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol * ask  # base vol → quote vol estimate
    return "bitfinex", asks, bids, vols


async def _lbank(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    data = await _get(s, "https://api.lbank.info/v2/ticker/24hr.do?symbol=all")
    asks, bids, vols = {}, {}, {}
    for item in data.get("data", []):
        raw = item.get("symbol", "").upper()
        sym = _sym(raw, sep="_")
        if not sym: continue
        sym = _alias_symbol("lbank", sym)
        t = item.get("ticker", {})
        # LBank only provides last price, no separate bid/ask
        last = _f(t.get("latest"))
        vol  = _f(t.get("vol"))
        if last > 0:
            asks[sym] = last
            bids[sym] = last
        if vol > 0: vols[sym] = vol * last  # base → quote estimate
    return "lbank", asks, bids, vols


async def _upbit(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    # Adım 1: USDT piyasalarını al
    markets_data = await _get(s, "https://api.upbit.com/v1/market/all?isDetails=false")
    usdt_markets = [m["market"] for m in markets_data if m["market"].startswith("USDT-")]
    if not usdt_markets:
        return "upbit", {}, {}, {}
    # Adım 2: Toplu ticker (100 limit)
    mstr = ",".join(usdt_markets[:150])
    data = await _get(s, f"https://api.upbit.com/v1/ticker?markets={mstr}")
    asks, bids, vols = {}, {}, {}
    for t in data:
        # market: "USDT-BTC" → "BTC/USDT"
        raw = t.get("market", "")  # "USDT-BTC"
        parts = raw.split("-")
        if len(parts) != 2: continue
        sym = f"{parts[1]}/{parts[0]}"  # BTC/USDT
        last = _f(t.get("trade_price"))
        vol  = _f(t.get("acc_trade_price_24h"))
        if last > 0:
            asks[sym] = last
            bids[sym] = last
        if vol > 0: vols[sym] = vol
    return "upbit", asks, bids, vols


async def _bitstamp(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    # Bitstamp: tek tek ticker çekmek gerekiyor; en likit USDT çiftleri paralel
    _BITSTAMP_PAIRS = [
        "btcusdt","ethusdt","xrpusdt","ltcusdt","linkusdt","adausdt",
        "dogeusdt","solusdt","uniusdt","avaxusdt","maticusdt","dotusdt",
    ]
    results = await asyncio.gather(
        *[_get(s, f"https://www.bitstamp.net/api/v2/ticker/{p}/") for p in _BITSTAMP_PAIRS],
        return_exceptions=True
    )
    asks, bids, vols = {}, {}, {}
    for pair, r in zip(_BITSTAMP_PAIRS, results):
        if isinstance(r, Exception) or not isinstance(r, dict): continue
        sym = _sym(pair.upper())
        if not sym: continue
        ask, bid, vol = _f(r.get("ask")), _f(r.get("bid")), _f(r.get("volume"))
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol * ask
    return "bitstamp", asks, bids, vols


async def _gemini(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    data = await _get(s, "https://api.gemini.com/v1/pricefeed")
    asks, bids, vols = {}, {}, {}
    for t in data:
        raw = t.get("pair", "")
        # "BTCUSD" → _sym → "BTC/USDT"
        sym = _sym(raw)
        if not sym: continue
        last = _f(t.get("price"))
        if last > 0:
            asks[sym] = last
            bids[sym] = last
    return "gemini", asks, bids, vols


async def _coinbase(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    # Coinbase Exchange (Pro) — USDT çiftleri paralel ticker
    _CB_PAIRS = [
        "BTC-USDT","ETH-USDT","SOL-USDT","XRP-USDT","ADA-USDT",
        "DOGE-USDT","AVAX-USDT","MATIC-USDT","LINK-USDT","DOT-USDT",
        "LTC-USDT","UNI-USDT","BCH-USDT","ATOM-USDT","FIL-USDT",
    ]
    results = await asyncio.gather(
        *[_get(s, f"https://api.exchange.coinbase.com/products/{p}/ticker") for p in _CB_PAIRS],
        return_exceptions=True
    )
    asks, bids, vols = {}, {}, {}
    for pair, r in zip(_CB_PAIRS, results):
        if isinstance(r, Exception): continue
        sym = _sym(pair.replace("-", ""))
        if not sym: continue
        ask, bid, vol = _f(r.get("ask")), _f(r.get("bid")), _f(r.get("volume"))
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol * ask
    return "coinbase", asks, bids, vols


async def _paribu(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    # Türk borsası — TRY çiftleri
    data = await _get(s, "https://www.paribu.com/ticker")
    asks, bids, vols = {}, {}, {}
    for raw_sym, t in data.items():
        # "BTC_TL" → "BTC/TRY"
        sym = _sym(raw_sym, sep="_")
        if not sym: continue
        last = _f(t.get("current") or t.get("last"))
        vol  = _f(t.get("volume"))
        if last > 0:
            asks[sym] = last
            bids[sym] = last
        if vol > 0: vols[sym] = vol
    return "paribu", asks, bids, vols


async def _btcturk(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    # Türk borsası — TRY ve USDT çiftleri
    data = await _get(s, "https://api.btcturk.com/api/v2/ticker")
    asks, bids, vols = {}, {}, {}
    for t in data.get("data", []):
        num = t.get("numeratorSymbol", "")
        den = t.get("denominatorSymbol", "")
        if not num or not den: continue
        sym = f"{num}/{den}"
        ask, bid, vol = _f(t.get("ask")), _f(t.get("bid")), _f(t.get("volume"))
        if ask > 0: asks[sym] = ask
        if bid > 0: bids[sym] = bid
        if vol > 0: vols[sym] = vol
    return "btcturk", asks, bids, vols


async def _kraken(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    # Adım 1: Tüm pair listesini al, USDT olanları filtrele
    pr = await _get(s, "https://api.kraken.com/0/public/AssetPairs")
    pairs_info = pr.get("result", {})
    usdt_pairs = [k for k in pairs_info if k.upper().endswith("USDT")]
    if not usdt_pairs:
        return "kraken", {}, {}, {}

    # Adım 2: 50'şer grupta ticker çek
    asks, bids, vols = {}, {}, {}
    for i in range(0, len(usdt_pairs), 50):
        batch = ",".join(usdt_pairs[i:i + 50])
        tr = await _get(s, f"https://api.kraken.com/0/public/Ticker?pair={batch}")
        for code, t in (tr.get("result") or {}).items():
            # wsname: "XBT/USDT" → BTC/USDT
            ws = pairs_info.get(code, {}).get("wsname", "")
            if not ws or "/" not in ws:
                continue
            base, quote = ws.split("/")
            base = base.replace("XBT", "BTC")  # Kraken XBT → BTC
            sym  = f"{base}/{quote}"
            ask = _f((t.get("a") or ["0"])[0])
            bid = _f((t.get("b") or ["0"])[0])
            vol = _f((t.get("v") or [0, 0])[1]) * ask  # 24h base vol × fiyat ≈ USDT
            if ask > 0: asks[sym] = ask
            if bid > 0: bids[sym] = bid
            if vol > 0: vols[sym] = vol
    return "kraken", asks, bids, vols


async def _bithumb(s: aiohttp.ClientSession) -> tuple[str, dict, dict, dict]:
    # Kore borsası — KRW çiftleri
    data = await _get(s, "https://api.bithumb.com/public/ticker/ALL_KRW")
    asks, bids, vols = {}, {}, {}
    for coin, t in (data.get("data") or {}).items():
        if coin == "date": continue
        sym = f"{coin}/KRW"
        last = _f(t.get("closing_price"))
        vol  = _f(t.get("acc_trade_value_24H"))
        if last > 0:
            asks[sym] = last
            bids[sym] = last
        if vol > 0: vols[sym] = vol
    return "bithumb", asks, bids, vols


_FETCHERS = {
    # Tier 1
    "binance":   _binance,
    "binancetr": _binancetr,
    "bybit":     _bybit,
    "bybiteu":   _bybiteu,
    "okx":       _okx,
    "bitget":    _bitget,
    "kucoin":    _kucoin,
    "gateio":    _gateio,
    "mexc":      _mexc,
    # Tier 2
    "htx":       _htx,
    "cryptocom": _cryptocom,
    "whitebit":  _whitebit,
    "bingx":     _bingx,
    "bitmart":   _bitmart,
    "poloniex":  _poloniex,
    "bitfinex":  _bitfinex,
    "lbank":     _lbank,
    "upbit":     _upbit,
    "kraken":    _kraken,
    # Tier 3
    "bitstamp":  _bitstamp,
    "gemini":    _gemini,
    "coinbase":  _coinbase,
    "paribu":    _paribu,
    "btcturk":   _btcturk,
    "bithumb":   _bithumb,
}


# ── Public API ────────────────────────────────────────────────────────────────

async def fetch_all(
    exchange_names: list[str],
) -> dict[str, tuple[dict[str, float], str]]:
    """
    Tüm CEX borsalarından paralel fiyat çeker.
    Döner: {borsa_adı: (fiyat_dict, hata_str)}
    """
    async with aiohttp.ClientSession(
        connector=_make_connector(limit=10), timeout=_TIMEOUT
    ) as session:
        tasks = {
            name: _FETCHERS[name](session)
            for name in exchange_names
            if name in _FETCHERS
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    out: dict[str, tuple[dict, dict, dict, str]] = {}
    for name, result in zip(tasks.keys(), results):
        if isinstance(result, tuple):
            _, asks, bids, vols = result
            out[name] = (asks, bids, vols, "")
        else:
            err = f"{type(result).__name__}: {str(result)[:120]}"
            out[name] = ({}, {}, {}, err)
    return out


async def load_popular_symbols() -> list[str]:
    """
    Binance 24s ticker'dan USDT çiftlerini hacme göre sıralı çeker.
    """
    try:
        async with aiohttp.ClientSession(
            connector=_make_connector(limit=5), timeout=_TIMEOUT
        ) as session:
            data = await _get(session, "https://api.binance.com/api/v3/ticker/24hr")
        pairs: list[tuple[str, float]] = []
        for t in data:
            sym = _sym(t.get("symbol", ""))
            vol = _f(t.get("quoteVolume"))
            if sym and vol > 0:
                pairs.append((sym, vol))
        pairs.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in pairs]
    except Exception:
        return config.SYMBOLS


async def patch_socket_for_web3() -> None:
    """
    socket.getaddrinfo'yu DoH ile yamalar.
    DEX RPC host'ları + tüm borsa API host'ları kapsanır →
    CCXT (requests/httpx) da DoH üzerinden çözümleme yapar.
    SSL, orijinal hostname üzerinden doğrulanmaya devam eder.
    """
    from urllib.parse import urlparse

    # DEX RPC host'ları
    rpc_hosts: set[str] = set()
    for chain in config.CHAINS.values():
        rpc = chain.get("rpc", [])
        for r in ([rpc] if isinstance(rpc, str) else rpc):
            h = urlparse(r).hostname
            if h:
                rpc_hosts.add(h)

    # CCXT'nin kullandığı borsa API host'ları
    rpc_hosts.update({
        "api.binance.com", "api.bybit.com", "api.kucoin.com",
        "api.gateio.ws", "api.mexc.com", "www.okx.com",
        "api.bitget.com", "api.htx.com", "api.crypto.com",
        "whitebit.com", "open-api.bingx.com", "api-cloud.bitmart.com",
        "api.poloniex.com", "api-pub.bitfinex.com", "api.lbank.com",
        "api.upbit.com", "www.bitstamp.net", "api.gemini.com",
        "api.exchange.coinbase.com", "www.paribu.com",
        "api.btcturk.com", "api.bithumb.com", "api.kraken.com",
        "api.lbank.info", "whitebit.com",
    })
    hosts = list(rpc_hosts)

    ip_map: dict[str, str] = {}
    resolve_tasks = [_DOH.resolve(h) for h in hosts]
    results = await asyncio.gather(*resolve_tasks, return_exceptions=True)
    for host, result in zip(hosts, results):
        if not isinstance(result, Exception) and result:
            ip_map[host] = result[0]["host"]

    if not ip_map:
        return

    _orig = socket.getaddrinfo

    def _patched(h, port, *args, **kwargs):
        return _orig(ip_map.get(h, h), port, *args, **kwargs)

    socket.getaddrinfo = _patched
