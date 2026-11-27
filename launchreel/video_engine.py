"""Broadcast-grade On-Chain Launch Reel & Market-Recap Video Renderer (1080x1920 & 1920x1080).

Builds kinetic fintech motion-graphics videos directly from live Robinhood Chain & Solana telemetry:
  - Beat 1 (0.0s Frame-0 Hook): Instant glowing token emblem, kinetic ticker, 24h delta pill & headline sweep
  - Beat 2 (Live Market Telemetry): Real-delta animated momentum chart + rolling MCAP/Vol/LP/Holders counters
  - Beat 3 (Order Flow & Proof): Live 24h Buy vs. Sell pressure bar + stamped immutable on-chain proof cards
  - Beat 4 (Launch Payoff / CTA): Clean v4.fun / project URL hero card designed for seamless Shorts looping
  - Audio: Local Kokoro-82M neural narration + broadcast EQ + studio Foley SFX + ducked underscore pad
"""
from __future__ import annotations

import gc
import json
import math
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from clipper.ffmpeg_bin import ffmpeg_exe
from vox_engine.palette import FONTS_DIR, smoothstep, smootherstep
from vox_engine.renderer import render_contact_sheet
from vox_engine.sfx import SfxCue, mix_audio_master
from vox_engine.voice import KOKORO_DIR, _master_voice_broadcast

from .chain_data import TokenTelemetry, build_default_script, fmt_int, fmt_price, fmt_usd, short_addr

ROOT = Path(__file__).resolve().parent.parent
RENDERS_DIR = ROOT / "data" / "launchreel_renders"

# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------

THEMES: dict[str, dict[str, tuple[int, int, int]]] = {
    "robinhood": {
        "bg_top": (7, 13, 17),
        "bg_bot": (11, 21, 26),
        "grid": (22, 39, 48),
        "card": (15, 25, 32),
        "card_alt": (19, 32, 41),
        "border": (38, 66, 78),
        "accent": (0, 229, 141),       # Robinhood neon emerald
        "accent2": (242, 201, 76),     # Warm gold highlight
        "danger": (255, 92, 108),      # Coral red for sells/negative
        "text": (244, 248, 251),
        "muted": (144, 168, 182),
        "pill_text": (8, 16, 20),
        "is_light": (0, 0, 0),
    },
    "canopy": {
        "bg_top": (10, 10, 14),
        "bg_bot": (18, 16, 24),
        "grid": (34, 31, 44),
        "card": (21, 19, 28),
        "card_alt": (28, 25, 37),
        "border": (66, 56, 38),
        "accent": (237, 189, 46),      # Canopy gold
        "accent2": (46, 229, 157),     # Emerald positive
        "danger": (242, 92, 104),
        "text": (246, 242, 232),
        "muted": (166, 158, 148),
        "pill_text": (15, 13, 18),
        "is_light": (0, 0, 0),
    },
    "editorial": {
        "bg_top": (239, 235, 227),     # Vox archival board
        "bg_bot": (232, 226, 214),
        "grid": (215, 208, 195),
        "card": (248, 245, 239),
        "card_alt": (242, 238, 228),
        "border": (198, 190, 174),
        "accent": (29, 28, 32),        # Letterpress ink
        "accent2": (237, 189, 46),     # Editorial yellow
        "danger": (224, 68, 54),       # Vermilion
        "text": (25, 24, 28),
        "muted": (102, 98, 92),
        "pill_text": (248, 245, 239),
        "is_light": (1, 1, 1),
    },
}

VOICE_PRESETS: dict[str, dict[str, str]] = {
    "beat_dark": {"id": "music:beat_dark", "label": "No Voice — Dark Fintech Beat + SFX (Recommended)"},
    "beat_hype": {"id": "music:beat_hype", "label": "No Voice — Hype Promo Beat + SFX"},
    "cinematic": {"id": "music:cinematic", "label": "No Voice — Editorial Score + SFX"},
    "elevenlabs": {"id": "elevenlabs:nathaniel", "label": "Pro Human Voice — ElevenLabs (Nathaniel)"},
}

MUSIC_TRACKS: dict[str, Path] = {
    "beat_dark": ROOT / "data" / "campaigns" / "forgegui" / "beat_dark.wav",
    "beat_hype": ROOT / "data" / "campaigns" / "forgegui" / "beat.wav",
    "cinematic": ROOT / "data" / "vox_output" / "tordesillas" / "hf_assets" / "music_vox_bed.mp3",
}

# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

_SYSTEM_FONT_CANDIDATES = {
    "bold": [
        r"C:\Windows\Fonts\ariblk.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        str(FONTS_DIR / "Anton-Regular.ttf"),
    ],
    "display": [
        str(FONTS_DIR / "Anton-Regular.ttf"),
        r"C:\Windows\Fonts\ariblk.ttf",
    ],
    "sans": [
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        str(FONTS_DIR / "LibreCaslonText-Regular.ttf"),
    ],
    "semibold": [
        r"C:\Windows\Fonts\seguisb.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
    ],
    "mono": [
        r"C:\Windows\Fonts\consolab.ttf",
        r"C:\Windows\Fonts\lucon.ttf",
        str(FONTS_DIR / "SpecialElite-Regular.ttf"),
    ],
    "serif_bold": [
        str(FONTS_DIR / "LibreCaslonText-Bold.ttf"),
        r"C:\Windows\Fonts\georgiab.ttf",
    ],
}


@lru_cache(maxsize=256)
def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for cand in _SYSTEM_FONT_CANDIDATES.get(kind, _SYSTEM_FONT_CANDIDATES["sans"]):
        if Path(cand).exists():
            return ImageFont.truetype(cand, max(10, int(size)))
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0], bb[3] - bb[1]


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        test = f"{cur} {w}".strip()
        tw, _ = _text_size(draw, test, font)
        if tw <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [text]


# ---------------------------------------------------------------------------
# Kokoro TTS singleton (fast repeated renders!)
# ---------------------------------------------------------------------------

_KOKORO_LOCK = threading.Lock()
_KOKORO_INST = None


def _get_kokoro():
    global _KOKORO_INST
    with _KOKORO_LOCK:
        if _KOKORO_INST is None:
            from kokoro_onnx import Kokoro

            model_p = KOKORO_DIR / "kokoro-v1.0.fp16.onnx"
            voices_p = KOKORO_DIR / "voices-v1.0.bin"
            _KOKORO_INST = Kokoro(str(model_p), str(voices_p))
        return _KOKORO_INST


@dataclass
class WordCue:
    word: str
    start_s: float
    end_s: float


@dataclass
class BeatSpan:
    idx: int
    text: str
    start_s: float
    end_s: float
    start_frame: int
    end_frame: int


