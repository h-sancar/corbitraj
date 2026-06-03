"""
DEX fiyat modülü — Uniswap V2 tarzı pool'lardan fiyat çeker.

Doğrulama zinciri (hepsi geçmeli, aksi hâlde None):
  1. Pair var mı?         → factory.getPair() != zero address
  2. 24 saat tazelik?     → getReserves().blockTimestampLast < 86400 saniye önce
  3. Yeterli likidite?    → quote rezervi >= MIN_DEX_LIQUIDITY_USD / 2 (USDT)
  4. Fiyat               → rezerv oranından spot fiyat (ücret hariç; filtre için yeterince yakın)

Pair adresi ve token sırası ilk çağrıda önbelleğe alınır →
sonraki taramalarda sadece getReserves() çağrılır (1 RPC/pair).
"""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from web3 import Web3

import config

# ── ABI'ler ──────────────────────────────────────────────────────────────────

_FACTORY_ABI = [
    {
        "inputs": [
            {"type": "address", "name": "tokenA"},
            {"type": "address", "name": "tokenB"},
        ],
        "name": "getPair",
        "outputs": [{"type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

_PAIR_ABI = [
    {
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"type": "uint112", "name": "reserve0"},
            {"type": "uint112", "name": "reserve1"},
            {"type": "uint32",  "name": "blockTimestampLast"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

_ZERO_ADDR = "0x" + "0" * 40

# ── Önbellekler ───────────────────────────────────────────────────────────────

_w3_cache:   dict[str, Web3] = {}
_rpc_index:  dict[str, int]  = {}   # zincir → aktif RPC indeksi
_scan_logs:  list[str]       = []   # tarama başına log tamponu


def get_and_clear_logs() -> list[str]:
    global _scan_logs
    logs, _scan_logs = _scan_logs, []
    return logs

# (chain, factory_addr, token_in_lower, token_out_lower)
#   → (pair_addr_cs, token_in_is_token0: bool)
#   → None  eğer pair hiç yoksa
_pair_cache: dict[tuple, Optional[tuple[str, bool]]] = {}

_executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="dex")

# Aynı RPC endpoint'e eş zamanlı en fazla 3 istek — 429 önlemi
_chain_sem: dict[str, threading.Semaphore] = {}

def _get_sem(chain_name: str) -> threading.Semaphore:
    if chain_name not in _chain_sem:
        _chain_sem[chain_name] = threading.Semaphore(3)
    return _chain_sem[chain_name]

# ── Yardımcılar ───────────────────────────────────────────────────────────────

def _current_rpc(chain_name: str) -> str:
    rpcs = config.CHAINS[chain_name]["rpc"]
    if isinstance(rpcs, str):
        return rpcs
    idx = _rpc_index.get(chain_name, 0) % len(rpcs)
    return rpcs[idx]


def _rotate_rpc(chain_name: str) -> None:
    """Rate limit veya hata → bir sonraki RPC'ye geç."""
    rpcs = config.CHAINS[chain_name]["rpc"]
    if not isinstance(rpcs, list) or len(rpcs) < 2:
        return
    _rpc_index[chain_name] = (_rpc_index.get(chain_name, 0) + 1) % len(rpcs)
    _w3_cache.pop(chain_name, None)
    _pair_cache.clear()
    print(config.ui_text(
        f"  [RPC] {chain_name} → {_current_rpc(chain_name)}",
        f"  [RPC] {chain_name} → {_current_rpc(chain_name)}",
    ))


def _get_w3(chain_name: str) -> Web3:
    rpc = _current_rpc(chain_name)
    cached = _w3_cache.get(chain_name)
    if cached is None or cached.provider.endpoint_uri != rpc:
        _w3_cache[chain_name] = Web3(
            Web3.HTTPProvider(rpc, request_kwargs={"timeout": 7})
        )
        _pair_cache.clear()
    return _w3_cache[chain_name]


def _resolve_pair(
    w3: Web3,
    factory_addr: str,
    token_in: str,
    token_out: str,
) -> Optional[tuple[str, bool]]:
    """
    Factory'den pair adresini ve token0 sırasını döndür.
    Çağrı maliyeti: getPair() + token0() = 2 RPC, sadece ilk seferde.
    """
    factory = w3.eth.contract(
        address=Web3.to_checksum_address(factory_addr),
        abi=_FACTORY_ABI,
    )
    pair_addr = factory.functions.getPair(
        Web3.to_checksum_address(token_in),
        Web3.to_checksum_address(token_out),
    ).call()

    if pair_addr.lower() == _ZERO_ADDR:
        return None  # Bu DEX'te pair yok

    pair_cs = Web3.to_checksum_address(pair_addr)
    pair = w3.eth.contract(address=pair_cs, abi=_PAIR_ABI)
    token0 = pair.functions.token0().call()
    is_token0_in = token0.lower() == token_in.lower()
    return pair_cs, is_token0_in


def _query_pool(
    chain_name: str,
    factory_addr: str,
    token_in: str,
    token_out: str,
    dec_in: int,
    dec_out: int,
    label: str = "",        # "sushiswap@ethereum:WETH/USDT" formatında log etiketi
) -> Optional[float]:
    """
    Senkron pool sorgusu. ThreadPoolExecutor içinde koşar.
    None döndürme koşulları:
      - Pair yok
      - Son swap > 24 saat önce (bayat rezervler)
      - Quote rezervi < MIN_DEX_LIQUIDITY_USD / 2 (ince pool)
    """
    def _log(reason: str) -> None:
        if label:
            msg = f"[DEX] {label}: {reason}"
            print(f"  {msg}")
            _scan_logs.append(msg)

    try:
        with _get_sem(chain_name):   # zincir başına max 3 eş zamanlı RPC çağrısı
            return _query_pool_inner(
                chain_name, factory_addr, token_in, token_out,
                dec_in, dec_out, label, _log,
            )
    except Exception as e:
        _log(f"{type(e).__name__}: {str(e)[:80]}")
        return None


def _query_pool_inner(
    chain_name: str,
    factory_addr: str,
    token_in: str,
    token_out: str,
    dec_in: int,
    dec_out: int,
    label: str,
    _log,
) -> Optional[float]:
    try:
        w3 = _get_w3(chain_name)

        cache_key = (chain_name, factory_addr.lower(), token_in.lower(), token_out.lower())
        if cache_key not in _pair_cache:
            _pair_cache[cache_key] = _resolve_pair(w3, factory_addr, token_in, token_out)

        pair_info = _pair_cache[cache_key]
        if pair_info is None:
            _log(config.ui_text(
                "bu DEX'te pair bulunamadı (zero address)",
                "pair not found on this DEX (zero address)",
            ))
            return None

        pair_cs, token_in_is_token0 = pair_info
        pair = w3.eth.contract(address=pair_cs, abi=_PAIR_ABI)
        r0, r1, ts_last = pair.functions.getReserves().call()

        # ── 1. Tazelik kontrolü ──────────────────────────────────────────────
        now = int(time.time())
        if ts_last > now:
            ts_last -= 2 ** 32          # uint32 sarım düzeltmesi
        age_h = (now - ts_last) / 3600
        if age_h > 24:
            _log(config.ui_text(
                f"son swap {age_h:.1f} saat önce (>24s bayat)",
                f"last swap {age_h:.1f} hours ago (>24h stale)",
            ))
            return None

        # ── 2. Rezervleri yönlendir ──────────────────────────────────────────
        if token_in_is_token0:
            reserve_in, reserve_out = r0, r1
        else:
            reserve_in, reserve_out = r1, r0

        if reserve_in == 0 or reserve_out == 0:
            _log(config.ui_text("sıfır rezerv", "zero reserve"))
            return None

        # ── 3. Likidite kontrolü ─────────────────────────────────────────────
        quote_usd = reserve_out / (10 ** dec_out)
        min_needed = config.MIN_DEX_LIQUIDITY_USD / 2
        if quote_usd < min_needed:
            _log(config.ui_text(
                f"düşük likidite: ${quote_usd:,.0f} < ${min_needed:,.0f}",
                f"low liquidity: ${quote_usd:,.0f} < ${min_needed:,.0f}",
            ))
            return None

        # ── 4. Spot fiyat ────────────────────────────────────────────────────
        spot = (reserve_out / (10 ** dec_out)) / (reserve_in / (10 ** dec_in))
        return spot

    except Exception as e:
        err = str(e)
        if any(s in err for s in ('-32001', 'usage limit', '429', 'rate limit', 'rate-limit')):
            _rotate_rpc(chain_name)
            _log(config.ui_text("rate limit → RPC rotasyonu", "rate limit → rotating RPC"))
        else:
            _log(f"{type(e).__name__}: {err[:80]}")
        return None


async def _fetch_async(
    chain_name: str,
    factory_addr: str,
    token_in: str,
    token_out: str,
    dec_in: int,
    dec_out: int,
    label: str = "",
) -> Optional[float]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _executor,
        _query_pool,
        chain_name, factory_addr, token_in, token_out, dec_in, dec_out, label,
    )


async def fetch_all_dex_prices() -> dict[str, dict[str, float]]:
    """
    Tüm zincir × DEX × pair kombinasyonlarını paralel sorgular.
    Döner: { "ETH/USDT": {"uniswap_v2@ethereum": 2100.5, ...} }
    CEX fiyat dict formatıyla uyumludur.
    """
    tasks: list = []
    labels: list[tuple[str, str]] = []   # (cex_symbol, exchange_label)

    for chain_name, chain in config.CHAINS.items():
        tokens = chain["tokens"]
        for dex_name, dex_cfg in chain["dexes"].items():
            factory = dex_cfg["factory"]
            label   = f"{dex_name}@{chain_name}"
            for base, quote, cex_symbol in chain["pairs"]:
                if config.is_pair_excluded(cex_symbol, label):
                    continue
                if base not in tokens or quote not in tokens:
                    continue
                token_in,  dec_in  = tokens[base]
                token_out, dec_out = tokens[quote]
                pair_label = f"{label}:{base}/{quote}"
                tasks.append(
                    asyncio.ensure_future(
                        _fetch_async(
                            chain_name, factory,
                            token_in, token_out,
                            dec_in,   dec_out,
                            pair_label,
                        )
                    )
                )
                labels.append((cex_symbol, label))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    prices: dict[str, dict[str, float]] = {}
    for (cex_symbol, label), result in zip(labels, results):
        if isinstance(result, float) and result > 0:
            prices.setdefault(cex_symbol, {})[label] = result

    return prices
