"""
Degen Boys Club – NFT Collection Generator Bot
Automates https://higgsfield.ai/image/seedream_v5_lite to generate 1000 images.

Higgsfield UI steps per image:
  1. Navigate to the SeedDream v5 Lite page
  2. Select model: SeedDream 5.0 Lite
  3. Set resolution: 3K
  4. Set aspect ratio: 1:1
  5. Enable Unlimited mode (green toggle)
  6. Fill prompt
  7. Click the green "Unlimited" Generate button
  8. Wait for image → download
  9. Wait 15 s before next image

Usage:
    python bot.py                        # generate all 1000
    python bot.py --start 1 --end 50     # generate NFTs 1-50
    python bot.py --resume               # skip already-downloaded NFTs

Requirements:
    pip install playwright
    python -m playwright install chromium
"""

import argparse
import asyncio
import base64
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
IMAGES_DIR      = OUTPUT_DIR / "images"
META_DIR        = OUTPUT_DIR / "metadata"
LOG_FILE        = OUTPUT_DIR / "progress.json"
COLLECTION_FILE = OUTPUT_DIR / "collection.json"

GENERATION_TIMEOUT = 180   # seconds to wait for image
DELAY_BETWEEN      = 15.0  # seconds between generations (as requested)
MAX_RETRIES        = 3


# ─────────────────────────────────────────────
# HELPERS
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
# LOGIN GUARD
# ─────────────────────────────────────────────

async def login_if_needed(page, force_login: bool = False):
    """
    Navigate to the tool page.

    If `force_login` is True (--login flag), always pause so the user
    can log in manually — regardless of what the URL looks like.
    Otherwise, only pause if we detect a redirect away from the tool page.
    """
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

        # Go to the tool page in case the user ended up elsewhere
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(3)

    print("[+] On Higgsfield image page. Configuring settings...")


# ─────────────────────────────────────────────
# UI CONFIGURATION  (run once after page load)
# ─────────────────────────────────────────────

async def _click_option_containing(page, text: str, timeout: int = 8000) -> bool:
    """Click any visible element whose text contains `text` (case-insensitive)."""
    loc = page.locator(f"*:has-text('{text}')").last
    try:
        await loc.wait_for(state="visible", timeout=timeout)
        await loc.click()
        return True
    except Exception:
        return False


async def select_model_seedream_lite(page) -> bool:
    """
    Open the model selector and pick 'SeedDream 5.0 Lite' (or closest match).
    Higgsfield shows a model dropdown / pill selector at the top.
    """
    print("  [cfg] Selecting model: SeedDream 5.0 Lite")

    # Common patterns for model selectors on Higgsfield
    model_trigger_selectors = [
        "button:has-text('SeedDream')",
        "button:has-text('seedream')",
        "[data-testid*='model']",
        "button:has-text('Model')",
        ".model-selector",
        # fallback: any button that has 'lite' in text already selected
        "button:has-text('Lite')",
    ]

    # Try to open model dropdown
    opened = False
    for sel in model_trigger_selectors:
        el = page.locator(sel).first
        if await el.count() > 0:
            try:
                await el.click(timeout=4000)
                opened = True
                break
            except Exception:
                pass

    await asyncio.sleep(1)

    # Now pick the lite option from the dropdown (if it appeared)
    lite_selectors = [
        "li:has-text('5.0 Lite')",
        "li:has-text('Lite')",
        "[role='option']:has-text('Lite')",
        "div:has-text('SeedDream 5.0 Lite')",
        "button:has-text('5.0 Lite')",
    ]
    for sel in lite_selectors:
        el = page.locator(sel).first
        if await el.count() > 0:
            try:
                await el.click(timeout=4000)
                print("  [cfg] Model selected.")
                return True
            except Exception:
                pass

    # If the model is already pre-selected (URL is /seedream_v5_lite), skip
    if "seedream_v5_lite" in page.url or "seedream" in page.url.lower():
        print("  [cfg] Model already set by URL.")
        return True

    print("  [!]  Could not select model – continuing anyway.")
    return False


