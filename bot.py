"""
Degen Boys Club – NFT Collection Generator Bot
Automates https://higgsfield.ai/image/seedream_v5_lite to generate 1000 images.

Usage:
    python bot.py                   # generate all 1000
    python bot.py --start 1 --end 50  # generate NFTs 1-50
    python bot.py --resume          # skip already-downloaded NFTs

Requirements:
    pip install playwright
    playwright install chromium     # or use: --chrome-path /path/to/chrome
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from traits import generate_collection

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

TARGET_URL = "https://higgsfield.ai/image/seedream_v5_lite"
OUTPUT_DIR = Path("output")
IMAGES_DIR = OUTPUT_DIR / "images"
META_DIR   = OUTPUT_DIR / "metadata"
LOG_FILE   = OUTPUT_DIR / "progress.json"

COLLECTION_FILE = OUTPUT_DIR / "collection.json"

# How long (seconds) to wait for image generation to finish
GENERATION_TIMEOUT = 120
# Delay between generations to be polite to the server
DELAY_BETWEEN = 3.0
# Retries if generation fails
MAX_RETRIES = 3


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def load_progress() -> set:
    """Return set of already-completed NFT IDs."""
    if LOG_FILE.exists():
        with open(LOG_FILE) as f:
            return set(json.load(f).get("completed", []))
    return set()


def save_progress(completed: set):
    with open(LOG_FILE, "w") as f:
        json.dump({"completed": sorted(completed)}, f)


def load_or_generate_collection(size: int = 1000) -> list:
    if COLLECTION_FILE.exists():
        print(f"[+] Loading existing collection from {COLLECTION_FILE}")
        with open(COLLECTION_FILE) as f:
            return json.load(f)
    print(f"[+] Generating new collection of {size} NFTs...")
    collection = generate_collection(size)
    COLLECTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(COLLECTION_FILE, "w") as f:
        json.dump(collection, f, indent=2)
    print(f"[+] Collection saved to {COLLECTION_FILE}")
    return collection


# ─────────────────────────────────────────────
# BROWSER AUTOMATION
# ─────────────────────────────────────────────

async def setup_browser(playwright, headless: bool = False, chrome_path: str = None):
    """Launch Chromium (or real Chrome). headless=False shows the browser window."""
    launch_kwargs = dict(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
        slow_mo=100,  # slight humanizing delay
    )
    if chrome_path:
        launch_kwargs["executable_path"] = chrome_path

    browser = await playwright.chromium.launch(**launch_kwargs)
    context = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    )
    page = await context.new_page()
    return browser, context, page


async def login_if_needed(page):
    """
    Higgsfield requires a logged-in account.
    If you're not logged in, the bot will pause and let you log in manually.
    After logging in, press ENTER in the terminal.
    """
    await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)

    # Detect login wall (adjust selector if the site changes)
    login_indicators = ["sign in", "log in", "create account", "get started"]
    page_text = (await page.content()).lower()

    if any(indicator in page_text for indicator in login_indicators):
        # Check if we're on the actual generation page
        if "seedream" not in page.url.lower() and "image" not in page.url.lower():
            print("\n" + "="*60)
            print("  LOGIN REQUIRED")
            print("  The browser is open. Please log in to Higgsfield.ai")
            print("  manually, then press ENTER here to continue...")
            print("="*60 + "\n")
            input("  Press ENTER after logging in > ")
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)


async def find_prompt_input(page):
    """Find the prompt textarea on the page."""
    # Try common selectors
    selectors = [
        "textarea[placeholder*='prompt' i]",
        "textarea[placeholder*='describe' i]",
        "textarea[placeholder*='enter' i]",
        "div[contenteditable='true']",
        "textarea",
    ]
    for sel in selectors:
        el = page.locator(sel).first
        if await el.count() > 0:
            return el
    return None


async def find_generate_button(page):
    """Find the Generate / Create button."""
    selectors = [
        "button:has-text('Generate')",
        "button:has-text('Create')",
        "button:has-text('Run')",
        "button[type='submit']",
        "[data-testid*='generate' i]",
        "[aria-label*='generate' i]",
    ]
    for sel in selectors:
        el = page.locator(sel).first
        if await el.count() > 0:
            return el
    return None


async def wait_for_image(page, timeout: int = GENERATION_TIMEOUT):
    """
    Wait until a newly generated image appears in the output area.
    Returns the image URL or None on timeout.
    """
    start = time.time()

    # Selectors that typically wrap generated images on AI image sites
    image_selectors = [
        "img[src*='cdn']",
        "img[src*='result']",
        "img[src*='generated']",
        "img[src*='output']",
        "img[src*='blob:']",
        "[data-testid*='result'] img",
        ".result img",
        ".output img",
        ".generated img",
        "canvas",
    ]

    print("    Waiting for image generation", end="", flush=True)

    while time.time() - start < timeout:
        await asyncio.sleep(2)
        print(".", end="", flush=True)

        for sel in image_selectors:
            elements = await page.locator(sel).all()
            for el in elements:
                src = await el.get_attribute("src")
                if src and src.startswith("http") and len(src) > 30:
                    print(f" done ({int(time.time()-start)}s)")
                    return src

    print(" TIMEOUT")
    return None


async def download_image(page, img_url: str, dest_path: Path):
    """Download image from URL to local file."""
    try:
        if img_url.startswith("blob:"):
            # Handle blob URLs via JS
            data = await page.evaluate("""
                async (url) => {
                    const resp = await fetch(url);
                    const blob = await resp.blob();
                    return new Promise((resolve) => {
                        const reader = new FileReader();
                        reader.onloadend = () => resolve(reader.result);
                        reader.readAsDataURL(blob);
                    });
                }
            """, img_url)
            # data is base64 data-url
            header, b64 = data.split(",", 1)
            import base64
            dest_path.write_bytes(base64.b64decode(b64))
        else:
            # Direct download via Playwright's fetch
            response = await page.request.get(img_url)
            dest_path.write_bytes(await response.body())
        return True
    except Exception as e:
        print(f"    [!] Download error: {e}")
        return False


async def generate_single_nft(page, nft: dict, output_path: Path) -> bool:
    """
    Submit one NFT prompt and save the resulting image.
    Returns True on success.
    """
    prompt = nft["prompt"]
    nft_id = nft["id"]

    print(f"\n[{nft_id:04d}/{1000}] {nft['name']} ({nft['rarity']})")
    print(f"  Traits: {nft['traits']['headwear']} | {nft['traits']['eyes']} | "
          f"{nft['traits']['outfit']}")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # ── Navigate / refresh page ──
            if attempt > 1 or nft_id % 10 == 1:
                await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2)

            # ── Find + fill prompt ──
            inp = await find_prompt_input(page)
            if inp is None:
                print(f"  [!] Can't find prompt input (attempt {attempt})")
                await asyncio.sleep(3)
                continue

            await inp.click(timeout=5000)
            await inp.fill("")          # clear existing text
            await inp.type(prompt, delay=10)
            await asyncio.sleep(0.5)

            # ── Click Generate ──
            btn = await find_generate_button(page)
            if btn is None:
                print(f"  [!] Can't find generate button (attempt {attempt})")
                await asyncio.sleep(3)
                continue

            await btn.click(timeout=5000)

            # ── Wait for result ──
            img_url = await wait_for_image(page, GENERATION_TIMEOUT)
            if img_url is None:
                print(f"  [!] No image generated (attempt {attempt})")
                continue

            # ── Download ──
            ok = await download_image(page, img_url, output_path)
            if ok:
                print(f"  [+] Saved to {output_path}")
                return True

        except PWTimeout:
            print(f"  [!] Timeout (attempt {attempt})")
        except Exception as e:
            print(f"  [!] Error: {e} (attempt {attempt})")

        await asyncio.sleep(5)

    print(f"  [x] FAILED after {MAX_RETRIES} attempts – skipping")
    return False


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

async def run(args):
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)

    collection = load_or_generate_collection(1000)
    completed  = load_progress() if args.resume else set()

    # Slice collection to requested range
    nfts = [n for n in collection
            if args.start <= n["id"] <= args.end
            and n["id"] not in completed]

    if not nfts:
        print("[+] Nothing to generate (all done or range empty).")
        return

    print(f"[+] Generating {len(nfts)} NFTs  "
          f"(#{args.start}–#{args.end}, {len(completed)} already done)")
    print(f"[+] Output → {OUTPUT_DIR.resolve()}")

    async with async_playwright() as pw:
        browser, context, page = await setup_browser(
            pw,
            headless=args.headless,
            chrome_path=args.chrome_path or None,
        )

        try:
            await login_if_needed(page)
            ok_count = 0
            fail_count = 0

            for nft in nfts:
                img_path  = IMAGES_DIR / f"{nft['id']:04d}.png"
                meta_path = META_DIR   / f"{nft['id']:04d}.json"

                success = await generate_single_nft(page, nft, img_path)

                if success:
                    # Save metadata
                    meta = {
                        "id":          nft["id"],
                        "name":        nft["name"],
                        "description": nft["traits"]["story"],
                        "image":       f"images/{nft['id']:04d}.png",
                        "rarity":      nft["rarity"],
                        "attributes": [
                            {"trait_type": "Background",     "value": nft["traits"]["background"]},
                            {"trait_type": "Headwear",       "value": nft["traits"]["headwear"]},
                            {"trait_type": "Eyes",           "value": nft["traits"]["eyes"]},
                            {"trait_type": "Mouth",          "value": nft["traits"]["mouth"]},
                            {"trait_type": "Outfit",         "value": nft["traits"]["outfit"]},
                            {"trait_type": "Accessory",      "value": nft["traits"]["accessory"]},
                            {"trait_type": "Rare Feature",   "value": nft["traits"]["rare_feature"]},
                            {"trait_type": "Degen Phase",    "value": nft["traits"]["degen_phase"]},
                            {"trait_type": "Meme Reference", "value": nft["traits"]["meme_reference"]},
                        ],
                        "prompt": nft["prompt"],
                    }
                    with open(meta_path, "w") as f:
                        json.dump(meta, f, indent=2)

                    completed.add(nft["id"])
                    save_progress(completed)
                    ok_count += 1
                else:
                    fail_count += 1

                await asyncio.sleep(DELAY_BETWEEN)

        finally:
            await browser.close()

    print(f"\n{'='*50}")
    print(f"  Done!  Generated: {ok_count}  Failed: {fail_count}")
    print(f"  Images   → {IMAGES_DIR.resolve()}")
    print(f"  Metadata → {META_DIR.resolve()}")
    print(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(
        description="Degen Boys Club NFT Collection Generator Bot"
    )
    parser.add_argument("--start",       type=int,  default=1,
                        help="First NFT ID to generate (default: 1)")
    parser.add_argument("--end",         type=int,  default=1000,
                        help="Last NFT ID to generate (default: 1000)")
    parser.add_argument("--resume",      action="store_true",
                        help="Skip NFTs already downloaded")
    parser.add_argument("--headless",    action="store_true",
                        help="Run browser in headless mode (no window)")
    parser.add_argument("--chrome-path", type=str,  default=None,
                        help="Path to Chrome/Chromium executable (optional)")
    parser.add_argument("--delay",       type=float, default=DELAY_BETWEEN,
                        help=f"Seconds to wait between generations (default: {DELAY_BETWEEN})")
    args = parser.parse_args()

    # Allow overriding delay globally
    global DELAY_BETWEEN
    DELAY_BETWEEN = args.delay

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
