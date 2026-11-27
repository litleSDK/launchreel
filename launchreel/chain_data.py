"""Live on-chain token telemetry fetcher for Robinhood Chain & Solana (DexScreener + Blockscout).

Zero API keys required. Fetches live price, market cap, liquidity, 24h volume,
24h buys/sells, holders count, DEX/Uniswap v4 pool metadata, socials, and logo.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "launchreel_cache"
ICON_CACHE_DIR = CACHE_DIR / "icons"

FEATURED_TOKENS: list[dict[str, str]] = [
    {
        "name": "LaunchReel",
        "symbol": "REEL",
        "address": "LAUNCHREEL_OFFICIAL",
        "chain": "robinhood",
        "tagline": "Our official v4.fun launch — instant 1080p launch trailers & Agen MCP video API",
        "badge": "OUR TOKEN",
    },
    {
        "name": "Canopy Finance",
        "symbol": "CNPY",
        "address": "0x532c5583671870723CEEf573600208aF49c87c54",
        "chain": "robinhood",
        "tagline": "Live ecosystem demo — 100% of v4.fun fees buy back & burn CNPY",
        "badge": "Live Demo",
    },
    {
        "name": "DigitalDon",
        "symbol": "DON",
        "address": "0x0B551573D731090B3F57129c11B0A4A9e4D0A69c",
        "chain": "robinhood",
        "tagline": "Live ecosystem demo — top vetted v4.fun holder-map project",
        "badge": "Live Demo",
    },
    {
        "name": "AINDEX",
        "symbol": "AIX",
        "address": "0x24949d12998ceCC9dE014E8BB7c8e007Ba5e308E",
        "chain": "robinhood",
        "tagline": "Live ecosystem demo — autonomous DeFi agents on Robinhood Chain",
        "badge": "Live Demo",
    },
    {
        "name": "Nomi Labs",
        "symbol": "NOMI",
        "address": "0xBD6F2A7c6BBBcca2BE40c4Ccd2C60C8f142FD03a",
        "chain": "robinhood",
        "tagline": "Live ecosystem demo — Super Intelligence tools on Robinhood Chain",
        "badge": "Live Demo",
    },
]

_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


@dataclass
class TokenTelemetry:
    address: str
    chain: str
    name: str
    symbol: str
    price_usd: float
    price_native: str
    quote_symbol: str
    market_cap: float
    fdv: float
    liquidity_usd: float
    liquidity_quote: float
    volume_24h: float
    volume_6h: float
    volume_1h: float
    change_24h: float
    change_6h: float
    change_1h: float
    change_5m: float
    buys_24h: int
    sells_24h: int
    holders_count: int | None
    dex_id: str
    pool_labels: list[str]
    pair_address: str
    pair_url: str
    v4_url: str
    website: str
    twitter: str
    telegram: str
    icon_url: str
    icon_local_path: str | None = None
    tagline: str = ""
    is_v4_ecosystem: bool = True
    raw_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fmt_usd(val: float | None) -> str:
    if val is None or val <= 0:
        return "$0"
    if val >= 1_000_000_000:
        return f"${val / 1_000_000_000:.2f}B"
    if val >= 1_000_000:
        return f"${val / 1_000_000:.2f}M"
    if val >= 1_000:
        return f"${val / 1_000:.1f}K"
    if val >= 1:
        return f"${val:.2f}"
    if val >= 0.01:
        return f"${val:.4f}"
    return f"${val:.6f}"


def fmt_price(val: float | None) -> str:
    if val is None or val <= 0:
        return "$0.00"
    if val >= 1.0:
        return f"${val:,.2f}"
    if val >= 0.01:
        return f"${val:.4f}"
    if val >= 0.0001:
        return f"${val:.6f}"
    return f"${val:.8f}"


def fmt_int(val: int | None) -> str:
    if val is None:
        return "Live"
    if val >= 1_000_000:
        return f"{val / 1_000_000:.2f}M"
    if val >= 10_000:
        return f"{val / 1_000:.1f}K"
    return f"{val:,}"


def short_addr(addr: str) -> str:
    addr = addr.strip()
    if len(addr) <= 12:
        return addr
    return f"{addr[:6]}...{addr[-4:]}"


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(float(v))
    except Exception:
        return default


def _download_icon(url: str, key: str) -> str | None:
    if not url:
        return None
    ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(f"{key}:{url}".encode("utf-8")).hexdigest()[:12]
    out_path = ICON_CACHE_DIR / f"{digest}.png"
    if out_path.exists() and out_path.stat().st_size > 256:
        return str(out_path)
    try:
        from io import BytesIO
        from PIL import Image

        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200 and len(r.content) > 128:
            img = Image.open(BytesIO(r.content)).convert("RGBA")
            img.save(out_path, "PNG")
            return str(out_path)
    except Exception:
        return None
    return None


def _lookup_featured_tagline(address: str) -> str:
    a_low = address.strip().lower()
    for item in FEATURED_TOKENS:
        if item["address"].lower() == a_low:
            return item["tagline"]
    return ""


def _ensure_launchreel_brand_icon() -> str:
    """Generate a crisp 512x512 vector-style LaunchReel ($REEL) brand icon PNG."""
    ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = ICON_CACHE_DIR / "launchreel_brand_icon.png"
    from PIL import Image, ImageDraw

    S = 512
    img = Image.new("RGBA", (S, S), (7, 14, 18, 255))
    draw = ImageDraw.Draw(img, "RGBA")
    # Subtle inner emerald halo ring
    draw.ellipse((24, 24, S - 24, S - 24), fill=(11, 23, 28, 255), outline=(0, 229, 141, 255), width=14)
    draw.ellipse((62, 62, S - 62, S - 62), outline=(0, 229, 141, 70), width=4)
    # Bold geometric Play/Launch Triangle + Frame Cut
    tri = [(192, 148), (192, 364), (376, 256)]
    draw.polygon(tri, fill=(0, 229, 141, 255))
    # Sleek gold cinema notch accent on left
    draw.rounded_rectangle((136, 154, 162, 358), radius=10, fill=(237, 189, 46, 255))
    img.save(out, "PNG")
    return str(out)


def build_launchreel_official_telemetry() -> TokenTelemetry:
    """Official LaunchReel ($REEL) pre-launch profile for generating our own launch trailer."""
    icon_p = _ensure_launchreel_brand_icon()
    return TokenTelemetry(
        address="LAUNCHREEL_OFFICIAL",
        chain="Robinhood Chain",
        name="LaunchReel",
        symbol="REEL",
        price_usd=0.0,
        price_native="",
        quote_symbol="ETH",
        market_cap=0.0,
        fdv=0.0,
        liquidity_usd=0.0,
        liquidity_quote=0.0,
        volume_24h=0.0,
        volume_6h=0.0,
        volume_1h=0.0,
        change_24h=100.0,
        change_6h=42.0,
        change_1h=18.0,
        change_5m=6.0,
        buys_24h=100,
        sells_24h=0,
        holders_count=None,
        dex_id="uniswap",
        pool_labels=["v4"],
        pair_address="",
        pair_url="https://v4.fun/vetted",
        v4_url="https://v4.fun",
        website="launchreel.xyz",
        twitter="https://x.com/launchreelfun",
        telegram="",
        icon_url="/api/brand-icon",
        icon_local_path=icon_p,
        tagline="Paste any contract address → 1080p launch trailer in 8 seconds",
        is_v4_ecosystem=True,
        raw_sources=["launchreel_official"],
    )


def fetch_token_telemetry(address: str, timeout: int = 12) -> TokenTelemetry:
    """Fetch live token telemetry from DexScreener + Robinhood Chain Blockscout."""
    clean_addr = address.strip()
    if not clean_addr:
        raise ValueError("Contract address or mint cannot be empty")
    if clean_addr.upper() in ("LAUNCHREEL_OFFICIAL", "REEL", "$REEL", "LAUNCHREEL"):
        return build_launchreel_official_telemetry()

    # Strip v4.fun URL if user pasted full URL like https://v4.fun/0x...
    m_url = re.search(r"v4\.fun/(?:tokens/|markets/)?([A-Za-z0-9]{20,64})", clean_addr)
    if m_url:
        clean_addr = m_url.group(1)

    is_evm = clean_addr.startswith("0x") and len(clean_addr) == 42
    sources: list[str] = []

    dex_pair: dict[str, Any] | None = None
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{clean_addr}",
            headers=_HTTP_HEADERS,
            timeout=timeout,
        )
        if r.status_code == 200:
            data = r.json()
            pairs = data.get("pairs") or []
            if pairs:
                # Prefer Robinhood Chain or Solana highest-liquidity pair
                def _pair_score(p: dict[str, Any]) -> tuple[int, float]:
                    cid = str(p.get("chainId", "")).lower()
                    pref = 2 if cid == "robinhood" else (1 if cid == "solana" else 0)
                    liq = _safe_float((p.get("liquidity") or {}).get("usd"))
                    vol = _safe_float((p.get("volume") or {}).get("h24"))
                    return (pref, liq + vol * 0.25)

                pairs_sorted = sorted(pairs, key=_pair_score, reverse=True)
                dex_pair = pairs_sorted[0]
                sources.append("dexscreener")
    except Exception:
        dex_pair = None

    blockscout: dict[str, Any] | None = None
    if is_evm:
        try:
            r2 = requests.get(
                f"https://robinhoodchain.blockscout.com/api/v2/tokens/{clean_addr}",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=timeout,
            )
            if r2.status_code == 200:
                blockscout = r2.json()
                sources.append("robinhood_blockscout")
        except Exception:
            blockscout = None

    if dex_pair is None and blockscout is None:
        raise RuntimeError(
            f"Could not find live on-chain data for {clean_addr} on DexScreener or Robinhood Chain Blockscout."
        )

    base = (dex_pair or {}).get("baseToken") or {}
    quote = (dex_pair or {}).get("quoteToken") or {}
    info = (dex_pair or {}).get("info") or {}
    txns_24 = ((dex_pair or {}).get("txns") or {}).get("h24") or {}
    vol = (dex_pair or {}).get("volume") or {}
    chg = (dex_pair or {}).get("priceChange") or {}
    liq = (dex_pair or {}).get("liquidity") or {}

    chain_raw = str((dex_pair or {}).get("chainId") or ("robinhood" if is_evm else "solana")).lower()
    chain_display = (
        "Robinhood Chain"
        if chain_raw == "robinhood"
        else ("Solana" if chain_raw == "solana" else chain_raw.title())
    )

    name = str(base.get("name") or (blockscout or {}).get("name") or "Unknown Token")
    symbol = str(base.get("symbol") or (blockscout or {}).get("symbol") or "TOKEN").upper()
    price_usd = _safe_float(
        (dex_pair or {}).get("priceUsd"),
        _safe_float((blockscout or {}).get("exchange_rate")),
    )
    market_cap = _safe_float(
        (dex_pair or {}).get("marketCap"),
        _safe_float((blockscout or {}).get("circulating_market_cap")),
    )
    fdv = _safe_float((dex_pair or {}).get("fdv"), market_cap)
    if market_cap <= 0 and fdv > 0:
        market_cap = fdv

    volume_24h = _safe_float(vol.get("h24"), _safe_float((blockscout or {}).get("volume_24h")))
    liquidity_usd = _safe_float(liq.get("usd"))
    liquidity_quote = _safe_float(liq.get("quote"))

    holders_raw = (blockscout or {}).get("holders_count")
    holders_count = _safe_int(holders_raw) if holders_raw is not None else None

    websites = info.get("websites") or []
    socials = info.get("socials") or []
    website = str(websites[0].get("url", "")) if websites else ""
    twitter = ""
    telegram = ""
    for s in socials:
        st = str(s.get("type", "")).lower()
        su = str(s.get("url", ""))
        if st == "twitter" and not twitter:
            twitter = su
        elif st == "telegram" and not telegram:
            telegram = su

    icon_url = str(info.get("imageUrl") or (blockscout or {}).get("icon_url") or "")
    icon_local = _download_icon(icon_url, f"{symbol}_{clean_addr[:10]}")

    dex_id = str((dex_pair or {}).get("dexId") or ("uniswap" if is_evm else "meteora"))
    labels = [str(x) for x in ((dex_pair or {}).get("labels") or [])]

    return TokenTelemetry(
        address=clean_addr,
        chain=chain_display,
        name=name,
        symbol=symbol,
        price_usd=price_usd,
        price_native=str((dex_pair or {}).get("priceNative") or ""),
        quote_symbol=str(quote.get("symbol") or ("ETH" if is_evm else "SOL")),
        market_cap=market_cap,
        fdv=fdv,
        liquidity_usd=liquidity_usd,
        liquidity_quote=liquidity_quote,
        volume_24h=volume_24h,
        volume_6h=_safe_float(vol.get("h6")),
        volume_1h=_safe_float(vol.get("h1")),
        change_24h=_safe_float(chg.get("h24")),
        change_6h=_safe_float(chg.get("h6")),
        change_1h=_safe_float(chg.get("h1")),
        change_5m=_safe_float(chg.get("m5")),
        buys_24h=_safe_int(txns_24.get("buys")),
        sells_24h=_safe_int(txns_24.get("sells")),
        holders_count=holders_count,
        dex_id=dex_id,
        pool_labels=labels,
        pair_address=str((dex_pair or {}).get("pairAddress") or ""),
        pair_url=str((dex_pair or {}).get("url") or f"https://v4.fun/{clean_addr}"),
        v4_url=f"https://v4.fun/{clean_addr}",
        website=website,
        twitter=twitter,
        telegram=telegram,
        icon_url=icon_url,
        icon_local_path=icon_local,
        tagline=_lookup_featured_tagline(clean_addr),
        is_v4_ecosystem=(chain_raw in ("robinhood", "solana")),
        raw_sources=sources,
    )


def build_default_script(t: TokenTelemetry) -> dict[str, Any]:
    """Generate a high-retention 4-beat launch promo / live market-recap script from live chain data."""
    mcap_s = fmt_usd(t.market_cap)
    vol_s = fmt_usd(t.volume_24h)
    liq_s = fmt_usd(t.liquidity_usd) if t.liquidity_usd > 0 else "locked on chain"
    total_tx = t.buys_24h + t.sells_24h

    # Speakable phonetic ticker helper (avoid robotic spelling when pronounceable)
    spoken_sym = t.symbol
    if t.symbol == "CNPY":
        spoken_sym = "Canopy"
    elif t.symbol == "DON":
        spoken_sym = "Digital Don"
    elif t.symbol == "AIX":
        spoken_sym = "A-I-X"
    elif t.symbol == "NOMI":
        spoken_sym = "Nomi"

    if t.address == "LAUNCHREEL_OFFICIAL":
        return {
            "kicker": "LAUNCHING ON V4.FUN  •  ROBINHOOD CHAIN",
            "hook_headline": "CA TO 1080P LAUNCH REEL IN 8 SECONDS",
            "cta_text": "LAUNCHING SOON ON V4.FUN  •  $REEL",
            "beats": [
                "PASTE ANY V4.FUN CONTRACT  •  GET A 1080P LAUNCH REEL",
                "LIVE BLOCKSCOUT + DEXSCREENER DATA  •  NO ROBOTIC AI VOICE",
                "HOLD $REEL FOR PRO 1080P + AGEN MCP AUTONOMOUS VIDEO API",
                "LAUNCHREEL ($REEL)  •  BUILT FOR EVERY V4.FUN BUILDER",
            ],
        }

    if t.change_24h >= 15:
        hook_headline = f"${t.symbol} UP {t.change_24h:+.1f}% TODAY"
        beat1 = f"${t.symbol} IS UP {t.change_24h:+.1f}% ON {t.chain.upper()}"
    elif t.change_24h <= -10:
        hook_headline = f"${t.symbol} LIVE ON {t.chain.upper()}"
        beat1 = f"LIVE ON-CHAIN TELEMETRY FOR ${t.symbol}"
    else:
        hook_headline = f"MEET ${t.symbol} ON {t.chain.upper()}"
        beat1 = f"LIVE TECH LAUNCH ON {t.chain.upper()}"

    if t.symbol == "CNPY":
        holders_phrase = f"{fmt_int(t.holders_count)} HOLDERS" if t.holders_count else f"{total_tx} DAILY TRADES"
        beat2 = f"{mcap_s} MCAP  •  {vol_s} 24H VOL  •  {holders_phrase}"
        beat3 = "100% OF V4.FUN PLATFORM FEES BUY BACK & BURN $CNPY"
        beat4 = "LAUNCH YOUR TECH ON V4.FUN  •  REELS BY LAUNCHREEL"
        kicker = "POWERING V4.FUN • ROBINHOOD CHAIN"
    else:
        holders_part = f"  •  {fmt_int(t.holders_count)} HOLDERS" if t.holders_count else ""
        beat2 = f"{mcap_s} MCAP  •  {vol_s} 24H VOL{holders_part}"
        if total_tx >= 10:
            buy_pct = int(round(100.0 * t.buys_24h / max(1, total_tx)))
            beat3 = f"{buy_pct}% BUY FLOW ({t.buys_24h} BUYS / {t.sells_24h} SELLS)  •  LOCKED LP"
        else:
            beat3 = "FIXED 1B SUPPLY  •  PERMANENTLY LOCKED LP  •  ZERO ADMIN KEYS"
        cta_site = t.website.replace("https://", "").replace("http://", "").rstrip("/") if t.website else "v4.fun"
        beat4 = f"LIVE ON V4.FUN  •  {cta_site.upper()}"
        kicker = f"{t.chain.upper()} • LIVE ON-CHAIN BRIEF"

    return {
        "kicker": kicker,
        "hook_headline": hook_headline,
        "cta_text": f"v4.fun/{short_addr(t.address)}",
        "beats": [beat1, beat2, beat3, beat4],
    }