async def set_resolution_3k(page) -> bool:
    """Click the 3K resolution option."""
    print("  [cfg] Setting resolution: 3K")

    selectors = [
        "button:has-text('3K')",
        "[data-testid*='resolution']:has-text('3K')",
        "label:has-text('3K')",
        "span:has-text('3K')",
        "div[role='button']:has-text('3K')",
        "*:has-text('3072')",   # 3K = 3072px
    ]
    for sel in selectors:
        el = page.locator(sel).first
        if await el.count() > 0:
            try:
                await el.click(timeout=4000)
                print("  [cfg] 3K resolution selected.")
                return True
            except Exception:
                pass

    print("  [!]  Could not set 3K resolution – continuing anyway.")
    return False


async def set_aspect_ratio_1_1(page) -> bool:
    """Click the 1:1 aspect ratio option."""
    print("  [cfg] Setting aspect ratio: 1:1")

    selectors = [
        "button:has-text('1:1')",
        "[data-testid*='aspect']:has-text('1:1')",
        "label:has-text('1:1')",
        "span:has-text('1:1')",
        "div[role='button']:has-text('1:1')",
        "[aria-label='1:1']",
    ]
    for sel in selectors:
        el = page.locator(sel).first
        if await el.count() > 0:
            try:
                await el.click(timeout=4000)
                print("  [cfg] 1:1 ratio selected.")
                return True
            except Exception:
                pass

    print("  [!]  Could not set 1:1 ratio – continuing anyway.")
    return False


async def enable_unlimited_mode(page) -> bool:
    """
    Find the Unlimited toggle and make sure it is ON (green).
    Higgsfield's Unlimited toggle is a switch/checkbox next to the word 'Unlimited'.
    """
    print("  [cfg] Enabling Unlimited mode")

    # Possible toggle patterns
    toggle_selectors = [
        # A toggle/switch that is near the text 'Unlimited'
        "label:has-text('Unlimited') input[type='checkbox']",
        "label:has-text('Unlimited') [role='switch']",
        "[role='switch'][aria-label*='Unlimited' i]",
        "button[role='switch']:near(:text('Unlimited'))",
        # Generic switch next to 'Unlimited' text
        ":text('Unlimited') ~ input[type='checkbox']",
        ":text('Unlimited') + [role='switch']",
        # Higgsfield sometimes uses a div as the toggle
        "div.toggle:near(:text('Unlimited'))",
        "span.toggle:near(:text('Unlimited'))",
    ]

    for sel in toggle_selectors:
        el = page.locator(sel).first
        try:
            if await el.count() > 0:
                # Check if already enabled
                is_checked = await el.is_checked() if await el.get_attribute("type") == "checkbox" else None
                aria = await el.get_attribute("aria-checked")

                already_on = (is_checked is True) or (aria == "true")
                if already_on:
                    print("  [cfg] Unlimited already ON.")
                    return True

                await el.click(timeout=4000)
                await asyncio.sleep(0.5)
                print("  [cfg] Unlimited toggled ON.")
                return True
        except Exception:
            pass

    # Fallback: find any element containing 'Unlimited' and click it
    try:
        el = page.locator("button:has-text('Unlimited'), [role='switch']:near(:text('Unlimited'))").first
        if await el.count() > 0:
            await el.click(timeout=4000)
            print("  [cfg] Unlimited clicked (fallback).")
            return True
    except Exception:
        pass

    print("  [!]  Could not find Unlimited toggle – continuing anyway.")
    return False


async def configure_page_settings(page):
    """Run all one-time UI configurations after page load."""
    await select_model_seedream_lite(page)
    await asyncio.sleep(0.5)
    await set_resolution_3k(page)
    await asyncio.sleep(0.5)
    await set_aspect_ratio_1_1(page)
    await asyncio.sleep(0.5)
    await enable_unlimited_mode(page)
    await asyncio.sleep(1)
    print("  [cfg] Settings configured.\n")


# ─────────────────────────────────────────────
# PROMPT INPUT
# ─────────────────────────────────────────────

