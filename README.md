# LaunchReel ($REEL)

**Contract Address → 1080p Broadcast Launch Trailer in 8 Seconds.**

Built natively for [**v4.fun**](https://v4.fun), **Robinhood Chain**, and **Solana** projects.

- **Live Web Studio:** https://litlesdk.github.io/launchreel/
- **Token:** `$REEL` (launching on v4.fun — fixed 1B supply, 100% permanently locked Uniswap v4 LP, zero team allocation)

## What LaunchReel Does

Every founder launching on `v4.fun` immediately needs scroll-stopping X videos and daily market recaps to drive volume — and AI agents launching autonomously via Canopy's **Agen MCP** need programmatic video marketing.

1. **Live On-Chain Telemetry:** Pulls real-time data from **DexScreener** and **Robinhood Chain Blockscout** (price, MCAP, 24h volume, liquidity, holders, 24h buy/sell pressure, immutable pool details).
2. **Broadcast 1080p Rendering (`CRF 14`, `30fps`, `9:16` & `16:9`):** Generates kinetic fintech motion graphics synced to dark fintech beats + studio Foley SFX (zero robotic AI voice) in under 10 seconds.
3. **Agen MCP / REST API (`POST /api/v1/generate`):** Autonomous agents can call one endpoint immediately after deploying a token on `v4.fun` to generate and publish a launch trailer.

## Run Full 1080p FFmpeg Studio Locally

```bash
pip install requests pillow opencv-python numpy scipy soundfile static-ffmpeg
python -m launchreel.server
# Opens http://127.0.0.1:8420
```
