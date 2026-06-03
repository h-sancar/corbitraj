"""
Corbitraj — Bot motoru
server.py tarafından run_bot(broadcast) üzerinden çalıştırılır.
"""

import asyncio
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Awaitable, Callable, Optional

import ccxt.async_support as ccxt
from colorama import Fore, Style, init

import cex
import coin_identity
import config
import dex

init(autoreset=True)

BroadcastFn = Callable[[dict], Awaitable[None]]

# Paylaşılan exchange havuzu — hem bot hem /trade sayfası kullanır
_exchange_pool: dict[str, ccxt.Exchange] = {}


async def init_exchanges() -> None:
    global _exchange_pool
    import aiohttp
    from cex import _make_connector
    _exchange_pool = {}
    for name in config.EXCHANGES:
        ex = _make_exchange(name)
        if ex is not None:
            # DoH resolver'lı session — CCXT'nin tüm HTTP çağrıları buradan geçer
            ex.session = aiohttp.ClientSession(connector=_make_connector(limit=5))
            _exchange_pool[name] = ex


def get_exchange(name: str) -> Optional[ccxt.Exchange]:
    return _exchange_pool.get(name)


@dataclass
class Opportunity:
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    gross_spread_pct: float
    net_profit_pct: float
    estimated_profit_usdt: float
    found_at: str


def _make_exchange(name: str) -> Optional[ccxt.Exchange]:
    """CCXT'de yoksa None döndürür — fiyat tarama direkt HTTP ile yapılır."""
    ccxt_name = "bybit" if name == "bybiteu" else name
    cls = getattr(ccxt, ccxt_name, None)
    if cls is None:
        return None
    params = {k: v for k, v in config.EXCHANGES[name].items()
              if k not in ("maker_fee", "taker_fee")}
    options: dict = {"enableRateLimit": True, "options": {"defaultType": "spot"}, **params}
    # Bybit EU için ayrı domain
    if name == "bybiteu":
        options["urls"] = {
            "api": {
                "public":  "https://api.bybit.com",
                "private": "https://api.bybit.com",
            }
        }
    return cls(options)


def get_fee(name: str) -> float:
    if name in config.EXCHANGES:
        return config.EXCHANGES[name]["taker_fee"]
    return config.DEX_FEES.get(name, 0.003)


async def load_popular_symbols() -> list[str]:
    """Binance 24s hacmine göre sıralı USDT çiftlerini döndürür."""
    return await cex.load_popular_symbols()


def find_opportunity(
    symbol: str,
    asks: dict[str, float],   # borsa → en ucuz satış fiyatı (biz buradan alırız)
    bids: dict[str, float],   # borsa → en yüksek alım teklifi (biz buraya satarız)
    coin_map: dict[str, dict[str, str]] | None = None,
) -> Optional[Opportunity]:
    if not asks or not bids:
        return None

    buy_name  = min(asks, key=asks.__getitem__)
    sell_name = max(bids, key=bids.__getitem__)

    if buy_name == sell_name:
        return None

    # Farklı borsalarda aynı ticker farklı coini temsil ediyorsa atla
    if coin_map:
        base      = symbol.split("/")[0]
        sym_addrs = coin_map.get(base, {})
        if sym_addrs:
            known_addrs = set(sym_addrs.values())
            if len(known_addrs) > 1:
                # Belirsiz ticker: borsalar farklı contract gösteriyor
                # Her iki tarafın da adresi bilinmeli ve eşleşmeli, yoksa atla
                buy_addr  = sym_addrs.get(buy_name)
                sell_addr = sym_addrs.get(sell_name)
                if not (buy_addr and sell_addr and buy_addr == sell_addr):
                    return None

    buy_price  = asks[buy_name]
    sell_price = bids[sell_name]

    if sell_price <= buy_price:
        return None

    gross_spread_pct = (sell_price - buy_price) / buy_price * 100

    # Spread çok büyükse büyük ihtimalle farklı coin → atla
    if gross_spread_pct > config.MAX_SPREAD_PCT:
        return None

    buy_fee_pct = get_fee(buy_name) * 100
    sell_fee_pct = get_fee(sell_name) * 100
    net_profit_pct = gross_spread_pct - buy_fee_pct - sell_fee_pct

    if net_profit_pct < config.MIN_PROFIT_PCT:
        return None

    coins = config.TRADE_AMOUNT_USDT / buy_price
    estimated_profit_usdt = (
        coins * sell_price
        - config.TRADE_AMOUNT_USDT
        - config.TRADE_AMOUNT_USDT * (buy_fee_pct + sell_fee_pct) / 100
    )

    return Opportunity(
        symbol=symbol,
        buy_exchange=buy_name,
        sell_exchange=sell_name,
        buy_price=buy_price,
        sell_price=sell_price,
        gross_spread_pct=gross_spread_pct,
        net_profit_pct=net_profit_pct,
        estimated_profit_usdt=estimated_profit_usdt,
        found_at=datetime.now().strftime("%H:%M:%S"),
    )