async def find_prompt_input(page):
    selectors = [
        "textarea[placeholder*='prompt' i]",
        "textarea[placeholder*='describe' i]",
        "textarea[placeholder*='enter' i]",
        "textarea[placeholder*='type' i]",
        "div[contenteditable='true']",
        "textarea",
    ]
    for sel in selectors:
        el = page.locator(sel).first
        if await el.count() > 0:
            return el
    return None


# ─────────────────────────────────────────────
# GENERATE BUTTON  (big green Unlimited button)
# ─────────────────────────────────────────────

async def find_unlimited_generate_button(page):
    """
    Find the green 'Unlimited' generate button.
    Higgsfield shows a large green button labelled 'Unlimited' (or 'Generate').
    """
    # Priority: the green Unlimited button specifically
    priority = [
        "button:has-text('Unlimited')",
        "button.unlimited",
        "button[class*='unlimited' i]",
        "button[class*='green' i]:has-text('Generate')",
        "[data-testid*='unlimited']",
    ]
    for sel in priority:
        el = page.locator(sel).first
        if await el.count() > 0:
            return el

    # Fallback: any generate button
    fallback = [
        "button:has-text('Generate')",
        "button:has-text('Create')",
        "button[type='submit']",
    ]
    for sel in fallback:
        el = page.locator(sel).first
        if await el.count() > 0:
            return el

    return None


# ─────────────────────────────────────────────
# WAIT FOR GENERATED IMAGE  (network interception)
# ─────────────────────────────────────────────

# Keywords that appear in Higgsfield CDN/storage image URLs
_CDN_HINTS = [
    "higgsfield", "cdn", "storage", "s3", "amazonaws",
    "cloudfront", "output", "result", "generated", "render",
    "prod", "media",
]
# Minimum file size (bytes) to be a real generated image, not a UI icon
_MIN_IMAGE_BYTES = 30_000


async def wait_for_image(page, timeout: int = GENERATION_TIMEOUT):
    """
    Intercept HTTP responses to capture the URL of the generated image.

    Strategy 1 (primary): listen for a large image response from the CDN
                           that arrives AFTER we click Generate.
    Strategy 2 (fallback): scan all <img> elements for any new src that
                           wasn't present before clicking Generate.

    Returns image URL (str) or None on timeout.
    """
    captured_url: list[str] = []   # list so inner closure can mutate it

    # ── Snapshot existing <img> srcs before generation starts ──
    existing_srcs: set[str] = set()
    for el in await page.locator("img").all():
        src = await el.get_attribute("src") or ""
        if src.startswith("http"):
            existing_srcs.add(src)

    # ── Strategy 1: network response listener ──
    async def on_response(response):
        if captured_url:
            return  # already got one
        url = response.url
        content_type = response.headers.get("content-type", "")
        is_image_ct = any(t in content_type for t in ("image/png", "image/jpeg",
                                                        "image/webp", "image/gif"))
        is_image_url = url.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        has_cdn_hint = any(h in url.lower() for h in _CDN_HINTS)

        if (is_image_ct or is_image_url) and has_cdn_hint:
            # Filter out tiny UI assets
            try:
                body = await response.body()
                if len(body) >= _MIN_IMAGE_BYTES:
                    captured_url.append(url)
            except Exception:
                # body unavailable – accept the URL anyway if CT looks right
                if is_image_ct and has_cdn_hint:
                    captured_url.append(url)

    page.on("response", on_response)

    start = time.time()
    print("    Waiting for image", end="", flush=True)

    while time.time() - start < timeout:
        await asyncio.sleep(3)
        print(".", end="", flush=True)

        # Check Strategy 1 result
        if captured_url:
            elapsed = int(time.time() - start)
            print(f" done via network ({elapsed}s)")
            page.remove_listener("response", on_response)
            return captured_url[0]

        # ── Strategy 2: DOM fallback ──
        for el in await page.locator("img").all():
            src = await el.get_attribute("src") or ""
            if (src.startswith("http")
                    and src not in existing_srcs
                    and len(src) > 40
                    and any(h in src.lower() for h in _CDN_HINTS)):
                elapsed = int(time.time() - start)
                print(f" done via DOM ({elapsed}s)")
                page.remove_listener("response", on_response)
                return src

    page.remove_listener("response", on_response)
    print(" TIMEOUT")
    return None


