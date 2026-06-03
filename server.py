"""
Corbitraj — Web sunucusu
  http://localhost:8001        → Dashboard
  http://localhost:8001/trade  → Trading sayfası
  http://localhost:8001/pairs  → Pair listesi
"""

import asyncio
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse

import coingecko
import config
from bot import init_exchanges, get_exchange, run_bot
from cex import patch_socket_for_web3

app = FastAPI(title="Corbitraj")
_clients: set[WebSocket] = set()

# İlk taramadan gelen pair listesi — /pairs sayfası için saklanır
_pairs_data: list[dict] = []


def _is_local_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost"}


async def _broadcast(data: dict) -> None:
    global _pairs_data
    if data.get("type") == "symbols_loaded" and "pairs" in data:
        _pairs_data = data["pairs"]
    dead: set[WebSocket] = set()
    for ws in list(_clients):
        try:
            await ws.send_json(data)
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


def _read(name: str) -> str:
    return (Path(__file__).parent / "static" / name).read_text(encoding="utf-8")


# ── Sayfalar ─────────────────────────────────────────────────────────────────

@app.get("/nav.js")
async def nav_js() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "nav.js",
                        media_type="application/javascript")


@app.get("/")
async def dashboard() -> HTMLResponse:
    return HTMLResponse(_read("index.html"))


@app.get("/trade")
async def trade_page() -> HTMLResponse:
    return HTMLResponse(_read("trade.html"))


@app.get("/pairs")
async def pairs_page() -> HTMLResponse:
    return HTMLResponse(_read("pairs.html"))


@app.get("/api-keys")
async def api_page() -> HTMLResponse:
    return HTMLResponse(_read("api.html"))


@app.get("/wallet")
async def wallet_page() -> HTMLResponse:
    return HTMLResponse(_read("wallet.html"))


@app.get("/settings")
async def settings_page() -> HTMLResponse:
    return HTMLResponse(_read("settings.html"))


@app.get("/api/settings")
async def api_get_settings() -> JSONResponse:
    return JSONResponse({
        "DRY_RUN":              config.DRY_RUN,
        "LANGUAGE":             config.LANGUAGE,
        "SCAN_INTERVAL":        config.SCAN_INTERVAL,
        "MIN_PROFIT_PCT":       config.MIN_PROFIT_PCT,
        "MAX_SPREAD_PCT":       config.MAX_SPREAD_PCT,
        "MIN_PAIR_VOLUME_USD":  config.MIN_PAIR_VOLUME_USD,
        "TRADE_AMOUNT_USDT":    config.TRADE_AMOUNT_USDT,
        "MIN_DEX_LIQUIDITY_USD":config.MIN_DEX_LIQUIDITY_USD,
        "EXCLUDED_PAIRS":       config.EXCLUDED_PAIRS,
    })


