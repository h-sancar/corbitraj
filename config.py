from dotenv import load_dotenv
import json
import os
from pathlib import Path

load_dotenv(".env.local")
load_dotenv()

# ─── CEX Borsaları ────────────────────────────────────────────────────────────

def _ex(prefix: str, **extra) -> dict:
    """Borsa config şablonu — env vars otomatik bulunur."""
    p = prefix.upper()
    return {
        "apiKey":  os.getenv(f"{p}_API_KEY", ""),
        "secret":  os.getenv(f"{p}_SECRET", ""),
        **extra,
    }

EXCHANGES = {
    # ── Tier 1 ───────────────────────────────────────────────────────────────
    "binance":   {**_ex("binance"),                          "maker_fee": 0.001,  "taker_fee": 0.001},
    "binancetr": {**_ex("binancetr"),                        "maker_fee": 0.001,  "taker_fee": 0.001},
    "bybit":     {**_ex("bybit"),                            "maker_fee": 0.001,  "taker_fee": 0.001},
    "bybiteu":   {**_ex("bybiteu"),                          "maker_fee": 0.001,  "taker_fee": 0.001},
    "okx":       {**_ex("okx",    passphrase=os.getenv("OKX_PASSPHRASE","")),
                                                              "maker_fee": 0.0008, "taker_fee": 0.001},
    "bitget":    {**_ex("bitget", passphrase=os.getenv("BITGET_PASSPHRASE","")),
                                                              "maker_fee": 0.001,  "taker_fee": 0.001},
    "kucoin":    {**_ex("kucoin", passphrase=os.getenv("KUCOIN_PASSPHRASE","")),
                                                              "maker_fee": 0.001,  "taker_fee": 0.001},
    "gateio":    {**_ex("gateio"),                           "maker_fee": 0.002,  "taker_fee": 0.002},
    "mexc":      {**_ex("mexc"),                             "maker_fee": 0.0,    "taker_fee": 0.001},
    "kraken":    {**_ex("kraken"),                           "maker_fee": 0.0016, "taker_fee": 0.0026},
    # ── Tier 2 ───────────────────────────────────────────────────────────────
    "htx":       {**_ex("htx"),                              "maker_fee": 0.002,  "taker_fee": 0.002},
    "cryptocom": {**_ex("cryptocom"),                        "maker_fee": 0.0,    "taker_fee": 0.00075},
    "whitebit":  {**_ex("whitebit"),                         "maker_fee": 0.001,  "taker_fee": 0.001},
    "bingx":     {**_ex("bingx"),                            "maker_fee": 0.001,  "taker_fee": 0.001},
    "bitmart":   {**_ex("bitmart", memo=os.getenv("BITMART_MEMO","")),
                                                              "maker_fee": 0.0025, "taker_fee": 0.0025},
    "poloniex":  {**_ex("poloniex"),                         "maker_fee": 0.001,  "taker_fee": 0.002},
    "bitfinex":  {**_ex("bitfinex"),                         "maker_fee": 0.001,  "taker_fee": 0.002},
    "lbank":     {**_ex("lbank"),                            "maker_fee": 0.001,  "taker_fee": 0.001},
    "upbit":     {**_ex("upbit"),                            "maker_fee": 0.0005, "taker_fee": 0.0005},
    # ── Tier 3 / Bölgesel ────────────────────────────────────────────────────
    "bitstamp":  {**_ex("bitstamp"),                         "maker_fee": 0.003,  "taker_fee": 0.005},
    "gemini":    {**_ex("gemini"),                           "maker_fee": 0.002,  "taker_fee": 0.004},
    "coinbase":  {**_ex("coinbase"),                         "maker_fee": 0.004,  "taker_fee": 0.006},
    "paribu":    {**_ex("paribu"),                           "maker_fee": 0.001,  "taker_fee": 0.001},
    "btcturk":   {**_ex("btcturk"),                          "maker_fee": 0.0018, "taker_fee": 0.0018},
    "bithumb":   {**_ex("bithumb"),                          "maker_fee": 0.0025, "taker_fee": 0.0025},
}

# CEX sembol listesi
SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "ADA/USDT",
    "AVAX/USDT",
]

# ─── DEX Zincirleri ───────────────────────────────────────────────────────────

# DEX exchange label'ı "dex_adı@zincir" formatında → fee tablosuna bakılır
DEX_FEES: dict[str, float] = {
    "uniswap_v2@ethereum":   0.003,
    "sushiswap@ethereum":    0.003,
    "pancakeswap_v2@bsc":    0.0025,
    "biswap@bsc":            0.002,
    "quickswap@polygon":     0.003,
    "sushiswap@polygon":     0.003,
}