# ─────────────────────────────────────────────
# DOWNLOAD
# ─────────────────────────────────────────────

async def download_image(page, img_url: str, dest_path: Path) -> bool:
    """
    Download the generated image.
    Tries three methods in order:
      1. Playwright request.get (fastest, uses session cookies)
      2. In-browser fetch via JS (works for blob: URLs and CDN with CORS)
      3. Standard urllib as last resort
    """
    try:
        # Method 1: Playwright fetch (includes session cookies)
        resp = await page.request.get(img_url, timeout=60000)
        body = await resp.body()
        if len(body) > 1000:
            dest_path.write_bytes(body)
            return True
    except Exception:
        pass

    try:
        # Method 2: in-browser JS fetch (handles blob: and auth cookies)
        data = await page.evaluate("""
            async (url) => {
                const r = await fetch(url);
                const b = await r.blob();
                return new Promise(res => {
                    const rd = new FileReader();
                    rd.onloadend = () => res(rd.result);
                    rd.readAsDataURL(b);
                });
            }
        """, img_url)
        if data and "," in data:
            _, b64 = data.split(",", 1)
            raw = base64.b64decode(b64)
            if len(raw) > 1000:
                dest_path.write_bytes(raw)
                return True
    except Exception:
        pass

    try:
        # Method 3: urllib fallback
        import urllib.request
        urllib.request.urlretrieve(img_url, dest_path)
        if dest_path.stat().st_size > 1000:
            return True
    except Exception as e:
        print(f"    [!] Download error: {e}")

    return False


# ─────────────────────────────────────────────
# SINGLE NFT GENERATION
# ─────────────────────────────────────────────

async def generate_single_nft(page, nft: dict, output_path: Path,
                               settings_configured: bool) -> tuple[bool, bool]:
    """
    Generate one NFT image.
    Returns (success: bool, settings_configured: bool).
    """
    nft_id = nft["id"]
    prompt = nft["prompt"]

    print(f"\n[{nft_id:04d}/1000] {nft['name']} ({nft['rarity']})")
    print(f"  {nft['traits']['headwear']} | {nft['traits']['eyes']} | {nft['traits']['outfit']}")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Reload page on retry or every 50 NFTs to keep session fresh
            if attempt > 1 or nft_id % 50 == 1:
                print(f"  [→] Loading page...")
                await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=40000)
                await asyncio.sleep(3)
                settings_configured = False   # re-configure after reload

            # Configure settings on first run or after reload
            if not settings_configured:
                await configure_page_settings(page)
                settings_configured = True

            # ── Fill prompt ──
            inp = await find_prompt_input(page)
            if inp is None:
                print(f"  [!] Prompt input not found (attempt {attempt})")
                await asyncio.sleep(3)
                settings_configured = False
                continue

            await inp.click(timeout=5000)
            await asyncio.sleep(0.1)

            # Detect element type to use the right clear method
            tag = (await inp.evaluate("el => el.tagName")).lower()
            is_contenteditable = (tag == "div" or tag == "span"
                                  or await inp.get_attribute("contenteditable") == "true")

            if is_contenteditable:
                # For contenteditable divs: clear via JS directly
                await inp.evaluate("el => { el.innerText = ''; el.textContent = ''; }")
                await asyncio.sleep(0.1)
                await inp.press("Control+a")
                await inp.press("Delete")
                await asyncio.sleep(0.1)
                # Verify via inner_text
                current = (await inp.inner_text()).strip()
            else:
                # For <textarea> / <input>: use fill("") which goes through React
                await inp.press("Control+a")
                await inp.press("Delete")
                await asyncio.sleep(0.1)
                await inp.fill("")
                await asyncio.sleep(0.1)
                current = (await inp.input_value()).strip()

            # Final fallback if anything remains
            if current:
                await inp.press("Control+a")
                await inp.press("Backspace")
                await asyncio.sleep(0.1)

            await inp.type(prompt, delay=8)
            await asyncio.sleep(0.5)

            # ── Click the green Unlimited generate button ──
            btn = await find_unlimited_generate_button(page)
            if btn is None:
                print(f"  [!] Generate button not found (attempt {attempt})")
                await asyncio.sleep(3)
                settings_configured = False
                continue

            print(f"  [→] Clicking generate...")
            await btn.click(timeout=6000)

            # ── Wait for image ──
            img_url = await wait_for_image(page, GENERATION_TIMEOUT)
            if img_url is None:
                print(f"  [!] No image produced (attempt {attempt})")
                settings_configured = False
                continue

            # ── Download ──
            ok = await download_image(page, img_url, output_path)
            if ok:
                print(f"  [+] Saved → {output_path.name}")
                return True, settings_configured

        except PWTimeout:
            print(f"  [!] Playwright timeout (attempt {attempt})")
            settings_configured = False
        except Exception as e:
            print(f"  [!] Error: {e} (attempt {attempt})")
            settings_configured = False

        await asyncio.sleep(5)

    print(f"  [x] FAILED after {MAX_RETRIES} attempts – skipping.")
    return False, settings_configured