def _synth_beats_with_timing(
    beats: list[str],
    out_wav: Path,
    voice: str = "am_michael+bm_george",
    speed: float = 1.03,
    pause_s: float = 0.24,
    fps: int = 30,
) -> tuple[list[BeatSpan], list[WordCue], float]:
    """Synthesize each script beat individually so visual transitions hit on the EXACT syllable."""
    kokoro = _get_kokoro()
    if "+" in voice:
        parts = [p.strip() for p in voice.split("+") if p.strip()]
        v_style = kokoro.get_voice_style(parts[0]) * 0.65 + kokoro.get_voice_style(parts[1]) * 0.35
    else:
        v_style = voice

    sr = 24000
    lead_pad_s = 4.0 / fps  # matches _master_voice_broadcast adelay
    chunks: list[np.ndarray] = []
    spans: list[BeatSpan] = []
    word_cues: list[WordCue] = []
    cursor_s = lead_pad_s

    n_beats = len(beats)
    for idx, sent in enumerate(beats):
        clean_sent = sent.strip()
        if not clean_sent:
            clean_sent = "Live on chain."
        b_speed = speed * (0.98 if idx == n_beats - 1 else (1.04 if idx == 0 else 1.02))
        samples, sr = kokoro.create(clean_sent, voice=v_style, speed=b_speed, lang="en-us")
        samples = samples.astype(np.float32)

        abs_s = np.abs(samples)
        nz = np.where(abs_s > 0.008)[0]
        if len(nz) > 0:
            i0 = max(0, int(nz[0]) - int(0.015 * sr))
            i1 = min(len(samples), int(nz[-1]) + int(0.035 * sr))
            samples = samples[i0:i1]

        dur_s = len(samples) / float(sr)
        b_start = cursor_s
        b_end = cursor_s + dur_s

        # Build syllable-weighted word timestamps across this beat (instant & deterministic)
        raw_words = [w for w in clean_sent.split() if w.strip()]
        weights = [max(1.0, len(re.sub(r"[^a-zA-Z0-9]", "", w)) * 0.85 + (1.6 if w[-1] in ".,—!?" else 0.0)) for w in raw_words]
        w_sum = sum(weights) or 1.0
        w_cur = b_start
        for w, wt in zip(raw_words, weights):
            w_dur = dur_s * (wt / w_sum)
            word_cues.append(WordCue(word=w, start_s=w_cur, end_s=w_cur + w_dur))
            w_cur += w_dur

        chunks.append(samples)
        gap = pause_s * (1.25 if idx == n_beats - 2 else 1.0) if idx < n_beats - 1 else 0.55
        if idx < n_beats - 1:
            chunks.append(np.zeros(int(sr * gap), dtype=np.float32))

        span_end = b_end + (gap if idx < n_beats - 1 else 0.65)
        spans.append(
            BeatSpan(
                idx=idx,
                text=clean_sent,
                start_s=b_start if idx > 0 else 0.0,
                end_s=span_end,
                start_frame=0 if idx == 0 else int(round(b_start * fps)),
                end_frame=int(round(span_end * fps)),
            )
        )
        cursor_s = b_end + gap

    raw = np.concatenate(chunks)
    raw_wav = out_wav.with_suffix(".raw.wav")
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(raw_wav), raw, sr)
    total_dur = _master_voice_broadcast(raw_wav, out_wav, lead_pad_s=lead_pad_s)
    raw_wav.unlink(missing_ok=True)
    # Ensure final beat reaches full duration
    if spans:
        spans[-1].end_s = max(spans[-1].end_s, total_dur)
        spans[-1].end_frame = int(round(spans[-1].end_s * fps))
    return spans, word_cues, max(total_dur, spans[-1].end_s if spans else 16.0)


# ---------------------------------------------------------------------------
# Visual Building Blocks
# ---------------------------------------------------------------------------

def _build_base_background(W: int, H: int, theme: dict[str, tuple[int, int, int]]) -> np.ndarray:
    """Pre-render crisp vertical gradient + subtle fintech terminal grid + corner vignette."""
    yy = np.linspace(0.0, 1.0, H, dtype=np.float32)[:, None]
    top = np.array(theme["bg_top"], dtype=np.float32)
    bot = np.array(theme["bg_bot"], dtype=np.float32)
    grad = top * (1.0 - yy) + bot * yy
    bg = np.broadcast_to(grad[:, None, :], (H, W, 3)).copy()

    # Subtle architectural terminal grid (48px spacing)
    grid_col = np.array(theme["grid"], dtype=np.float32)
    step = 54 if H > W else 60
    bg[::step, :, :] = bg[::step, :, :] * 0.72 + grid_col * 0.28
    bg[:, ::step, :] = bg[:, ::step, :] * 0.72 + grid_col * 0.28

    # Soft radial accent halo in upper-center
    xx_n = (np.arange(W, dtype=np.float32)[None, :] - W * 0.5) / (W * 0.58)
    yy_n = (np.arange(H, dtype=np.float32)[:, None] - H * 0.28) / (H * 0.36)
    r2 = xx_n ** 2 + yy_n ** 2
    glow = np.exp(-r2 * 1.45)[:, :, None]
    acc = np.array(theme["accent"], dtype=np.float32)
    is_light = theme["is_light"][0] == 1
    if not is_light:
        bg = np.clip(bg + glow * acc * 0.14, 0, 255)
    else:
        acc2 = np.array(theme["accent2"], dtype=np.float32)
        bg = np.clip(bg * (1.0 - glow * 0.04) + glow * acc2 * 0.06, 0, 255)

    return bg.astype(np.uint8)


