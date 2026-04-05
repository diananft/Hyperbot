# Degen Boys Club – NFT Collection Generator Bot

Automates [Higgsfield AI SeedDream v5 Lite](https://higgsfield.ai/image/seedream_v5_lite)
to generate **1 000 unique pixel-art Crypto Degen avatars** in your Chrome browser.

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Run the bot

```bash
# Generate all 1000 NFTs (opens a real browser window)
python bot.py

# Generate a specific range (e.g. first 50)
python bot.py --start 1 --end 50

# Resume from where you left off (skip already-saved images)
python bot.py --resume

# Use your installed Chrome instead of bundled Chromium
python bot.py --chrome-path "C:\Program Files\Google\Chrome\Application\chrome.exe"
# macOS:
python bot.py --chrome-path "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Headless mode (no visible browser window)
python bot.py --headless
```

### 3. Check progress & stats

```bash
python metadata.py summary          # rarity breakdown, trait stats
python metadata.py collection-json  # export ERC-721 metadata
python metadata.py fix-missing      # list/re-run missing images
```

---

## Output structure

```
output/
├── collection.json          ← all 1000 NFT prompts + traits (generated once)
├── progress.json            ← which IDs are done (used by --resume)
├── nft_collection_metadata.json  ← ERC-721 export (after running metadata.py)
├── images/
│   ├── 0001.png
│   ├── 0002.png
│   └── ...
└── metadata/
    ├── 0001.json
    ├── 0002.json
    └── ...
```

---

## Collection Design

| Trait          | Pool size | Notes |
|---------------|-----------|-------|
| Background     | 12        | neon, galaxy, moon, exchange UI… |
| Headwear       | 13        | DOGE cap, PEPE hat, diamond crown… |
| Eyes           | 13        | laser, FOMO, sunglasses, VR headset… |
| Mouth          | 12        | cigarette, energy drink, cigar (legendary)… |
| Outfit         | 10        | hoodie, diamond jersey, golden armor (rare)… |
| Accessory      | 12        | green-chart phone, Ledger wallet… |
| Rare Feature   | 12        | Diamond Hands, Whale Wallet, 1/1 Rainbow Aura… |
| Degen Phase    | 7         | Noob → FOMO → Diamond Hands → Rug Pull Survivor… |
| Meme Reference | 9         | DOGE, PEPE, BONK, $PNDC… |

**Rarity tiers:** Common · Uncommon · Rare · Epic · Legendary

---

## Notes

- You must be **logged in** to Higgsfield.ai. The bot will pause and let you log in manually if needed.
- Each generation takes ~30-90 seconds. 1000 NFTs ≈ **8-25 hours** depending on server speed.
- Use `--start`/`--end` + `--resume` to run in batches across multiple sessions.
- All prompts and metadata are saved before generation starts, so you never lose trait assignments.