async def execute_trade(
    exchanges: dict[str, ccxt.Exchange], opp: Opportunity
) -> None:
    if config.DRY_RUN:
        return

    if "@" in opp.buy_exchange or "@" in opp.sell_exchange:
        print(Fore.YELLOW + config.ui_text(
            f"[DEX] {opp.symbol} — on-chain imza gerekli, atlandı.",
            f"[DEX] {opp.symbol} — on-chain signature required, skipped.",
        ))
        return

    amount = config.TRADE_AMOUNT_USDT / opp.buy_price
    try:
        buy_order, sell_order = await asyncio.gather(
            exchanges[opp.buy_exchange].create_market_buy_order(opp.symbol, amount),
            exchanges[opp.sell_exchange].create_market_sell_order(opp.symbol, amount),
            return_exceptions=True,
        )
        if isinstance(buy_order, Exception):
            print(Fore.RED + config.ui_text(
                f"[HATA] Alış başarısız: {buy_order}",
                f"[ERROR] Buy failed: {buy_order}",
            ))
        if isinstance(sell_order, Exception):
            print(Fore.RED + config.ui_text(
                f"[HATA] Satış başarısız: {sell_order}",
                f"[ERROR] Sell failed: {sell_order}",
            ))
    except Exception as e:
        print(Fore.RED + config.ui_text(f"[HATA] {e}", f"[ERROR] {e}"))