CHAINS: dict[str, dict] = {
    "ethereum": {
        "rpc": os.getenv("ETH_RPC", "").split(",") if os.getenv("ETH_RPC") else [
            "https://eth-mainnet.public.blastapi.io",
            "https://ethereum.blockpi.network/v1/rpc/public",
            "https://eth.meowrpc.com",
            "https://eth.llamarpc.com",
        ],
        # Her DEX: router (fiyat sorgusu) + factory (pair adresi + rezerv doğrulaması)
        "dexes": {
            "uniswap_v2": {
                "router":  "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
                "factory": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
            },
            "sushiswap": {
                "router":  "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F",
                "factory": "0xC0AEe478e3658e2610c5F7A4A2E1777cE9e4f2Ac",
            },
        },
        "tokens": {
            "WETH": ("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", 18),
            "USDT": ("0xdAC17F958D2ee523a2206206994597C13D831ec7",  6),
            "USDC": ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  6),
            "WBTC": ("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",  8),
        },
        "pairs": [
            ("WETH", "USDT", "ETH/USDT"),
            ("WBTC", "USDT", "BTC/USDT"),
            ("WETH", "USDC", "ETH/USDT"),
        ],
    },
    "bsc": {
        "rpc": os.getenv("BSC_RPC", "").split(",") if os.getenv("BSC_RPC") else [
            "https://bsc-dataseed.binance.org/",
            "https://bsc-dataseed1.defibit.io/",
            "https://bsc-dataseed2.defibit.io/",
        ],
        "dexes": {
            "pancakeswap_v2": {
                "router":  "0x10ED43C718714eb63d5aA57B78B54704E256024E",
                "factory": "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",
            },
            "biswap": {
                "router":  "0x3a6d8cA21D1CF76F653A67577FA0D27453350dD8",
                "factory": "0x858E3312ed3A876947EA49d572A7C42DE08af7EE",
            },
        },
        "tokens": {
            "WBNB": ("0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", 18),
            "USDT": ("0x55d398326f99059fF775485246999027B3197955", 18),
            "BTCB": ("0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c", 18),
            "ETH":  ("0x2170Ed0880ac9A755fd29B2688956BD959F933F8", 18),
        },
        "pairs": [
            ("WBNB", "USDT", "BNB/USDT"),
            ("BTCB", "USDT", "BTC/USDT"),
            ("ETH",  "USDT", "ETH/USDT"),
        ],
    },
    "polygon": {
        "rpc": os.getenv("POLYGON_RPC", "").split(",") if os.getenv("POLYGON_RPC") else [
            "https://gateway.tenderly.co/public/polygon",
            "https://polygon.llamarpc.com",
        ],
        "dexes": {
            "quickswap": {
                "router":  "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff",
                "factory": "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32",
            },
            "sushiswap": {
                "router":  "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
                "factory": "0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
            },
        },
        "tokens": {
            "WETH":   ("0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", 18),
            "USDT":   ("0xc2132D05D31c914a87C6611C10748AEb04B58e8F",  6),
            "WBTC":   ("0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6",  8),
            "WMATIC": ("0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", 18),
        },
        "pairs": [
            ("WETH",   "USDT", "ETH/USDT"),
            ("WBTC",   "USDT", "BTC/USDT"),
            ("WMATIC", "USDT", "MATIC/USDT"),
        ],
    },
}

_EXCLUDED_PAIRS_FILE = Path(__file__).with_name(".excluded_pairs.json")
EXCLUDED_PAIRS: list[dict[str, list[str]]] = []
EXCLUDED_PAIRS_VERSION = 0

_PAIR_QUOTES = sorted(
    ["FDUSD", "TUSD", "BUSD", "USDT", "USDC", "BNB", "ETH", "BTC",
     "EUR", "GBP", "TRY", "KRW", "DAI", "BIDR"],
    key=len,
    reverse=True,
)


def normalize_pair_symbol(value: str) -> str:
    raw = str(value or "").strip().upper().replace("-", "/").replace("_", "/")
    raw = "".join(raw.split())
    if not raw:
        return ""
    if "/" in raw:
        base, _, quote = raw.partition("/")
        return f"{base}/{quote}" if base and quote else ""
    for quote in _PAIR_QUOTES:
        if raw.endswith(quote) and len(raw) > len(quote):
            return f"{raw[:-len(quote)]}/{quote}"
    return raw


def scan_exchange_ids() -> set[str]:
    ids = set(EXCHANGES)
    for chain_name, chain in CHAINS.items():
        for dex_name in chain["dexes"]:
            ids.add(f"{dex_name}@{chain_name}")
    return ids