# ─────────────────────────────────────────────
# MAIN RUN
# ─────────────────────────────────────────────

async def run(args):
    global DELAY_BETWEEN
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)

    collection = load_or_generate_collection(1000)
    completed  = load_progress() if args.resume else set()

    nfts = [n for n in collection
            if args.start <= n["id"] <= args.end
            and n["id"] not in completed]

    if not nfts:
        print("[+] Nothing to generate (all done or range empty).")
        return

    print(f"[+] Generating {len(nfts)} NFTs  (#{args.start}–#{args.end},"
          f" {len(completed)} already done)")
    print(f"[+] Delay between images: {DELAY_BETWEEN}s")
    print(f"[+] Output → {OUTPUT_DIR.resolve()}\n")

    async with async_playwright() as pw:
        browser, context, page = await setup_browser(
            pw,
            headless=args.headless,
            chrome_path=args.chrome_path or None,
        )

        try:
            await login_if_needed(page, force_login=args.login)

            ok_count    = 0
            fail_count  = 0
            settings_ok = False   # will be set True after first configure

            for nft in nfts:
                img_path  = IMAGES_DIR / f"{nft['id']:04d}.png"
                meta_path = META_DIR   / f"{nft['id']:04d}.json"

                success, settings_ok = await generate_single_nft(
                    page, nft, img_path, settings_ok
                )

                if success:
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

                # ── 15-second cooldown between generations ──
                if nft["id"] != nfts[-1]["id"]:
                    print(f"  [⏳] Waiting {int(DELAY_BETWEEN)}s before next image...")
                    await asyncio.sleep(DELAY_BETWEEN)

        finally:
            await browser.close()

    print(f"\n{'='*52}")
    print(f"  Done!   Generated: {ok_count}   Failed: {fail_count}")
    print(f"  Images   → {IMAGES_DIR.resolve()}")
    print(f"  Metadata → {META_DIR.resolve()}")
    print(f"{'='*52}")


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

def main():
    global DELAY_BETWEEN
    parser = argparse.ArgumentParser(
        description="Degen Boys Club – NFT Collection Generator (Higgsfield.ai)"
    )
    parser.add_argument("--start",       type=int,   default=1,
                        help="First NFT ID to generate (default: 1)")
    parser.add_argument("--end",         type=int,   default=1000,
                        help="Last NFT ID to generate (default: 1000)")
    parser.add_argument("--resume",      action="store_true",
                        help="Skip NFTs that are already downloaded")
    parser.add_argument("--login",       action="store_true",
                        help="Always pause at startup so you can log in manually")
    parser.add_argument("--headless",    action="store_true",
                        help="Run browser without visible window")
    parser.add_argument("--chrome-path", type=str,   default=None,
                        help="Path to Chrome/Chromium executable")
    parser.add_argument("--delay",       type=float, default=DELAY_BETWEEN,
                        help=f"Seconds between generations (default: {DELAY_BETWEEN})")
    args = parser.parse_args()

    DELAY_BETWEEN = args.delay
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