@app.post("/api/settings")
async def api_set_settings(body: dict) -> JSONResponse:
    """Config değerlerini runtime'da günceller."""
    allowed = {
        "DRY_RUN": bool,
        "SCAN_INTERVAL": int,
        "MIN_PROFIT_PCT": float,
        "MAX_SPREAD_PCT": float,
        "MIN_PAIR_VOLUME_USD": int,
        "TRADE_AMOUNT_USDT": float,
        "MIN_DEX_LIQUIDITY_USD": int,
    }
    try:
        if "EXCLUDED_PAIRS" in body:
            config.set_excluded_pairs(body.get("EXCLUDED_PAIRS"))
        if "LANGUAGE" in body:
            lang = str(body.get("LANGUAGE", "")).lower()
            if lang not in ("tr", "en"):
                return JSONResponse({"error": config.ui_text("Geçersiz dil", "Invalid language")}, 400)
            config.LANGUAGE = lang
        for key, val in body.items():
            if key not in allowed:
                continue
            setattr(config, key, allowed[key](val))
        return JSONResponse({
            "ok": True,
            "LANGUAGE": config.LANGUAGE,
            "EXCLUDED_PAIRS": config.EXCLUDED_PAIRS,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, 400)


# ── REST API ──────────────────────────────────────────────────────────────────

_EX_DISPLAY = {
    'binance':'Binance','binancetr':'Binance TRY','bybit':'Bybit','bybiteu':'Bybit EU','okx':'OKX','bitget':'Bitget',
    'kraken':'Kraken',
    'kucoin':'KuCoin','gateio':'Gate.io','mexc':'MEXC','htx':'HTX',
    'cryptocom':'Crypto.com','whitebit':'WhiteBit','bingx':'BingX',
    'bitmart':'BitMart','poloniex':'Poloniex','bitfinex':'Bitfinex',
    'lbank':'LBank','upbit':'Upbit','bitstamp':'Bitstamp','gemini':'Gemini',
    'coinbase':'Coinbase','paribu':'Paribu','btcturk':'BtcTürk','bithumb':'Bithumb',
}

_DEX_DISPLAY = {
    "uniswap_v2": "Uniswap V2",
    "sushiswap": "SushiSwap",
    "pancakeswap_v2": "PancakeSwap V2",
    "biswap": "Biswap",
    "quickswap": "QuickSwap",
}


def _scan_source_display(label: str) -> str:
    if "@" not in label:
        return _EX_DISPLAY.get(label, label.upper())
    dex_name, chain_name = label.split("@", 1)
    return f"{_DEX_DISPLAY.get(dex_name, dex_name)} @ {chain_name}"


@app.get("/api/test-key")
async def api_test_key(exchange: str) -> JSONResponse:
    """Yüklü API key'ini gösterir (güvenli — sadece ilk 6 karakteri)."""
    ex = get_exchange(exchange)
    if not ex:
        return JSONResponse({"error": config.ui_text("Borsa bulunamadı", "Exchange not found")})
    key    = ex.apiKey or ""
    secret = ex.secret or ""
    return JSONResponse({
        "exchange":    exchange,
        "has_key":     bool(key),
        "key_preview": key[:6] + "…" + key[-4:] if len(key) > 10 else ("(boş)" if not key else key),
        "key_len":     len(key),
        "has_secret":  bool(secret),
        "secret_len":  len(secret),
    })


@app.get("/api/exchanges")
async def api_exchanges() -> JSONResponse:
    exs = [{"id": k, "name": _EX_DISPLAY.get(k, k.upper())}
           for k in config.EXCHANGES]
    return JSONResponse({"exchanges": exs})


@app.get("/api/scan-sources")
async def api_scan_sources() -> JSONResponse:
    sources = [
        {"id": k, "name": _EX_DISPLAY.get(k, k.upper())}
        for k in config.EXCHANGES
    ]
    for chain_name, chain in config.CHAINS.items():
        for dex_name in chain["dexes"]:
            label = f"{dex_name}@{chain_name}"
            sources.append({"id": label, "name": _scan_source_display(label)})
    return JSONResponse({"sources": sources})


@app.get("/api/pairs")
async def api_pairs() -> JSONResponse:
    return JSONResponse({"pairs": _pairs_data})


def _api_symbol(exchange: str, symbol: str) -> str:
    if exchange in ("binance", "htx", "lbank") and symbol == "SLEEPLESSAI/USDT":
        return "AI/USDT"
    return symbol


@app.get("/api/ticker")
async def api_ticker(exchange: str, symbol: str) -> JSONResponse:
    ex = get_exchange(exchange)
    if not ex:
        return JSONResponse({"error": config.ui_text("Borsa bulunamadı", "Exchange not found")}, 404)
    try:
        t = await ex.fetch_ticker(_api_symbol(exchange, symbol))
        return JSONResponse({
            "ask": t.get("ask"), "bid": t.get("bid"),
            "last": t.get("last"), "change": t.get("percentage"),
            "high": t.get("high"), "low": t.get("low"),
            "volume": t.get("quoteVolume"),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)


def _hmac(secret: str, msg: str) -> str:
    import hmac as _hmac_mod, hashlib
    return _hmac_mod.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


async def _direct_balance(url_template: str, api_key: str, secret: str,
                           parse_fn, extra_params: str = "") -> dict:
    """DoH session üzerinden HMAC imzalı balance çağrısı."""
    import time, aiohttp
    from cex import _make_connector, _SSL
    ts = int(time.time() * 1000)
    params = f"timestamp={ts}&recvWindow=60000{extra_params}"
    sig = _hmac(secret, params)
    url = f"{url_template}?{params}&signature={sig}"
    connector = _make_connector(limit=3)
    async with aiohttp.ClientSession(connector=connector) as sess:
        async with sess.get(url, headers={"X-MBX-APIKEY": api_key}, ssl=_SSL) as r:
            data = await r.json()
    return parse_fn(data)


def _safe_float(v) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _parse_binance(data: dict) -> dict:
    if "code" in data:
        raise ValueError(f"Binance {data.get('code')}: {data.get('msg','')}")
    return {b["asset"]: _safe_float(b.get("free"))
            for b in data.get("balances", [])
            if _safe_float(b.get("free")) > 0}


def _parse_mexc(data: dict) -> dict:
    if isinstance(data, dict) and "code" in data:
        raise ValueError(str(data))
    return {b["asset"]: _safe_float(b.get("free"))
            for b in (data.get("balances") or [])
            if _safe_float(b.get("free")) > 0}


async def _bybit_balance_direct(api_key: str, secret: str, base_url: str = "https://api.bybit.com") -> dict:
    """Bybit V5 — header tabanlı HMAC imzalama."""
    import time, aiohttp
    from cex import _make_connector, _SSL
    ts          = str(int(time.time() * 1000))
    recv_window = "60000"
    query       = "accountType=UNIFIED"
    sign_str    = ts + api_key + recv_window + query
    sig         = _hmac(secret, sign_str)
    url = f"{base_url}/v5/account/wallet-balance?{query}"
    headers = {
        "X-BAPI-API-KEY":      api_key,
        "X-BAPI-SIGN":         sig,
        "X-BAPI-TIMESTAMP":    ts,
        "X-BAPI-RECV-WINDOW":  recv_window,
    }
    connector = _make_connector(limit=3)
    async with aiohttp.ClientSession(connector=connector) as sess:
        async with sess.get(url, headers=headers, ssl=_SSL) as r:
            data = await r.json()
    if data.get("retCode") != 0:
        raise ValueError(f"Bybit {data.get('retCode')}: {data.get('retMsg','')}")
    free: dict[str, float] = {}
    for acct in data.get("result", {}).get("list", []):
        for coin in acct.get("coin", []):
            amt = _safe_float(coin.get("availableToWithdraw") or coin.get("availableToBorrow") or 0)
            if amt > 0:
                free[coin["coin"]] = amt
    return free


# Binance-tarzı (query-param + X-MBX-APIKEY) direkt implementasyon
_BINANCE_STYLE = {
    "binance":   ("https://api.binance.com/api/v3/account",  _parse_binance, ""),
    "binancetr": ("https://api.binance.com/api/v3/account",  _parse_binance, ""),
    "mexc":      ("https://api.mexc.com/api/v3/account",     _parse_mexc,    ""),
}


@app.get("/api/balance")
async def api_balance(exchange: str) -> JSONResponse:
    ex = get_exchange(exchange)
    if not ex:
        return JSONResponse({"error": config.ui_text("Borsa bulunamadı", "Exchange not found")}, 404)
    if not ex.apiKey:
        return JSONResponse({"error": config.ui_text(
            "API anahtarı girilmemiş — /api-keys sayfasından ekleyin.",
            "API key not entered - add it on the /api-keys page.",
        )}, 400)

    # Binance-tarzı direkt HTTP (DoH session) — CCXT DNS sorununu bypass eder
    if exchange in _BINANCE_STYLE:
        url, parse_fn, extra = _BINANCE_STYLE[exchange]
        try:
            free = await _direct_balance(url, ex.apiKey, ex.secret, parse_fn, extra)
            return JSONResponse({"free": free})
        except Exception as e:
            return JSONResponse({"error": str(e)}, 500)

    # Bybit / Bybit EU — header tabanlı imzalama
    if exchange in ("bybit", "bybiteu"):
        try:
            free = await _bybit_balance_direct(ex.apiKey, ex.secret)
            return JSONResponse({"free": free})
        except Exception as e:
            return JSONResponse({"error": str(e)}, 500)

    # Diğer borsalar: CCXT (DoH session ayarlı)
    try:
        b = await ex.fetch_balance()
        free = {k: v for k, v in (b.get("free") or {}).items() if v and v > 0}
        return JSONResponse({"free": free})
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)


@app.post("/api/order")
async def api_order(body: dict) -> JSONResponse:
    if config.DRY_RUN:
        return JSONResponse({"error": config.ui_text(
            "DRY_RUN aktif — config.py'de DRY_RUN = False yapın",
            "DRY_RUN is active - set DRY_RUN = False in config.py",
        )}, 400)
    exchange = body.get("exchange", "")
    symbol   = body.get("symbol", "")
    side     = body.get("side", "")       # "buy" | "sell"
    otype    = body.get("type", "market") # "market" | "limit"
    amount   = float(body.get("amount", 0))
    price    = body.get("price")          # limit için
    hidden   = bool(body.get("hidden", False))

    ex = get_exchange(exchange)
    if not ex:
        return JSONResponse({"error": config.ui_text("Borsa bulunamadı", "Exchange not found")}, 404)
    if amount <= 0:
        return JSONResponse({"error": config.ui_text("Geçersiz miktar", "Invalid amount")}, 400)
    try:
        extra = {"hidden": True, "displayQty": 0, "postOnly": True} if hidden else {}
        if otype == "market":
            order = await ex.create_market_order(symbol, side, amount, extra or None)
        else:
            order = await ex.create_limit_order(symbol, side, amount, float(price), extra or None)
        return JSONResponse(order)
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)