def normalize_excluded_pairs(items) -> list[dict[str, list[str]]]:
    valid_exchanges = scan_exchange_ids()
    grouped: dict[str, set[str]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        symbol = normalize_pair_symbol(
            item.get("symbol") or item.get("pair") or item.get("s") or ""
        )
        if not symbol:
            continue
        exchanges = item.get("exchanges") or item.get("e") or []
        if not isinstance(exchanges, list):
            continue
        clean_exchanges = {
            str(ex).strip()
            for ex in exchanges
            if str(ex).strip() in valid_exchanges
        }
        if clean_exchanges:
            grouped.setdefault(symbol, set()).update(clean_exchanges)
    return [
        {"symbol": symbol, "exchanges": sorted(exchanges)}
        for symbol, exchanges in sorted(grouped.items())
    ]


def _save_excluded_pairs() -> None:
    _EXCLUDED_PAIRS_FILE.write_text(
        json.dumps(EXCLUDED_PAIRS, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_excluded_pairs() -> None:
    global EXCLUDED_PAIRS
    if not _EXCLUDED_PAIRS_FILE.exists():
        return
    try:
        data = json.loads(_EXCLUDED_PAIRS_FILE.read_text(encoding="utf-8"))
        EXCLUDED_PAIRS = normalize_excluded_pairs(data)
    except Exception:
        EXCLUDED_PAIRS = []


def set_excluded_pairs(items) -> list[dict[str, list[str]]]:
    global EXCLUDED_PAIRS, EXCLUDED_PAIRS_VERSION
    normalized = normalize_excluded_pairs(items)
    if normalized != EXCLUDED_PAIRS:
        EXCLUDED_PAIRS = normalized
        EXCLUDED_PAIRS_VERSION += 1
        _save_excluded_pairs()
    return EXCLUDED_PAIRS


def is_pair_excluded(symbol: str, exchange: str) -> bool:
    normalized = normalize_pair_symbol(symbol)
    return any(
        item["symbol"] == normalized and exchange in item["exchanges"]
        for item in EXCLUDED_PAIRS
    )


_load_excluded_pairs()

# Pool'un kabul edilebilir minimum USDT likiditesi (quote rezerv tarafı, tek yön)
# Bu değerin altındaki pool'lar atlanır → ince/bayat pool koruması
MIN_DEX_LIQUIDITY_USD = 10_000

# ─── Genel Parametreler ───────────────────────────────────────────────────────

# Ücretler düşüldükten sonra gereken minimum net kâr yüzdesi
MIN_PROFIT_PCT = 0.3          # %0.30

# P&L tahmini için işlem büyüklüğü (USDT)
TRADE_AMOUNT_USDT = 1000.0

# True → sadece fırsatları göster, emir gönderme
DRY_RUN = True

# UI / terminal dili: "tr" veya "en"
LANGUAGE = "en"


def is_english() -> bool:
    return str(LANGUAGE).lower() == "en"


def ui_text(tr_text: str, en_text: str) -> str:
    return en_text if is_english() else tr_text

# Tarama döngü aralığı (saniye)
SCAN_INTERVAL = 30

# Binance'ten çekilecek en popüler USDT çifti sayısı
# Taranacak sembol sayısı (0 = limitsiz)
TICKER_LIMIT = 0

# Her iki exchange'de de asgari 24s USDT hacmi (aynı ticker ≠ aynı coin riskini azaltır)
MIN_PAIR_VOLUME_USD = 100_000

# Gross spread bu eşiği geçerse farklı coin olduğu anlamına gelir, atlanır
# Gerçek arbitraj tipik olarak %0.1–%5 arasındadır
MAX_SPREAD_PCT = 50.0  # %50'den büyük spread → farklı coin veya veri hatası


# CoinGecko exchange ID'leri — coin kimlik doğrulaması için kullanılır
COINGECKO_EXCHANGE_IDS: dict[str, str] = {
    "binance":   "binance",
    "binancetr": "binance_tr",
    "bybit":     "bybit_spot",
    "bybiteu":   "bybit_spot",
    "okx":       "okex",
    "bitget":    "bitget",
    "kucoin":    "kucoin",
    "gateio":    "gate",
    "mexc":      "mxc",
    "kraken":    "kraken",
    "htx":       "huobi",
    "cryptocom": "crypto_com",
    "whitebit":  "whitebit",
    "bingx":     "bingx",
    "bitmart":   "bitmart",
    "poloniex":  "poloniex",
    "bitfinex":  "bitfinex",
    "lbank":     "lbank",
    "upbit":     "upbit",
    "bitstamp":  "bitstamp",
    "gemini":    "gemini",
    "coinbase":  "gdax",
    "bithumb":   "bithumb",
}

# Web server bind address and port. Keep localhost by default for API key safety.
HOST = os.getenv("HOST", "127.0.0.1")

# Web sunucu portu
PORT = 8001
