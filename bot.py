"""
Degen Boys Club – NFT Collection Generator Bot
Automates https://higgsfield.ai/image/seedream_v5_lite to generate 1000 images.

Flow per NFT:
  1. Select model: SeedDream 5.0 Lite  (once per page load)
  2. Set resolution: 3K                (once per page load)
  3. Set aspect ratio: 1:1             (once per page load)
  4. Enable Unlimited mode             (once per page load)
  5. Clear prompt field + type new prompt
  6. Click the green Unlimited Generate button
  7. Wait 15 seconds → go to step 5 for next NFT

Images generate on Higgsfield's servers and are saved to your account.
No local downloading – just rapid prompt submission.

Usage:
    python bot.py --login              # first run (pause to log in)
    python bot.py --login --resume     # resume after a break
    python bot.py --start 1 --end 100  # specific range
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from traits import generate_collection

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

TARGET_URL = "https://higgsfield.ai/image/seedream_v5_lite"

OUTPUT_DIR      = Path("output")
META_DIR        = OUTPUT_DIR / "metadata"
LOG_FILE        = OUTPUT_DIR / "progress.json"
COLLECTION_FILE = OUTPUT_DIR / "collection.json"

DELAY_BETWEEN = 15.0   # seconds to wait after clicking Generate
MAX_RETRIES   = 3
# Page reload interval (keep session alive)
RELOAD_EVERY  = 50


# ─────────────────────────────────────────────
# PROGRESS TRACKING
# ─────────────────────────────────────────────

def load_progress() -> set:
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
# BROWSER SETUP
# ─────────────────────────────────────────────

async def setup_browser(playwright, headless: bool = False, chrome_path: str = None):
    launch_kwargs = dict(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--start-maximized",
        ],
        slow_mo=80,
    )
    if chrome_path:
        launch_kwargs["executable_path"] = chrome_path

    browser = await playwright.chromium.launch(**launch_kwargs)
    context = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    page = await context.new_page()
    return browser, context, page


# ─────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────

async def login_if_needed(page, force_login: bool = False):
    await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=40000)
    await asyncio.sleep(3)

    needs_login = force_login or ("image" not in page.url and "seedream" not in page.url)

    if needs_login:
        print("\n" + "=" * 60)
        print("  LOGIN STEP")
        print("  The browser window is open at Higgsfield.ai.")
        print()
        print("  1. Log in to your Higgsfield account in the browser.")
        print("  2. Make sure you can see the image generator page.")
        print("  3. Come back here and press ENTER to start generating.")
        print("=" * 60 + "\n")
        input("  Press ENTER when you are logged in and ready > ")
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(3)

    print("[+] On Higgsfield image page. Configuring settings...")


# ─────────────────────────────────────────────
# PAGE SETTINGS  (run after every page load)
# ─────────────────────────────────────────────

async def _try_click(page, selectors: list[str], label: str) -> bool:
    for sel in selectors:
        el = page.locator(sel).first
        if await el.count() > 0:
            try:
                await el.click(timeout=4000)
                print(f"  [cfg] {label} selected.")
                return True
            except Exception:
                pass
    print(f"  [!]  Could not set {label} – continuing.")
    return False


async def configure_page_settings(page):
    print("  [cfg] Selecting model: SeedDream 5.0 Lite")
    await _try_click(page, [
        "button:has-text('SeedDream')",
        "button:has-text('Lite')",
        "[data-testid*='model']",
    ], "model trigger")
    await asyncio.sleep(0.5)
    await _try_click(page, [
        "li:has-text('5.0 Lite')",
        "[role='option']:has-text('Lite')",
        "div:has-text('SeedDream 5.0 Lite')",
        "button:has-text('5.0 Lite')",
    ], "SeedDream 5.0 Lite")
    await asyncio.sleep(0.5)

    print("  [cfg] Setting resolution: 3K")
    await _try_click(page, [
        "button:has-text('3K')",
        "label:has-text('3K')",
        "span:has-text('3K')",
        "div[role='button']:has-text('3K')",
    ], "3K resolution")
    await asyncio.sleep(0.5)

    print("  [cfg] Setting aspect ratio: 1:1")
    await _try_click(page, [
        "button:has-text('1:1')",
        "label:has-text('1:1')",
        "[aria-label='1:1']",
        "div[role='button']:has-text('1:1')",
    ], "1:1 ratio")
    await asyncio.sleep(0.5)

    print("  [cfg] Enabling Unlimited mode")
    unlimited_done = False
    for sel in [
        "label:has-text('Unlimited') input[type='checkbox']",
        "label:has-text('Unlimited') [role='switch']",
        "[role='switch'][aria-label*='Unlimited' i]",
        ":text('Unlimited') ~ input[type='checkbox']",
        ":text('Unlimited') + [role='switch']",
    ]:
        el = page.locator(sel).first
        try:
            if await el.count() > 0:
                aria = await el.get_attribute("aria-checked")
                checked = await el.is_checked() if (await el.get_attribute("type")) == "checkbox" else False
                if aria == "true" or checked:
                    print("  [cfg] Unlimited already ON.")
                    unlimited_done = True
                    break
                await el.click(timeout=4000)
                print("  [cfg] Unlimited toggled ON.")
                unlimited_done = True
                break
        except Exception:
            pass

    if not unlimited_done:
        await _try_click(page, ["button:has-text('Unlimited')"], "Unlimited fallback")

    await asyncio.sleep(0.5)
    print("  [cfg] Settings configured.\n")


# ─────────────────────────────────────────────
# PROMPT INPUT
# ─────────────────────────────────────────────

async def find_prompt_input(page):
    for sel in [
        "textarea[placeholder*='prompt' i]",
        "textarea[placeholder*='describe' i]",
        "textarea[placeholder*='enter' i]",
        "textarea[placeholder*='type' i]",
        "div[contenteditable='true']",
        "textarea",
    ]:
        el = page.locator(sel).first
        if await el.count() > 0:
            return el
    return None


async def clear_and_type(inp, prompt: str):
    """Reliably clear any input field and type the new prompt."""
    await inp.click(timeout=5000)
    await asyncio.sleep(0.1)

    tag = (await inp.evaluate("el => el.tagName")).lower()
    is_ce = (tag in ("div", "span")
             or await inp.get_attribute("contenteditable") == "true")

    if is_ce:
        await inp.evaluate("el => { el.innerText = ''; el.textContent = ''; }")
        await inp.press("Control+a")
        await inp.press("Delete")
        await asyncio.sleep(0.1)
        remaining = (await inp.inner_text()).strip()
    else:
        await inp.press("Control+a")
        await inp.press("Delete")
        await inp.fill("")
        await asyncio.sleep(0.1)
        remaining = (await inp.input_value()).strip()

    if remaining:
        await inp.press("Control+a")
        await inp.press("Backspace")
        await asyncio.sleep(0.1)

    await inp.type(prompt, delay=8)


# ─────────────────────────────────────────────
# GENERATE BUTTON
# ─────────────────────────────────────────────

async def find_generate_button(page):
    for sel in [
        "button:has-text('Unlimited')",
        "button[class*='unlimited' i]",
        "button:has-text('Generate')",
        "button:has-text('Create')",
        "button[type='submit']",
    ]:
        el = page.locator(sel).first
        if await el.count() > 0:
            return el
    return None


# ─────────────────────────────────────────────
# SINGLE NFT  –  submit prompt, don't wait for render
# ─────────────────────────────────────────────

async def submit_nft(page, nft: dict, settings_ok: bool) -> tuple[bool, bool]:
    """
    Type the prompt and click Generate.
    Does NOT wait for the image to finish rendering.
    Returns (success, settings_ok).
    """
    nft_id = nft["id"]
    prompt = nft["prompt"]

    print(f"\n[{nft_id:04d}/1000] {nft['name']} ({nft['rarity']})")
    print(f"  {nft['traits']['headwear']} | {nft['traits']['eyes']} | {nft['traits']['outfit']}")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Reload page every RELOAD_EVERY NFTs or on retry
            if attempt > 1 or nft_id % RELOAD_EVERY == 1:
                print(f"  [→] Loading page...")
                await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=40000)
                await asyncio.sleep(3)
                settings_ok = False

            if not settings_ok:
                await configure_page_settings(page)
                settings_ok = True

            # ── Clear + fill prompt ──
            inp = await find_prompt_input(page)
            if inp is None:
                print(f"  [!] Prompt input not found (attempt {attempt})")
                settings_ok = False
                await asyncio.sleep(2)
                continue

            await clear_and_type(inp, prompt)
            await asyncio.sleep(0.3)

            # ── Click Generate ──
            btn = await find_generate_button(page)
            if btn is None:
                print(f"  [!] Generate button not found (attempt {attempt})")
                settings_ok = False
                await asyncio.sleep(2)
                continue

            print(f"  [→] Clicking generate...")
            await btn.click(timeout=6000)
            print(f"  [✓] Generation started!")
            return True, settings_ok

        except PWTimeout:
            print(f"  [!] Timeout (attempt {attempt})")
            settings_ok = False
        except Exception as e:
            print(f"  [!] Error: {e} (attempt {attempt})")
            settings_ok = False

        await asyncio.sleep(3)

    print(f"  [x] FAILED after {MAX_RETRIES} attempts – skipping.")
    return False, settings_ok


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

async def run(args):
    global DELAY_BETWEEN
    META_DIR.mkdir(parents=True, exist_ok=True)

    collection = load_or_generate_collection(1000)
    completed  = load_progress() if args.resume else set()

    nfts = [n for n in collection
            if args.start <= n["id"] <= args.end
            and n["id"] not in completed]

    if not nfts:
        print("[+] Nothing to generate (all done or range empty).")
        return

    print(f"[+] Submitting {len(nfts)} prompts  "
          f"(#{args.start}–#{args.end}, {len(completed)} already done)")
    print(f"[+] Delay between submissions: {int(DELAY_BETWEEN)}s")
    print(f"[+] Metadata → {META_DIR.resolve()}\n")

    async with async_playwright() as pw:
        browser, context, page = await setup_browser(
            pw,
            headless=args.headless,
            chrome_path=args.chrome_path or None,
        )

        try:
            await login_if_needed(page, force_login=args.login)

            ok_count   = 0
            fail_count = 0
            settings_ok = False

            for i, nft in enumerate(nfts):
                meta_path = META_DIR / f"{nft['id']:04d}.json"

                success, settings_ok = await submit_nft(page, nft, settings_ok)

                if success:
                    # Save prompt + trait metadata (no image file)
                    meta = {
                        "id":          nft["id"],
                        "name":        nft["name"],
                        "description": nft["traits"]["story"],
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

                # ── 15s cooldown before next prompt ──
                if i < len(nfts) - 1:
                    print(f"  [⏳] Waiting {int(DELAY_BETWEEN)}s...")
                    await asyncio.sleep(DELAY_BETWEEN)

        finally:
            await browser.close()

    print(f"\n{'='*52}")
    print(f"  Done!   Submitted: {ok_count}   Failed: {fail_count}")
    print(f"  Metadata → {META_DIR.resolve()}")
    print(f"{'='*52}")


def main():
    global DELAY_BETWEEN
    parser = argparse.ArgumentParser(
        description="Degen Boys Club – NFT Collection Generator (Higgsfield.ai)"
    )
    parser.add_argument("--start",       type=int,   default=1,
                        help="First NFT ID (default: 1)")
    parser.add_argument("--end",         type=int,   default=1000,
                        help="Last NFT ID (default: 1000)")
    parser.add_argument("--resume",      action="store_true",
                        help="Skip already-submitted NFTs")
    parser.add_argument("--login",       action="store_true",
                        help="Pause at startup so you can log in manually")
    parser.add_argument("--headless",    action="store_true",
                        help="Run browser without visible window")
    parser.add_argument("--chrome-path", type=str,   default=None,
                        help="Path to Chrome/Chromium executable")
    parser.add_argument("--delay",       type=float, default=DELAY_BETWEEN,
                        help=f"Seconds between submissions (default: {DELAY_BETWEEN})")
    args = parser.parse_args()

    DELAY_BETWEEN = args.delay
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