_OB_URLS: dict[str, str] = {
    "binance":   "https://api.binance.com/api/v3/depth?symbol={sym}&limit={limit}",
    "bybit":     "https://api.bybit.com/v5/market/orderbook?category=spot&symbol={sym}&limit={limit}",
    "bybiteu":   "https://api.bybit.com/v5/market/orderbook?category=spot&symbol={sym}&limit={limit}",
    "okx":       "https://www.okx.com/api/v5/market/books?instId={sym}&sz={limit}",
    "mexc":      "https://api.mexc.com/api/v3/depth?symbol={sym}&limit={limit}",
    "kucoin":    "https://api.kucoin.com/api/v1/market/orderbook/level2_{limit}?symbol={sym}",
    "gateio":    "https://api.gateio.ws/api/v4/spot/order_book?currency_pair={sym}&limit={limit}",
    "htx":       "https://api.htx.com/market/depth?symbol={sym}&type=step0&depth={limit}",
    "bitget":    "https://api.bitget.com/api/v2/spot/market/orderbook?symbol={sym}&limit={limit}",
    "whitebit":  "https://whitebit.com/api/v4/public/orderbook/{sym}?limit={limit}",
    "kraken":    "https://api.kraken.com/0/public/Depth?pair={sym}&count={limit}",
}