async def scan_once(
    coin_map: dict[str, dict[str, str]] | None = None,
) -> tuple[list[Opportunity], dict[str, dict], list[tuple[str, list[str]]], list[str]]:
    """
    CEX (tüm pairler, hacim filtreli) + DEX fiyatlarını paralel çeker.
    Döner: (fırsat listesi, {kaynak: {ok, error}}, taranan_sembol_sayısı)
    """
    cex_all, dex_prices = await asyncio.gather(
        cex.fetch_all(list(config.EXCHANGES.keys())),
        dex.fetch_all_dex_prices(),
    )

    ex_status: dict[str, dict] = {}
    asks_by_sym:  dict[str, dict[str, float]] = {}
    bids_by_sym:  dict[str, dict[str, float]] = {}
    vols_by_sym:  dict[str, dict[str, float]] = {}
    scan_logs:    list[str] = []

    for ex_name, (asks, bids, vols, error) in cex_all.items():
        if error:
            msg = f"[CEX] {ex_name}: {error[:100]}"
            print(Fore.RED + msg + Style.RESET_ALL)
            scan_logs.append(msg)
        ex_status[ex_name] = {"ok": bool(asks), "error": error}
        for sym, p in asks.items():
            if config.is_pair_excluded(sym, ex_name):
                continue
            asks_by_sym.setdefault(sym, {})[ex_name] = p
        for sym, p in bids.items():
            if config.is_pair_excluded(sym, ex_name):
                continue
            bids_by_sym.setdefault(sym, {})[ex_name] = p
        for sym, v in vols.items():
            if config.is_pair_excluded(sym, ex_name):
                continue
            vols_by_sym.setdefault(sym, {})[ex_name] = v

    # DEX: spot fiyat hem ask hem bid olarak kullanılır
    for sym, dex_ex_prices in dex_prices.items():
        filtered_prices = {
            lbl: price
            for lbl, price in dex_ex_prices.items()
            if not config.is_pair_excluded(sym, lbl)
        }
        if not filtered_prices:
            continue
        asks_by_sym.setdefault(sym, {}).update(filtered_prices)
        bids_by_sym.setdefault(sym, {}).update(filtered_prices)
        for lbl in filtered_prices:
            ex_status[lbl] = {"ok": True, "error": ""}

    for chain_name, chain in config.CHAINS.items():
        for dex_name in chain["dexes"]:
            lbl = f"{dex_name}@{chain_name}"
            ex_status.setdefault(lbl, {"ok": False, "error": config.ui_text("veri yok", "no data")})

    min_vol = config.MIN_PAIR_VOLUME_USD
    opportunities:   list[Opportunity] = []
    scanned_symbols: list[tuple[str, list[str]]] = []

    for sym, sym_asks in asks_by_sym.items():
        sym_bids = bids_by_sym.get(sym, {})
        if len(sym_asks) < 2:
            continue

        if min_vol > 0:
            sym_vols = vols_by_sym.get(sym, {})
            sym_asks = {ex: p for ex, p in sym_asks.items()
                        if "@" in ex or sym_vols.get(ex, 0) >= min_vol}
            sym_bids = {ex: p for ex, p in sym_bids.items()
                        if "@" in ex or sym_vols.get(ex, 0) >= min_vol}
            if len(sym_asks) < 2:
                continue

        scanned_symbols.append((sym, sorted(sym_asks.keys())))
        opp = find_opportunity(sym, sym_asks, sym_bids, coin_map)
        if opp:
            opportunities.append(opp)

    scan_logs += dex.get_and_clear_logs()
    opportunities.sort(key=lambda o: o.net_profit_pct, reverse=True)
    # scanned_symbols: list of (sym, [exchanges])
    return opportunities, ex_status, scanned_symbols, scan_logs