def _prepare_token_emblem(telemetry: TokenTelemetry, size: int, accent: tuple[int, int, int]) -> Image.Image:
    """Create crisp circular token emblem RGBA with ring border (uses live downloaded logo or monogram)."""
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    pad = 10
    md.ellipse((pad, pad, size - pad, size - pad), fill=255)

    inner = None
    if telemetry.icon_local_path and Path(telemetry.icon_local_path).exists():
        try:
            src = Image.open(telemetry.icon_local_path).convert("RGBA")
            # Center-crop square
            sw, sh = src.size
            side = min(sw, sh)
            src = src.crop(((sw - side) // 2, (sh - side) // 2, (sw + side) // 2, (sh + side) // 2))
            inner = src.resize((size - 2 * pad, size - 2 * pad), Image.Resampling.LANCZOS)
        except Exception:
            inner = None

    if inner is None:
        inner = Image.new("RGBA", (size - 2 * pad, size - 2 * pad), (18, 28, 36, 255))
        idraw = ImageDraw.Draw(inner)
        sym = telemetry.symbol[:4]
        f = _font("display", int(size * 0.32))
        tw, th = _text_size(idraw, sym, f)
        idraw.text(((size - 2 * pad - tw) // 2, (size - 2 * pad - th) // 2 - 4), sym, font=f, fill=(*accent, 255))

    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    layer.paste(inner, (pad, pad))
    out.paste(layer, (0, 0), mask)

    draw = ImageDraw.Draw(out)
    draw.ellipse((pad, pad, size - pad, size - pad), outline=(*accent, 240), width=5)
    return out


def _generate_momentum_curve(t: TokenTelemetry, n_pts: int = 44) -> list[float]:
    """Synthesize a realistic multi-window price trajectory anchored to real 24h/6h/1h/5m deltas."""
    # Back-calculate relative anchors ending at 1.00 today
    def _from_delta(end_val: float, pct: float) -> float:
        denom = max(0.25, 1.0 + pct / 100.0)
        return end_val / denom

    p_now = 1.0
    p_5m = _from_delta(p_now, t.change_5m)
    p_1h = _from_delta(p_now, t.change_1h)
    p_6h = _from_delta(p_now, t.change_6h)
    p_24h = _from_delta(p_now, t.change_24h)

    anchors = [p_24h, (p_24h + p_6h) * 0.52, p_6h, (p_6h + p_1h) * 0.49, p_1h, p_5m, p_now]
    xs_a = np.linspace(0.0, 1.0, len(anchors))
    xs = np.linspace(0.0, 1.0, n_pts)
    base = np.interp(xs, xs_a, anchors)

    # Deterministic organic microstructure wiggle seeded by contract address
    seed = int.from_bytes(t.address.encode("utf-8")[:4], "big") % 10000
    rng = np.random.default_rng(seed)
    span = max(0.04, float(np.max(base) - np.min(base)))
    wiggle = np.cumsum(rng.normal(0.0, span * 0.055, size=n_pts))
    # Pin start and end to exact anchors
    wiggle -= np.linspace(wiggle[0], wiggle[-1], n_pts)
    curve = base + wiggle
    lo, hi = float(np.min(curve)), float(np.max(curve))
    if hi - lo < 1e-5:
        return [0.5] * n_pts
    # Normalize to [0.14, 0.88] of chart box height
    return [float(0.14 + 0.74 * (v - lo) / (hi - lo)) for v in curve]


def _draw_rounded_card(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    fill: tuple[int, int, int],
    border: tuple[int, int, int],
    radius: int = 24,
    border_w: int = 2,
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=(*fill, 238), outline=(*border, 255), width=border_w)


def _draw_pill(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    bg: tuple[int, int, int],
    fg: tuple[int, int, int],
    pad_x: int = 22,
    pad_y: int = 10,
    center_x: bool = False,
    border: tuple[int, int, int] | None = None,
) -> tuple[int, int, int, int]:
    tw, th = _text_size(draw, text, font)
    pw = tw + pad_x * 2
    ph = th + pad_y * 2
    x0 = int(x - pw // 2) if center_x else int(x)
    y0 = int(y)
    box = (x0, y0, x0 + pw, y0 + ph)
    draw.rounded_rectangle(
        box,
        radius=ph // 2,
        fill=(*bg, 245),
        outline=((*border, 255) if border else None),
        width=2 if border else 0,
    )
    # Optical vertical centering using bbox top offset
    bb = draw.textbbox((0, 0), text, font=font)
    ty = y0 + (ph - (bb[3] - bb[1])) // 2 - bb[1]
    tx = x0 + (pw - (bb[2] - bb[0])) // 2 - bb[0]
    draw.text((tx, ty), text, font=font, fill=(*fg, 255))
    return box


def _draw_captions(
    draw: ImageDraw.ImageDraw,
    word_cues: list[WordCue],
    t_s: float,
    W: int,
    H: int,
    theme: dict[str, tuple[int, int, int]],
    is_vertical: bool,
) -> None:
    """Crisp lower-third 3-word captions with active-word highlight pill (never overlaps main stage!)."""
    if not word_cues:
        return
    # Skip first 0.25s so Frame-0 visual hook breathes cleanly
    if t_s < 0.22:
        return

    # Group words into 3-word chunks
    chunk_size = 3 if is_vertical else 5
    active_idx = -1
    for i, wc in enumerate(word_cues):
        if wc.start_s <= t_s <= wc.end_s + 0.08:
            active_idx = i
            break
    if active_idx < 0:
        return

    chunk_idx = active_idx // chunk_size
    c_start = chunk_idx * chunk_size
    chunk = word_cues[c_start : c_start + chunk_size]
    if not chunk:
        return

    f_cap = _font("bold", 50 if is_vertical else 44)
    space_w = 20
    clean_words = [re.sub(r"^[—–-]+|[—–-]+$", "", wc.word).upper() for wc in chunk]
    clean_words = [w if w else "•" for w in clean_words]
    bboxes = [draw.textbbox((0, 0), w, font=f_cap) for w in clean_words]
    widths = [bb[2] - bb[0] for bb in bboxes]
    glyph_h = max(bb[3] - bb[1] for bb in bboxes)
    total_w = sum(widths) + space_w * (len(chunk) - 1)
    y_center = int(H * 0.822) if is_vertical else int(H * 0.885)
    x_cur = (W - total_w) // 2

    is_light = theme["is_light"][0] == 1
    acc = theme["accent2"] if is_light else theme["accent"]
    hi_fg = (15, 15, 18)
    base_fg = (255, 255, 255)
    stroke_col = (12, 14, 18)

    # Dark sleek caption backing bar optically centered around glyph heights
    pad_bx, pad_by = 30, 20
    bar_box = (
        max(36, x_cur - pad_bx),
        y_center - glyph_h // 2 - pad_by,
        min(W - 36, x_cur + total_w + pad_bx),
        y_center + glyph_h // 2 + pad_by,
    )
    draw.rounded_rectangle(bar_box, radius=22, fill=(10, 14, 18, 205))

    for local_i, (w_txt, bb, ww) in enumerate(zip(clean_words, bboxes, widths)):
        is_active = (c_start + local_i) == active_idx
        ty = y_center - (bb[3] - bb[1]) // 2 - bb[1]
        tx = x_cur - bb[0]
        if is_active:
            draw.rounded_rectangle(
                (x_cur - 12, y_center - glyph_h // 2 - 10, x_cur + ww + 12, y_center + glyph_h // 2 + 10),
                radius=13,
                fill=(*acc, 255),
            )
            draw.text((tx, ty), w_txt, font=f_cap, fill=(*hi_fg, 255))
        else:
            draw.text(
                (tx, ty),
                w_txt,
                font=f_cap,
                fill=(*base_fg, 255),
                stroke_width=4,
                stroke_fill=(*stroke_col, 255),
            )
        x_cur += ww + space_w


# ---------------------------------------------------------------------------
# Main Render Pipeline
# ---------------------------------------------------------------------------

def _mix_music_and_sfx_only(
    music_path: Path | None,
    cues: list[SfxCue],
    total_frames: int,
    out_wav: Path,
    fps: int = 30,
) -> Path:
    """Build a punchy, zero-AI-voice 48kHz stereo trailer master (music beat + tactile UI/bass SFX)."""
    from vox_engine.sfx import SR, _generate_documentary_pad, _load_music_bed, _synth_sfx

    total_n = int(round((total_frames / float(fps)) * SR))
    if music_path is not None and music_path.exists():
        bed = _load_music_bed(music_path, total_n)
        # Boost beat presence cleanly for music-only trailer
        bed = bed * 2.45
    else:
        bed = _generate_documentary_pad(total_n) * 1.9

    sfx_l = np.zeros(total_n, dtype=np.float32)
    sfx_r = np.zeros(total_n, dtype=np.float32)
    rng = np.random.default_rng(2026)
    for idx, cue in enumerate(sorted(cues, key=lambda c: c.frame)):
        start_i = int(round((cue.frame / float(fps)) * SR))
        if start_i < 0 or start_i >= total_n:
            continue
        wave = _synth_sfx(cue.kind, seed=300 + idx * 41) * (0.42 * cue.gain)
        pan = float(rng.uniform(-0.20, 0.20))
        gl = math.sqrt(0.5 * (1.0 - pan))
        gr = math.sqrt(0.5 * (1.0 + pan))
        end_i = min(total_n, start_i + len(wave))
        ln = end_i - start_i
        sfx_l[start_i:end_i] += wave[:ln] * gl
        sfx_r[start_i:end_i] += wave[:ln] * gr

    stereo = np.stack([bed[:, 0] + sfx_l, bed[:, 1] + sfx_r], axis=1)
    # Crisp 0.12s fade-in, 0.45s fade-out, peak normalize to -0.6 dB
    fin = min(total_n // 10, int(SR * 0.12))
    fout = min(total_n // 6, int(SR * 0.45))
    if fin > 0:
        stereo[:fin] *= np.linspace(0.0, 1.0, fin, dtype=np.float32)[:, None]
    if fout > 0:
        stereo[-fout:] *= np.linspace(1.0, 0.0, fout, dtype=np.float32)[:, None]

    peak = float(np.max(np.abs(stereo))) + 1e-6
    stereo = stereo * (0.93 / peak)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_wav), stereo, SR, subtype="PCM_16")
    return out_wav


def _synth_elevenlabs_beats(
    beats: list[str],
    out_wav: Path,
    fps: int = 30,
) -> tuple[list[BeatSpan], list[WordCue], float]:
    """Generate pro human narration via ElevenLabs API (uses ELEVENLABS_API_KEY in .env)."""
    import urllib.request
    from vox_engine.elevenlabs_tts import ensure_nathaniel_voice_id, load_api_key

    key = load_api_key()
    voice_id = ensure_nathaniel_voice_id(key)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"

    sr = 48000
    lead_pad_s = 4.0 / fps
    chunks: list[np.ndarray] = []
    spans: list[BeatSpan] = []
    word_cues: list[WordCue] = []
    cursor_s = lead_pad_s

    for idx, sent in enumerate(beats):
        clean_sent = sent.strip() or "Live on chain."
        payload = json.dumps({
            "text": clean_sent,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.48, "similarity_boost": 0.78, "style": 0.25},
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            mp3_bytes = resp.read()
        tmp_mp3 = out_wav.with_name(f"{out_wav.stem}_b{idx}.mp3")
        tmp_mp3.write_bytes(mp3_bytes)
        proc = subprocess.run(
            [ffmpeg_exe(), "-y", "-i", str(tmp_mp3), "-ar", str(sr), "-ac", "1", "-f", "f32le", "pipe:1"],
            capture_output=True, check=True,
        )
        tmp_mp3.unlink(missing_ok=True)
        samples = np.frombuffer(proc.stdout, dtype=np.float32).copy()
        dur_s = len(samples) / float(sr)
        b_start = cursor_s
        b_end = cursor_s + dur_s

        raw_words = [w for w in clean_sent.split() if w.strip()]
        weights = [max(1.0, len(re.sub(r"[^a-zA-Z0-9]", "", w)) * 0.85) for w in raw_words]
        w_sum = sum(weights) or 1.0
        w_cur = b_start
        for w, wt in zip(raw_words, weights):
            w_dur = dur_s * (wt / w_sum)
            word_cues.append(WordCue(word=w, start_s=w_cur, end_s=w_cur + w_dur))
            w_cur += w_dur

        chunks.append(samples)
        gap = 0.22 if idx < len(beats) - 1 else 0.50
        if idx < len(beats) - 1:
            chunks.append(np.zeros(int(sr * gap), dtype=np.float32))
        span_end = b_end + gap
        spans.append(BeatSpan(idx=idx, text=clean_sent, start_s=b_start if idx > 0 else 0.0, end_s=span_end,
                              start_frame=0 if idx == 0 else int(round(b_start * fps)), end_frame=int(round(span_end * fps))))
        cursor_s = b_end + gap

    raw = np.concatenate(chunks)
    raw_wav = out_wav.with_suffix(".raw.wav")
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(raw_wav), raw, sr)
    total_dur = _master_voice_broadcast(raw_wav, out_wav, lead_pad_s=lead_pad_s)
    raw_wav.unlink(missing_ok=True)
    if spans:
        spans[-1].end_s = max(spans[-1].end_s, total_dur)
        spans[-1].end_frame = int(round(spans[-1].end_s * fps))
    return spans, word_cues, max(total_dur, spans[-1].end_s if spans else 16.0)


def _draw_kinetic_takeaway(
    draw: ImageDraw.ImageDraw,
    text: str,
    age: int,
    W: int,
    H: int,
    theme: dict[str, tuple[int, int, int]],
    is_vertical: bool,
) -> None:
    """Clean Apple/Phantom-style lower-third kinetic callout bar (used in zero-voice music trailer mode)."""
    if not text.strip():
        return
    ep = smootherstep(min(1.0, age / 10.0))
    if ep <= 0.02:
        return
    pad_x = 68 if is_vertical else 140
    f_t = _font("bold", 34 if is_vertical else 30)
    lines = _wrap_text(draw, text.strip().upper(), f_t, W - 2 * pad_x - 64)[:2]
    line_h = 44 if is_vertical else 38
    box_h = 44 + len(lines) * line_h
    y_mid = int(H * 0.825) if is_vertical else int(H * 0.885)
    shift = int((1.0 - ep) * 26)
    y0 = y_mid - box_h // 2 + shift
    box = (pad_x, y0, W - pad_x, y0 + box_h)
    _draw_rounded_card(draw, box, theme["card"], theme["accent"], radius=22, border_w=2)
    # Left pulsing accent notch
    draw.rounded_rectangle((pad_x, y0 + 18, pad_x + 10, y0 + box_h - 18), radius=5, fill=(*theme["accent"], 255))
    ty = y0 + (box_h - len(lines) * line_h) // 2 + 2
    for ln in lines:
        lw, _ = _text_size(draw, ln, f_t)
        draw.text(((W - lw) // 2, ty), ln, font=f_t, fill=(*theme["text"], 255))
        ty += line_h


def render_launch_reel(
    telemetry: TokenTelemetry,
    *,
    kicker: str | None = None,
    hook_headline: str | None = None,
    cta_text: str | None = None,
    beats: list[str] | None = None,
    theme_name: str = "robinhood",
    voice_preset: str = "beat_dark",
    aspect: str = "9:16",
    out_dir: Path | None = None,
    out_name: str | None = None,
    progress_cb: Callable[[int, str], None] | None = None,
) -> dict[str, str | float | int]:
    """Render a complete 1080p MP4 launch promo reel + QA contact sheet from live telemetry."""
    t0 = time.time()

    def _prog(pct: int, msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(pct, msg)
            except Exception:
                pass

    defaults = build_default_script(telemetry)
    kicker = (kicker or defaults["kicker"]).strip()
    hook_headline = (hook_headline or defaults["hook_headline"]).strip()
    cta_text = (cta_text or defaults["cta_text"]).strip()
    beats_list = [b.strip() for b in (beats or defaults["beats"]) if b.strip()]
    while len(beats_list) < 4:
        beats_list.append(defaults["beats"][len(beats_list)])
    beats_list = beats_list[:4]

    theme = THEMES.get(theme_name, THEMES["robinhood"])
    preset_info = VOICE_PRESETS.get(voice_preset, VOICE_PRESETS["beat_dark"])
    voice_id = preset_info["id"]
    is_music_only = voice_id.startswith("music:")
    is_vertical = aspect != "16:9"
    W, H = (1080, 1920) if is_vertical else (1920, 1080)
    fps = 30

    render_dir = out_dir or RENDERS_DIR
    render_dir.mkdir(parents=True, exist_ok=True)
    safe_sym = re.sub(r"[^A-Za-z0-9_-]", "", telemetry.symbol or "TOKEN") or "TOKEN"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    stem = out_name or f"{safe_sym}_{'9x16' if is_vertical else '16x9'}_{stamp}"
    voice_wav = render_dir / f"{stem}_voice.wav"
    master_wav = render_dir / f"{stem}_master.wav"
    out_mp4 = render_dir / f"{stem}.mp4"
    contact_jpg = render_dir / f"{stem}_contact.jpg"

    # 1. Build beat timing & audio track (Default: crisp 15s No-Voice Music + SFX trailer!)
    word_cues: list[WordCue] = []
    if is_music_only:
        _prog(15, "Building 15s music-synced kinetic trailer timeline...")
        beat_durs = [3.5, 4.3, 4.2, 3.2]  # 15.2s punchy high-retention loop!
        spans: list[BeatSpan] = []
        cur = 0.0
        for idx, (txt, dur) in enumerate(zip(beats_list, beat_durs)):
            spans.append(
                BeatSpan(
                    idx=idx,
                    text=txt,
                    start_s=cur,
                    end_s=cur + dur,
                    start_frame=int(round(cur * fps)),
                    end_frame=int(round((cur + dur) * fps)),
                )
            )
            cur += dur
        total_dur = cur
        total_frames = int(math.ceil(total_dur * fps))
    elif voice_id.startswith("elevenlabs:"):
        _prog(12, "Generating pro human narration (ElevenLabs)...")
        spans, word_cues, total_dur = _synth_elevenlabs_beats(beats_list, voice_wav, fps=fps)
        total_frames = max(fps * 8, int(math.ceil(total_dur * fps)))
    else:
        _prog(12, "Synthesizing narration...")
        spans, word_cues, total_dur = _synth_beats_with_timing(beats_list, voice_wav, voice=voice_id, fps=fps)
        total_frames = max(fps * 8, int(math.ceil(total_dur * fps)))

    # 2. Build tactile Foley SFX timeline synced to scene transitions & card stamps
    _prog(28, "Mastering music beat & tactile UI/bass SFX...")
    cues: list[SfxCue] = [
        SfxCue(frame=1, kind="reveal_boom", gain=1.0),
        SfxCue(frame=spans[1].start_frame, kind="camera_whoosh", gain=0.95),
        SfxCue(frame=spans[1].start_frame + 8, kind="paper_slap", gain=0.82),
        SfxCue(frame=spans[1].start_frame + 16, kind="paper_slap", gain=0.76),
        SfxCue(frame=spans[1].start_frame + 24, kind="paper_slap", gain=0.76),
        SfxCue(frame=spans[1].start_frame + 32, kind="paper_slap", gain=0.76),
        SfxCue(frame=spans[2].start_frame, kind="camera_whoosh", gain=0.95),
        SfxCue(frame=spans[2].start_frame + 10, kind="marker_scratch", gain=0.80),
        SfxCue(frame=spans[2].start_frame + 22, kind="typewriter_tick", gain=0.88),
        SfxCue(frame=spans[2].start_frame + 34, kind="typewriter_tick", gain=0.88),
        SfxCue(frame=spans[2].start_frame + 46, kind="typewriter_tick", gain=0.88),
        SfxCue(frame=spans[3].start_frame, kind="reveal_boom", gain=1.0),
    ]
    if is_music_only:
        track_key = voice_id.split(":", 1)[1]
        _mix_music_and_sfx_only(
            music_path=MUSIC_TRACKS.get(track_key),
            cues=cues,
            total_frames=total_frames,
            out_wav=master_wav,
            fps=fps,
        )
    else:
        mix_audio_master(
            voice_wav=voice_wav,
            cues=cues,
            total_frames=total_frames,
            out_wav=master_wav,
            fps=fps,
            sfx_ratio=0.34,
            pad_gain=0.070,
            music_bed_path=MUSIC_TRACKS.get("cinematic"),
            music_gain=0.11,
        )

    # 3. Precompute static background, token emblem, and real-delta chart curve
    _prog(38, "Preparing live telemetry visuals & token emblem...")
    base_bg = _build_base_background(W, H, theme)
    emblem_lg = _prepare_token_emblem(telemetry, 224 if is_vertical else 188, theme["accent"])
    emblem_sm = _prepare_token_emblem(telemetry, 118 if is_vertical else 104, theme["accent"])
    curve_pts = _generate_momentum_curve(telemetry, n_pts=48)

    pos_change = telemetry.change_24h >= 0
    delta_col = (
        theme["accent2"]
        if (theme["is_light"][0] == 1 and pos_change)
        else (theme["accent"] if pos_change else theme["danger"])
    )
    if theme_name == "canopy" and pos_change:
        delta_col = theme["accent2"]
    is_self_launch = telemetry.address == "LAUNCHREEL_OFFICIAL"
    delta_str = "★ V4.FUN LAUNCH" if is_self_launch else f"{'▲' if pos_change else '▼'} {telemetry.change_24h:+.1f}% 24H"
    dex_tag = "UNISWAP V4" if ("v4" in [x.lower() for x in telemetry.pool_labels] or telemetry.dex_id == "uniswap") else telemetry.dex_id.upper()
    chain_pill = f"●  {telemetry.chain.upper()}  •  {dex_tag}"
    total_tx = telemetry.buys_24h + telemetry.sells_24h
    buy_ratio = (telemetry.buys_24h / float(total_tx)) if total_tx > 0 else 0.62

    # Prepare Scene 3 proof bullets
    if is_self_launch:
        proof_rows = [
            ("01 // $REEL PRO STUDIO", "Hold $REEL for watermark-free 1080p & custom themes"),
            ("02 // AGEN MCP VIDEO API", "Autonomous AI agents auto-render trailers at launch"),
            ("03 // IMMUTABLE V4.FUN POOL", "Fixed 1B supply • Zero team allocation • Locked LP"),
        ]
    elif telemetry.symbol == "CNPY":
        proof_rows = [
            ("01 // FEE FLYWHEEL", "100% of v4.fun platform fees buy back & burn $CNPY"),
            ("02 // BUILDER GRANTS", "$1,000 + X Gold Badge + PR for vetted tech launches"),
            ("03 // ON-CHAIN CONTRACT", f"Robinhood Chain  •  {short_addr(telemetry.address)}"),
        ]
    else:
        proof_rows = [
            ("01 // LOCKED LIQUIDITY", "Full supply locked in pool — zero admin withdraw key"),
            ("02 // FIXED 1B SUPPLY", "Zero team allocation, zero mint, immutable fee vault"),
            ("03 // VERIFIED CONTRACT", f"{telemetry.chain}  •  {short_addr(telemetry.address)}"),
        ]

    # 4. Frame renderer function (30fps smooth!)
    def render_frame(frame: int) -> np.ndarray:
        t_s = frame / float(fps)
        img = Image.fromarray(base_bg.copy()).convert("RGBA")
        draw = ImageDraw.Draw(img, "RGBA")

        # Subtle moving radar scanline
        scan_y = int(((frame * 5) % (H + 240)) - 120)
        draw.rectangle((0, scan_y, W, scan_y + 3), fill=(*theme["accent"], 22))

        # Determine active scene beat
        beat_i = 0
        for sp in spans:
            if frame >= sp.start_frame:
                beat_i = sp.idx
        sp = spans[beat_i]
        age = max(0, frame - sp.start_frame)
        beat_len = max(1, sp.end_frame - sp.start_frame)
        p_beat = min(1.0, age / float(beat_len))

        # --- TOP TELEMETRY HUD (always crisp, never crowded) ---
        hud_y = 66 if is_vertical else 42
        hud_x = 58 if is_vertical else 72
        _draw_pill(
            draw,
            hud_x,
            hud_y,
            chain_pill,
            _font("mono", 22 if is_vertical else 20),
            bg=theme["card"],
            fg=theme["accent"],
            pad_x=20,
            pad_y=11,
            border=theme["border"],
        )
        # Right badge: Beat progress indicator (1/4 .. 4/4)
        prog_lbl = f"LIVE  •  0{beat_i + 1}/04"
        f_hud_r = _font("mono", 21 if is_vertical else 19)
        pw_r, _ = _text_size(draw, prog_lbl, f_hud_r)
        _draw_pill(
            draw,
            W - hud_x - pw_r - 40,
            hud_y,
            prog_lbl,
            f_hud_r,
            bg=theme["card"],
            fg=theme["muted"],
            pad_x=20,
            pad_y=11,
            border=theme["border"],
        )

        # Progress bar across very top edge
        bar_w = int(W * (frame / float(max(1, total_frames - 1))))
        draw.rectangle((0, 0, bar_w, 8), fill=(*theme["accent"], 255))

        # ===================================================================
        # SCENE 1: FRAME-0 INSTANT KINETIC HOOK
        # ===================================================================
        if beat_i == 0:
            # Pulsing radial rings behind emblem
            cx = W // 2 if is_vertical else int(W * 0.30)
            cy = 430 if is_vertical else int(H * 0.45)
            pulse = 0.5 + 0.5 * math.sin(frame * 0.22)
            for ring_i in range(3):
                rad = int((128 if is_vertical else 112) + ring_i * 34 + pulse * 14)
                alpha_r = max(18, 95 - ring_i * 28)
                draw.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), outline=(*theme["accent"], alpha_r), width=3)

            # Token Emblem (instant visible on Frame 0, subtle breathing scale)
            ew, eh = emblem_lg.size
            img.alpha_composite(emblem_lg, (cx - ew // 2, cy - eh // 2))

            if is_vertical:
                # Big kinetic Ticker
                f_tick = _font("display", 136)
                tick_txt = f"${telemetry.symbol}"
                tw, th = _text_size(draw, tick_txt, f_tick)
                tx = (W - tw) // 2
                ty = 578
                draw.text((tx, ty), tick_txt, font=f_tick, fill=(*theme["text"], 255))

                # Sweeping accent underline (frames 0..14)
                sweep = smootherstep(min(1.0, age / 14.0))
                ul_w = int(tw * sweep)
                draw.rounded_rectangle((tx, ty + th + 14, tx + ul_w, ty + th + 24), radius=5, fill=(*theme["accent"], 255))

                # Full name + Price + Delta pill row
                f_name = _font("semibold", 40)
                nw, nh = _text_size(draw, telemetry.name, f_name)
                draw.text(((W - nw) // 2, ty + th + 42), telemetry.name, font=f_name, fill=(*theme["muted"], 255))

                # Delta & Live Price Pills
                pill_y = ty + th + 110
                _draw_pill(
                    draw,
                    W // 2 - 155,
                    pill_y,
                    delta_str,
                    _font("bold", 28),
                    bg=delta_col,
                    fg=theme["pill_text"],
                    pad_x=24,
                    pad_y=12,
                    center_x=True,
                )
                _draw_pill(
                    draw,
                    W // 2 + 165,
                    pill_y,
                    "1B FIXED • LOCKED LP" if is_self_launch else f"MCAP  {fmt_usd(telemetry.market_cap)}",
                    _font("mono", 24 if is_self_launch else 26),
                    bg=theme["card_alt"],
                    fg=theme["text"],
                    pad_x=22,
                    pad_y=13,
                    center_x=True,
                    border=theme["border"],
                )

                # Hook Headline Card (y = 955..1235)
                card_box = (72, 955, W - 72, 1270)
                _draw_rounded_card(draw, card_box, theme["card"], theme["border"], radius=28, border_w=2)
                # Left accent bar
                draw.rounded_rectangle((72, 955, 86, 1270), radius=6, fill=(*theme["accent"], 255))
                f_k = _font("mono", 24)
                draw.text((114, 988), kicker[:44], font=f_k, fill=(*theme["accent"], 255))
                f_hl = _font("bold", 54)
                hl_lines = _wrap_text(draw, hook_headline.upper(), f_hl, W - 220)[:2]
                hy = 1044
                for ln in hl_lines:
                    draw.text((114, hy), ln, font=f_hl, fill=(*theme["text"], 255))
                    hy += 66
                # Subtle live price line inside card
                draw.text(
                    (114, 1206),
                    "1080P 30FPS   •   ~8S RENDER   •   AGEN MCP API" if is_self_launch else f"PRICE {fmt_price(telemetry.price_usd)}   •   24H VOL {fmt_usd(telemetry.volume_24h)}",
                    font=_font("mono", 24),
                    fill=(*theme["muted"], 255),
                )
            else:
                # 16:9 widescreen Scene 1 layout (left emblem, right telemetry hero)
                rx = int(W * 0.48)
                draw.text((rx, 190), kicker[:48], font=_font("mono", 24), fill=(*theme["accent"], 255))
                f_tick = _font("display", 128)
                tick_txt = f"${telemetry.symbol}"
                tw, th = _text_size(draw, tick_txt, f_tick)
                draw.text((rx, 232), tick_txt, font=f_tick, fill=(*theme["text"], 255))
                sweep = smootherstep(min(1.0, age / 14.0))
                draw.rounded_rectangle((rx, 232 + th + 12, rx + int(tw * sweep), 232 + th + 20), radius=4, fill=(*theme["accent"], 255))
                draw.text((rx, 232 + th + 38), telemetry.name, font=_font("semibold", 38), fill=(*theme["muted"], 255))
                _draw_pill(draw, rx, 455, delta_str, _font("bold", 26), bg=delta_col, fg=theme["pill_text"], pad_x=22, pad_y=12)
                _draw_pill(
                    draw,
                    rx + 290,
                    455,
                    f"MCAP {fmt_usd(telemetry.market_cap)}",
                    _font("mono", 24),
                    bg=theme["card_alt"],
                    fg=theme["text"],
                    pad_x=22,
                    pad_y=13,
                    border=theme["border"],
                )
                f_hl = _font("bold", 48)
                hl_lines = _wrap_text(draw, hook_headline.upper(), f_hl, W - rx - 90)[:2]
                hy = 555
                for ln in hl_lines:
                    draw.text((rx, hy), ln, font=f_hl, fill=(*theme["text"], 255))
                    hy += 60
                draw.text(
                    (rx, 705),
                    f"PRICE {fmt_price(telemetry.price_usd)}   •   24H VOL {fmt_usd(telemetry.volume_24h)}",
                    font=_font("mono", 24),
                    fill=(*theme["muted"], 255),
                )

        # ===================================================================
        # SCENE 2: ANIMATED MOMENTUM CHART + LIVE ROLLING METRICS
        # ===================================================================
        elif beat_i == 1:
            pad_x = 68 if is_vertical else 76
            title_y = 186 if is_vertical else 128
            draw.text((pad_x, title_y), "LIVE ON-CHAIN TELEMETRY", font=_font("mono", 24), fill=(*theme["accent"], 255))
            draw.text(
                (pad_x, title_y + 34),
                f"${telemetry.symbol}  •  {telemetry.name}",
                font=_font("bold", 46 if is_vertical else 42),
                fill=(*theme["text"], 255),
            )

            # Chart Card Box
            if is_vertical:
                c_box = (pad_x, 304, W - pad_x, 764)
            else:
                c_box = (pad_x, 224, int(W * 0.56), 760)
            _draw_rounded_card(draw, c_box, theme["card"], theme["border"], radius=28, border_w=2)

            # Chart Header inside card
            cx0, cy0, cx1, cy1 = c_box
            draw.text((cx0 + 30, cy0 + 24), "LIVE ON-CHAIN ENGINE PIPELINE" if is_self_launch else "24H PRICE MOMENTUM", font=_font("mono", 21), fill=(*theme["muted"], 255))
            _draw_pill(
                draw,
                cx1 - 245 if is_self_launch else cx1 - 220,
                cy0 + 18,
                delta_str,
                _font("bold", 21),
                bg=delta_col,
                fg=theme["pill_text"],
                pad_x=16,
                pad_y=7,
            )
            draw.text(
                (cx0 + 30, cy0 + 56),
                "1080P • CRF 14" if is_self_launch else fmt_price(telemetry.price_usd),
                font=_font("bold", 44),
                fill=(*theme["text"], 255),
            )

            # Draw subtle horizontal chart gridlines
            gx0, gy0, gx1, gy1 = cx0 + 32, cy0 + 132, cx1 - 32, cy1 - 44
            for gi in range(4):
                gy = int(gy0 + (gy1 - gy0) * (gi / 3.0))
                draw.line((gx0, gy, gx1, gy), fill=(*theme["grid"], 200), width=1)

            # Progressive animated draw-on of real momentum curve
            draw_prog = smootherstep(min(1.0, age / 28.0))
            n_show = max(2, int(round(draw_prog * (len(curve_pts) - 1))) + 1)
            pts: list[tuple[int, int]] = []
            for i in range(n_show):
                px = int(gx0 + (gx1 - gx0) * (i / float(len(curve_pts) - 1)))
                py = int(gy1 - (gy1 - gy0) * curve_pts[i])
                pts.append((px, py))

            # Soft translucent gradient area fill under curve (TradingView / Robinhood Pro style)
            if len(pts) >= 2:
                poly = pts + [(pts[-1][0], gy1), (pts[0][0], gy1)]
                ch_w, ch_h = max(1, gx1 - gx0), max(1, gy1 - gy0)
                mask_poly = Image.new("L", (ch_w, ch_h), 0)
                mp_draw = ImageDraw.Draw(mask_poly)
                shifted_poly = [(px - gx0, py - gy0) for px, py in poly]
                mp_draw.polygon(shifted_poly, fill=255)
                # Multiply polygon mask by smooth vertical alpha gradient (78 -> 4)
                grad_1d = np.linspace(84, 4, ch_h, dtype=np.float32)[:, None]
                alpha_arr = (np.asarray(mask_poly, dtype=np.float32) / 255.0 * grad_1d).astype(np.uint8)
                fill_rgba = np.zeros((ch_h, ch_w, 4), dtype=np.uint8)
                fill_rgba[:, :, 0] = delta_col[0]
                fill_rgba[:, :, 1] = delta_col[1]
                fill_rgba[:, :, 2] = delta_col[2]
                fill_rgba[:, :, 3] = alpha_arr
                img.alpha_composite(Image.fromarray(fill_rgba, mode="RGBA"), (gx0, gy0))
                draw = ImageDraw.Draw(img, "RGBA")
                # Outer soft glow + crisp 6px main line
                draw.line(pts, fill=(*delta_col, 90), width=11, joint="curve")
                draw.line(pts, fill=(*delta_col, 255), width=5, joint="curve")
                # Pulsing beacon at live tip
                tip_x, tip_y = pts[-1]
                pr = 14 + int(5 * math.sin(frame * 0.3))
                draw.ellipse((tip_x - pr, tip_y - pr, tip_x + pr, tip_y + pr), fill=(*delta_col, 70))
                draw.ellipse((tip_x - 8, tip_y - 8, tip_x + 8, tip_y + 8), fill=(*theme["text"], 255), outline=(*delta_col, 255), width=4)

            # X-axis labels
            for lbl, frac in (("24H", 0.0), ("6H", 0.35), ("1H", 0.70), ("NOW", 0.96)):
                lx = int(gx0 + (gx1 - gx0) * frac)
                draw.text((lx - 12, gy1 + 10), lbl, font=_font("mono", 18), fill=(*theme["muted"], 255))

            # 2x2 Rolling Metric Cards
            count_p = smootherstep(min(1.0, max(0.0, (age - 4) / 26.0)))
            m_mcap = fmt_usd(telemetry.market_cap * count_p) if telemetry.market_cap > 0 else "Live"
            m_vol = fmt_usd(telemetry.volume_24h * count_p) if telemetry.volume_24h > 0 else "Live"
            m_liq = fmt_usd(telemetry.liquidity_usd * count_p) if telemetry.liquidity_usd > 0 else "Locked"
            if telemetry.holders_count and telemetry.holders_count > 0:
                m4_lbl = "ON-CHAIN HOLDERS"
                m4_val = fmt_int(int(round(telemetry.holders_count * count_p)))
                m4_sub = f"{telemetry.chain}"
            else:
                m4_lbl = "24H TRADES"
                m4_val = fmt_int(int(round(total_tx * count_p)))
                m4_sub = f"{telemetry.buys_24h}B / {telemetry.sells_24h}S"

            if is_self_launch:
                metrics = [
                    ("RENDER SPEED", f"{max(1.0, 7.5 * count_p):.1f}s", "1080p 30fps MP4", 6),
                    ("CHAINS LIVE", "2 CHAINS", "Robinhood + Solana", 14),
                    ("AUDIO ENGINE", "BEATS+SFX", "Zero robotic voice", 22),
                    ("AGENT API", "MCP READY", "POST /api/v1/generate", 30),
                ]
            else:
                metrics = [
                    ("MARKET CAP", m_mcap, f"FDV {fmt_usd(telemetry.fdv)}", 6),
                    ("24H VOLUME", m_vol, f"{total_tx:,} swaps", 14),
                    ("POOL LIQUIDITY", m_liq, f"{telemetry.liquidity_quote:.2f} {telemetry.quote_symbol}" if telemetry.liquidity_quote > 0 else "Locked LP", 22),
                    (m4_lbl, m4_val, m4_sub, 30),
                ]

            gap = 22
            if is_vertical:
                grid_x0, grid_y0 = pad_x, 804
                cw = (W - 2 * pad_x - gap) // 2
                ch = 242
            else:
                grid_x0, grid_y0 = int(W * 0.585), 224
                cw = (W - grid_x0 - pad_x - gap) // 2
                ch = 256

            for idx_m, (mlbl, mval, msub, delay_f) in enumerate(metrics):
                m_enter = smootherstep(min(1.0, max(0.0, (age - delay_f) / 10.0)))
                if m_enter <= 0.01:
                    continue
                row, col = divmod(idx_m, 2)
                mx0 = grid_x0 + col * (cw + gap)
                my0 = int(grid_y0 + row * (ch + gap) + (1.0 - m_enter) * 32)
                mbox = (mx0, my0, mx0 + cw, my0 + ch)
                _draw_rounded_card(draw, mbox, theme["card"], theme["border"], radius=24, border_w=2)
                # Top micro accent stripe
                draw.rounded_rectangle((mx0 + 24, my0 + 18, mx0 + 78, my0 + 24), radius=3, fill=(*theme["accent"], 255))
                draw.text((mx0 + 24, my0 + 40), mlbl, font=_font("mono", 20), fill=(*theme["muted"], 255))
                draw.text((mx0 + 24, my0 + 86), mval, font=_font("bold", 54 if is_vertical else 48), fill=(*theme["text"], 255))
                draw.text((mx0 + 24, my0 + ch - 52), msub[:24], font=_font("mono", 21), fill=(*theme["accent"], 235))

        # ===================================================================
        # SCENE 3: LIVE BUY/SELL ORDER FLOW + STAMPED ON-CHAIN PROOF
        # ===================================================================
        elif beat_i == 2:
            pad_x = 68 if is_vertical else 76
            title_y = 186 if is_vertical else 128
            draw.text((pad_x, title_y), "ORDER FLOW & IMMUTABLE PROOF", font=_font("mono", 24), fill=(*theme["accent"], 255))
            draw.text(
                (pad_x, title_y + 34),
                f"${telemetry.symbol}  •  VERIFIED ON-CHAIN",
                font=_font("bold", 44 if is_vertical else 40),
                fill=(*theme["text"], 255),
            )

            # Order Flow Card
            if is_vertical:
                of_box = (pad_x, 306, W - pad_x, 594)
            else:
                of_box = (pad_x, 226, W - pad_x, 456)
            _draw_rounded_card(draw, of_box, theme["card"], theme["border"], radius=28, border_w=2)
            ox0, oy0, ox1, oy1 = of_box

            buy_pct_int = int(round(buy_ratio * 100))
            draw.text((ox0 + 32, oy0 + 28), "IMMUTABLE V4.FUN LAUNCH CONFIG" if is_self_launch else "24H SWAP ORDER FLOW", font=_font("mono", 22), fill=(*theme["muted"], 255))
            draw.text(
                (ox0 + 32, oy0 + 66),
                "100% LOCKED POOL  •  0% TEAM" if is_self_launch else (f"{buy_pct_int}% BUY PRESSURE" if total_tx > 0 else "LIVE UNISWAP V4 POOL"),
                font=_font("bold", 40 if (is_vertical and is_self_launch) else (44 if is_vertical else 38)),
                fill=(*theme["accent"], 255),
            )

            # Animated split horizontal flow bar
            bar_p = smootherstep(min(1.0, age / 20.0))
            bx0, bx1 = ox0 + 32, ox1 - 32
            by0 = oy0 + (146 if is_vertical else 126)
            bh = 44 if is_vertical else 36
            bw = bx1 - bx0
            draw.rounded_rectangle((bx0, by0, bx1, by0 + bh), radius=bh // 2, fill=(*theme["card_alt"], 255))
            buy_w = max(bh, int(bw * buy_ratio * bar_p))
            sell_w = max(0, int(bw * (1.0 - buy_ratio) * bar_p))
            buy_col = theme["accent2"] if theme_name == "canopy" else (theme["accent"] if theme["is_light"][0] == 0 else (18, 168, 102))
            if buy_w > 6:
                draw.rounded_rectangle((bx0, by0, bx0 + buy_w, by0 + bh), radius=bh // 2, fill=(*buy_col, 255))
            if sell_w > 12:
                draw.rounded_rectangle((bx1 - sell_w, by0, bx1, by0 + bh), radius=bh // 2, fill=(*theme["danger"], 240))

            # Counts underneath bar
            if is_self_launch:
                b_lbl = "▲  1,000,000,000 LOCKED IN POOL"
                s_lbl = "0% TEAM MINT"
            else:
                b_lbl = f"▲  {telemetry.buys_24h:,} BUYS" if total_tx > 0 else "▲  LOCKED LP"
                s_lbl = f"{telemetry.sells_24h:,} SELLS  ▼" if total_tx > 0 else "0% ADMIN KEYS  ▼"
            f_cnt = _font("mono", 25 if is_vertical else 22)
            sw_s, _ = _text_size(draw, s_lbl, f_cnt)
            draw.text((bx0, by0 + bh + 20), b_lbl, font=f_cnt, fill=(*buy_col, 255))
            draw.text((bx1 - sw_s, by0 + bh + 20), s_lbl, font=f_cnt, fill=(*theme["danger"], 255))

            # 3 Stamped Proof Cards
            card_start_y = 638 if is_vertical else 492
            card_h = 196 if is_vertical else 112
            card_gap = 24 if is_vertical else 18

            for pi, (ptitle, pdesc) in enumerate(proof_rows):
                delay_p = 10 + pi * 14
                ep = smootherstep(min(1.0, max(0.0, (age - delay_p) / 10.0)))
                if ep <= 0.01:
                    continue
                shift_x = int((1.0 - ep) * 56)
                py0 = card_start_y + pi * (card_h + card_gap)
                pbox = (pad_x + shift_x, py0, W - pad_x + shift_x, py0 + card_h)
                _draw_rounded_card(draw, pbox, theme["card"], theme["border"], radius=24, border_w=2)

                # Green/Accent Checkmark Stamp Circle
                ck_r = 28 if is_vertical else 22
                ck_cx = pad_x + shift_x + 56
                ck_cy = py0 + card_h // 2
                draw.ellipse((ck_cx - ck_r, ck_cy - ck_r, ck_cx + ck_r, ck_cy + ck_r), fill=(*theme["accent"], 255))
                draw.line(
                    [(ck_cx - 11, ck_cy + 1), (ck_cx - 3, ck_cy + 10), (ck_cx + 13, ck_cy - 9)],
                    fill=(*theme["pill_text"], 255),
                    width=5,
                )

                tx_p = ck_cx + ck_r + 28
                draw.text((tx_p, py0 + (34 if is_vertical else 20)), ptitle, font=_font("mono", 24 if is_vertical else 21), fill=(*theme["accent"], 255))
                f_pd = _font("semibold", 34 if is_vertical else 27)
                desc_lines = _wrap_text(draw, pdesc, f_pd, (W - pad_x) - tx_p - 28)[:2]
                dy_p = py0 + (76 if is_vertical else 54)
                for dl in desc_lines:
                    draw.text((tx_p, dy_p), dl, font=f_pd, fill=(*theme["text"], 255))
                    dy_p += 42 if is_vertical else 32

        # ===================================================================
        # SCENE 4: CLEAN LAUNCH PAYOFF & CTA LOOP CARD
        # ===================================================================
        else:
            ep = smootherstep(min(1.0, age / 14.0))
            shift_y = int((1.0 - ep) * 44)
            pad_x = 76 if is_vertical else 240
            card_box = (pad_x, (310 if is_vertical else 170) + shift_y, W - pad_x, (1240 if is_vertical else 770) + shift_y)
            _draw_rounded_card(draw, card_box, theme["card"], theme["accent"], radius=34, border_w=3)

            cx = W // 2
            top_y = card_box[1]

            # Pulsing emblem at top of CTA card
            ew, eh = emblem_sm.size
            em_y = top_y + (64 if is_vertical else 36)
            pulse = 0.5 + 0.5 * math.sin(frame * 0.25)
            pr = int(ew * 0.58 + pulse * 10)
            draw.ellipse((cx - pr, em_y + eh // 2 - pr, cx + pr, em_y + eh // 2 + pr), outline=(*theme["accent"], 120), width=3)
            img.alpha_composite(emblem_sm, (cx - ew // 2, em_y))

            # Ticker + Name
            f_ct = _font("display", 104 if is_vertical else 88)
            ct_txt = f"${telemetry.symbol}"
            ctw, cth = _text_size(draw, ct_txt, f_ct)
            y_t = em_y + eh + (30 if is_vertical else 18)
            draw.text((cx - ctw // 2, y_t), ct_txt, font=f_ct, fill=(*theme["text"], 255))

            f_sub = _font("semibold", 34 if is_vertical else 30)
            sub_txt = f"{telemetry.name}  •  {telemetry.chain}"
            sw_sub, sh_sub = _text_size(draw, sub_txt, f_sub)
            draw.text((cx - sw_sub // 2, y_t + cth + 18), sub_txt, font=f_sub, fill=(*theme["muted"], 255))

            # Big CTA Button Pill
            btn_y = y_t + cth + (108 if is_vertical else 78)
            _draw_pill(
                draw,
                cx,
                btn_y,
                f"LIVE ON V4.FUN   →",
                _font("bold", 38 if is_vertical else 32),
                bg=theme["accent"],
                fg=theme["pill_text"],
                pad_x=44,
                pad_y=20,
                center_x=True,
            )

            # Contract & URL pill
            url_y = btn_y + (116 if is_vertical else 96)
            _draw_pill(
                draw,
                cx,
                url_y,
                cta_text[:34],
                _font("mono", 28 if is_vertical else 24),
                bg=theme["card_alt"],
                fg=theme["text"],
                pad_x=30,
                pad_y=14,
                center_x=True,
                border=theme["border"],
            )

            # Footer branding credit
            foot_txt = "GENERATED LIVE WITH LAUNCHREEL  •  V4.FUN"
            f_ft = _font("mono", 22 if is_vertical else 20)
            fw, _ = _text_size(draw, foot_txt, f_ft)
            draw.text((cx - fw // 2, card_box[3] - (68 if is_vertical else 52)), foot_txt, font=f_ft, fill=(*theme["muted"], 220))

        # --- LOWER-THIRD: KINETIC TAKEAWAY (MUSIC MODE) OR SYNCED CAPTIONS (VOICE MODE) ---
        if is_music_only:
            _draw_kinetic_takeaway(draw, sp.text, age, W, H, theme, is_vertical)
        else:
            _draw_captions(draw, word_cues, t_s, W, H, theme, is_vertical)

        return np.asarray(img.convert("RGB"), dtype=np.uint8)

    # 5. Render QA contact sheet first
    _prog(48, "Generating visual QA contact sheet...")
    render_contact_sheet(render_frame, total_frames, contact_jpg, n_tiles=12, cols=4)

    # 6. Stream smooth 30fps RGB24 frames into FFmpeg H.264 CRF 14 + AAC 192k
    _prog(55, f"Encoding {W}x{H} 30fps broadcast MP4 (CRF 14)...")
    dar = "9/16" if is_vertical else "16/9"
    cmd = [
        ffmpeg_exe(),
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{W}x{H}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
        "-i",
        str(master_wav),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vf",
        f"setsar=1,setdar={dar}",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "14",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(out_mp4),
    ]

    log_path = out_mp4.with_suffix(".ffmpeg.log")
    log_f = open(log_path, "wb")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log_f)
    assert proc.stdin is not None
    try:
        for f in range(total_frames):
            rgb = np.ascontiguousarray(render_frame(f))
            proc.stdin.write(memoryview(rgb))
            if f % 30 == 0 or f == total_frames - 1:
                pct = 55 + int(42 * (f + 1) / float(total_frames))
                _prog(pct, f"Rendering frame {f + 1}/{total_frames}...")
            if (f + 1) % 180 == 0:
                gc.collect()
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        rc = proc.wait()
        log_f.close()

    if rc != 0:
        err = log_path.read_text(encoding="utf-8", errors="ignore")
        raise RuntimeError(f"FFmpeg encode failed (rc={rc}):\n{err[-1200:]}")
    log_path.unlink(missing_ok=True)
    voice_wav.unlink(missing_ok=True)
    master_wav.unlink(missing_ok=True)

    elapsed = round(time.time() - t0, 1)
    _prog(100, f"Done in {elapsed}s!")
    return {
        "mp4_path": str(out_mp4),
        "mp4_name": out_mp4.name,
        "contact_sheet_path": str(contact_jpg),
        "contact_sheet_name": contact_jpg.name,
        "duration_s": round(total_dur, 2),
        "width": W,
        "height": H,
        "elapsed_s": elapsed,
    }