async def _fetch_ob_direct(exchange: str, symbol: str, limit: int) -> Optional[dict]:
    """DEX olmayan borsalar için public orderbook direkt çek (kimlik doğrulama yok)."""
    import aiohttp
    from cex import _make_connector, _SSL

    tpl = _OB_URLS.get(exchange)
    if not tpl:
        return None

    symbol = _api_symbol(exchange, symbol)

    # Sembol formatı borsaya göre uyarla
    raw = symbol.replace("/", "")               # BTC/USDT → BTCUSDT
    okx_sym = symbol.replace("/", "-")          # BTC/USDT → BTC-USDT
    kuc_sym = symbol.replace("/", "-")
    gate_sym = symbol.replace("/", "_")         # BTC/USDT → BTC_USDT
    htx_sym  = symbol.replace("/", "").lower()  # BTC/USDT → btcusdt
    wb_sym   = symbol.replace("/", "_")

    sym_map = {
        "binance": raw, "bybit": raw, "bybiteu": raw, "mexc": raw,
        "bitget": raw,
        "okx": okx_sym, "kucoin": kuc_sym, "gateio": gate_sym,
        "htx": htx_sym, "whitebit": wb_sym,
    }
    url = tpl.format(sym=sym_map.get(exchange, raw), limit=limit)

    try:
        connector = _make_connector(limit=3)
        async with aiohttp.ClientSession(connector=connector) as sess:
            async with sess.get(url, ssl=_SSL, timeout=aiohttp.ClientTimeout(total=6)) as r:
                data = await r.json(content_type=None)

        # Her borsanın response yapısını normalleştir
        asks, bids = [], []
        if exchange in ("binance", "mexc"):
            asks = [[float(p), float(a)] for p, a in data.get("asks", [])]
            bids = [[float(p), float(a)] for p, a in data.get("bids", [])]
        elif exchange in ("bybit", "bybiteu"):
            res = data.get("result", {})
            asks = [[float(p), float(a)] for p, a in res.get("a", [])]
            bids = [[float(p), float(a)] for p, a in res.get("b", [])]
        elif exchange == "okx":
            book = (data.get("data") or [{}])[0]
            asks = [[float(r[0]), float(r[1])] for r in book.get("asks", [])]
            bids = [[float(r[0]), float(r[1])] for r in book.get("bids", [])]
        elif exchange == "kucoin":
            d = data.get("data", {})
            asks = [[float(p), float(a)] for p, a in d.get("asks", [])]
            bids = [[float(p), float(a)] for p, a in d.get("bids", [])]
        elif exchange == "gateio":
            asks = [[float(p), float(a)] for p, a in data.get("asks", [])]
            bids = [[float(p), float(a)] for p, a in data.get("bids", [])]
        elif exchange == "htx":
            tick = data.get("tick", {})
            asks = [[float(r[0]), float(r[1])] for r in tick.get("asks", [])]
            bids = [[float(r[0]), float(r[1])] for r in tick.get("bids", [])]
        elif exchange == "bitget":
            d = data.get("data", {})
            asks = [[float(p), float(a)] for p, a in d.get("asks", [])]
            bids = [[float(p), float(a)] for p, a in d.get("bids", [])]
        elif exchange == "whitebit":
            asks = [[float(p), float(a)] for p, a in data.get("asks", [])]
            bids = [[float(p), float(a)] for p, a in data.get("bids", [])]
        elif exchange == "kraken":
            res = (data.get("result") or {})
            book = next(iter(res.values()), {}) if res else {}
            asks = [[float(r[0]), float(r[1])] for r in book.get("asks", [])]
            bids = [[float(r[0]), float(r[1])] for r in book.get("bids", [])]

        return {"asks": asks[:limit], "bids": bids[:limit]}
    except Exception:
        return None