async def run_bot(broadcast: BroadcastFn) -> None:
    """
    Ana döngü. server.py tarafından başlatılır.
    Tüm olaylar broadcast() ile WebSocket istemcilerine iletilir.
    """
    async def safe_broadcast(data: dict) -> None:
        try:
            await broadcast(data)
        except Exception:
            pass

    await safe_broadcast({
        "type": "status",
        "message": config.ui_text("Borsalara bağlanılıyor...", "Connecting to exchanges..."),
    })

    exchanges = _exchange_pool  # init_exchanges() tarafından doldurulur

    # Başlangıç durumu: bilinmiyor
    for name in config.EXCHANGES:
        await safe_broadcast({"type": "exchange_status", "exchange": name, "ok": False})
    for chain_name, chain in config.CHAINS.items():
        for dex_name in chain["dexes"]:
            await safe_broadcast({
                "type": "exchange_status",
                "exchange": f"{dex_name}@{chain_name}",
                "ok": False,
            })

    await safe_broadcast({
        "type": "status",
        "message": config.ui_text("İlk tarama başlıyor...", "Starting first scan..."),
    })

    start_time = time.time()
    scan_count = 0
    _coin_map: dict[str, dict[str, str]] = {}
    _identity_task: asyncio.Task | None = None
    _last_pairs_version = -1
    _last_pairs_signature: tuple[tuple[str, tuple[str, ...]], ...] = ()

    try:
        while True:
            scan_count += 1
            t0 = time.monotonic()
            opps, ex_status, sym_list, logs = await scan_once(_coin_map)
            scanned = len(sym_list)
            elapsed = time.monotonic() - t0
            pairs_signature = tuple(
                sorted((s, tuple(ex)) for s, ex in sym_list)
            )

            # İlk taramada ve hariç listesi değiştiğinde pair listesini UI'a güncelle
            if (
                scan_count == 1
                or config.EXCLUDED_PAIRS_VERSION != _last_pairs_version
                or pairs_signature != _last_pairs_signature
            ):
                if scan_count == 1:
                    print(Fore.CYAN + config.ui_text(
                        f"[Bot] {scanned} sembol tarandı.",
                        f"[Bot] {scanned} symbols scanned.",
                    ))
                pairs_payload = [{"s": s, "e": ex} for s, ex in sym_list]
                await safe_broadcast({
                    "type": "symbols_loaded",
                    "count": scanned,
                    "pairs": pairs_payload,
                })
                _last_pairs_version = config.EXCLUDED_PAIRS_VERSION
                _last_pairs_signature = pairs_signature
                if _identity_task is None:
                    active_bases = {s.split("/")[0] for s, _ in sym_list}
                    _identity_task = asyncio.create_task(
                        coin_identity.build_address_map(
                            active_bases, list(config.EXCHANGES.keys())
                        )
                    )

            # Arka plan kimlik görevi tamamlandıysa coin_map'i güncelle
            if _identity_task is not None and _identity_task.done():
                try:
                    _coin_map = _identity_task.result()
                    resolved = sum(len(v) for v in _coin_map.values())
                    msg = (
                        config.ui_text(
                            f"[CoinIdentity] {resolved} sembol/borsa sözleşme adresi alındı.",
                            f"[CoinIdentity] {resolved} symbol/exchange contract addresses resolved.",
                        )
                        if resolved else
                        config.ui_text(
                            "[CoinIdentity] Sözleşme adresi alınamadı.",
                            "[CoinIdentity] No contract addresses resolved.",
                        )
                    )
                    print(Fore.CYAN + msg)
                    logs.append(msg)
                except Exception as e:
                    logs.append(config.ui_text(
                        f"[CoinIdentity] Hata: {e}",
                        f"[CoinIdentity] Error: {e}",
                    ))
                _identity_task = None

            # Durum güncelle (ok + hata nedeni UI'a iletilir)
            for ex_name, st in ex_status.items():
                has_key = bool(config.EXCHANGES.get(ex_name, {}).get("apiKey", ""))
                await safe_broadcast({
                    "type": "exchange_status",
                    "exchange": ex_name,
                    "ok":      st["ok"],
                    "error":   st["error"],
                    "has_key": has_key,
                })

            # Fırsatlar önce → UI buffer'a girer, scan_complete sinyal olarak gelir
            for opp in opps:
                d = asdict(opp)
                d["type"] = "opportunity"
                d["scan_num"] = scan_count
                await safe_broadcast(d)
                buy_label = config.ui_text("AL", "BUY")
                sell_label = config.ui_text("SAT", "SELL")
                print(
                    Fore.GREEN
                    + f"[{opp.found_at}] {opp.symbol:12}"
                    + f"  {buy_label}:{opp.buy_exchange}@{opp.buy_price:.6f}"
                    + f"  {sell_label}:{opp.sell_exchange}@{opp.sell_price:.6f}"
                    + f"  NET:%{opp.net_profit_pct:.3f}"
                    + Style.RESET_ALL
                )
                await execute_trade(exchanges, opp)

            await safe_broadcast({
                "type": "scan_complete",
                "scan_count": scan_count,
                "elapsed": round(elapsed, 2),
                "opportunity_count": len(opps),
                "scanned": scanned,
                "uptime": round(time.time() - start_time, 1),
                "logs": logs,
            })

            print(config.ui_text(
                f"[Tarama #{scan_count}] {len(opps)} fırsat  |  {scanned} sembol  |  "
                f"{elapsed:.2f}s  |  {datetime.now().strftime('%H:%M:%S')}",
                f"[Scan #{scan_count}] {len(opps)} opportunities  |  {scanned} symbols  |  "
                f"{elapsed:.2f}s  |  {datetime.now().strftime('%H:%M:%S')}",
            ))

            await asyncio.sleep(max(0, config.SCAN_INTERVAL - elapsed))

    finally:
        await asyncio.gather(*[ex.close() for ex in exchanges.values()])
        print(Fore.CYAN + config.ui_text("Bağlantılar kapatıldı.", "Connections closed."))