@app.get("/api/orderbook")
async def api_orderbook(exchange: str, symbol: str, limit: int = 15) -> JSONResponse:
    # Önce direkt HTTP dene
    direct = await _fetch_ob_direct(exchange, symbol, limit)
    if direct is not None:
        return JSONResponse(direct)

    # Fallback: CCXT
    ex = get_exchange(exchange)
    if not ex:
        return JSONResponse({"asks": [], "bids": []})
    try:
        ob = await ex.fetch_order_book(_api_symbol(exchange, symbol), limit)
        return JSONResponse({
            "asks": ob.get("asks", [])[:limit],
            "bids": ob.get("bids", [])[:limit],
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)


@app.get("/api/orders")
async def api_open_orders(exchange: str, symbol: str) -> JSONResponse:
    ex = get_exchange(exchange)
    if not ex:
        return JSONResponse({"error": config.ui_text("Borsa bulunamadı", "Exchange not found")}, 404)
    if not ex.apiKey:
        return JSONResponse({"orders": []})
    try:
        orders = await ex.fetch_open_orders(symbol)
        return JSONResponse({"orders": orders})
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)


@app.get("/api/coin-info")
async def api_coin_info(exchange: str, symbol: str) -> JSONResponse:
    info = await coingecko.get_coin_info(
        symbol,
        exchange,
        config.COINGECKO_EXCHANGE_IDS,
    )
    if not info:
        return JSONResponse({"error": config.ui_text(
            "Coin açıklaması bulunamadı",
            "Coin description not found",
        )}, 404)
    return JSONResponse(info)


@app.post("/api/save-env")
async def api_save_env(request: Request, body: dict) -> JSONResponse:
    """Local-only helper: writes API keys to .env and updates in-memory CCXT objects."""
    if not _is_local_request(request):
        return JSONResponse({"error": config.ui_text(
            "Bu işlem yalnızca localhost üzerinden yapılabilir.",
            "This action is only allowed from localhost.",
        )}, 403)
    ex_name = body.get("exchange", "").lower()
    ex_id   = ex_name.upper()
    key     = body.get("key", "").strip()
    secret  = body.get("secret", "").strip()
    pp      = body.get("passphrase", "").strip()
    env_path = Path(__file__).parent / ".env"
    try:
        # Bellekteki CCXT nesnesi + session'ı anında güncelle
        ccxt_ex = get_exchange(ex_name)
        if ccxt_ex:
            if key:    ccxt_ex.apiKey   = key
            if secret: ccxt_ex.secret   = secret
            if pp:     ccxt_ex.password = pp
            # Markets cache'i temizle — yeni key ile yeniden yüklensin
            ccxt_ex.markets        = None
            ccxt_ex.markets_by_id  = None
        ex = ex_id  # .env yazımı için
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        updates = {
            f"{ex}_API_KEY": key,
            f"{ex}_SECRET":  secret,
        }
        if pp:
            updates[f"{ex}_PASSPHRASE"] = pp
        # Varolan satırları güncelle veya yenisini ekle
        updated_keys = set()
        new_lines = []
        for line in lines:
            if "=" in line and not line.startswith("#"):
                k = line.split("=", 1)[0].strip()
                if k in updates:
                    new_lines.append(f"{k}={updates[k]}")
                    updated_keys.add(k)
                    continue
            new_lines.append(line)
        for k, v in updates.items():
            if k not in updated_keys:
                new_lines.append(f"{k}={v}")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)


@app.post("/api/cancel")
async def api_cancel(body: dict) -> JSONResponse:
    ex = get_exchange(body.get("exchange", ""))
    if not ex:
        return JSONResponse({"error": config.ui_text("Borsa bulunamadı", "Exchange not found")}, 404)
    try:
        result = await ex.cancel_order(body["order_id"], body["symbol"])
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _clients.discard(ws)


# ── Başlangıç ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    await init_exchanges()
    await patch_socket_for_web3()
    asyncio.create_task(run_bot(_broadcast))


if __name__ == "__main__":
    uvicorn.run("server:app", host=config.HOST, port=config.PORT, reload=False)
