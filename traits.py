"""
NFT Trait definitions and prompt generator for "Degen Boys Club" collection.
1000 unique Crypto Degen pixel-art avatars.
"""

import random
import hashlib
import json

# ─────────────────────────────────────────────
# TRAIT POOLS  (weight = relative rarity)
# ─────────────────────────────────────────────

BACKGROUNDS = [
    ("neon green retro grid",        30),
    ("deep purple galaxy",           30),
    ("dark blue crypto exchange UI", 25),
    ("black with gold coin rain",    20),
    ("orange meme-coin explosion",   20),
    ("red rug-pull panic chart",     15),
    ("teal moon surface landscape",  15),
    ("yellow YOLO sunrise",          15),
    ("grey matrix code rain",        12),
    ("white clean minimal",          10),
    ("rainbow holographic gradient",  8),
    ("transparent void",              5),
]

HEADWEAR = [
    ("no headwear",                   30),
    ("backwards cap",                 20),
    ("hoodie up",                     20),
    ("DOGE baseball cap",             15),
    ("PEPE green frog hat",           15),
    ("Shiba Inu ear headband",        15),
    ("diamond crown",                 10),
    ("tinfoil hat",                   10),
    ("laser-eye headband",            10),
    ("astronaut helmet",               8),
    ("golden laurel wreath",           7),
    ("moon landing helmet",            6),
    ("legendary halo glowing",         3),
]

EYES = [
    ("tired bloodshot eyes",          25),
    ("laser red eyes",                20),
    ("FOMO wide open eyes",           20),
    ("sunglasses retro",              20),
    ("DOGE shades",                   15),
    ("VR headset",                    12),
    ("crying laughing emoji eyes",    12),
    ("spiral hypnosis eyes",          10),
    ("monocle old-money",              8),
    ("night-vision goggles",           7),
    ("3D glasses",                     6),
    ("solid gold sunglasses",          4),
    ("diamond-encrusted glasses",      3),
]

MOUTH = [
    ("cigarette in mouth",            20),
    ("energy drink can sipping",      20),
    ("smirking confident",            18),
    ("screaming FOMO panic",          18),
    ("grinning euphoric",             18),
    ("sad frown rug-pull despair",    15),
    ("gritting teeth holding bags",   12),
    ("gold tooth grin",               10),
    ("chewing pizza slice",            8),
    ("blowing smoke ring",             7),
    ("diamond mouthguard",             5),
    ("cigar legendary",                4),
]

OUTFIT = [
    ("hoodie with meme coin logo",    25),
    ("sleeveless degen tank top",     20),
    ("suit jacket no tie crypto bro", 15),
    ("YOLO printed t-shirt",          15),
    ("HODL graphic tee",              15),
    ("diamond hands jersey",          12),
    ("rug-pull survivor bandages",    10),
    ("moon mission spacesuit",         8),
    ("whale investor trench coat",     6),
    ("legendary golden armor",         3),
]

ACCESSORY = [
    ("no accessory",                  25),
    ("phone showing green chart",     20),
    ("laptop with trading terminal",  15),
    ("Ledger hardware wallet",        12),
    ("meme coin plushie",             12),
    ("coffee cup trading fuel",       12),
    ("dumbbell diamond hands",        10),
    ("DOGE plushie",                  10),
    ("PEPE plushie",                   8),
    ("bag of coins overflowing",       7),
    ("golden phone showing moon",      5),
    ("1/1 legendary NFT certificate",  3),
]

RARE_FEATURE = [
    ("none",                          45),
    ("Diamond Hands aura",            15),
    ("Bag Holder curse mark",         12),
    ("Rug Pull Survivor scar",        10),
    ("YOLO halo",                      8),
    ("FOMO lightning bolt",            5),
    ("Whale Wallet badge",             3),
    ("Moonshot Prophet crown",         2),
    ("Elon's Advisor briefcase",       1),
    ("Legendary Degen Boys Club seal", 1),
    ("$PNDC iconic brand logo",        1),
    ("Ultra Rare 1/1 Rainbow Aura",    1),
]

DEGEN_PHASE = [
    ("Noob Degen – nervous expression, wide eyes, fresh to the market",  25),
    ("FOMO Degen – panicked buying face, chart going up behind",         20),
    ("Diamond Hands Veteran – calm stoic face, rocket in background",    20),
    ("Euphoric Moon Boy – grinning ear to ear, hands raised",            15),
    ("Rug Pull Survivor – bandaged avatar, broken chart background",     12),
    ("Whale Accumulator – smug smile, massive bag of coins",              5),
    ("Legendary Moonshot Prophet – glowing eyes, cosmic background",      3),
]

MEME_REFERENCE = [
    ("none",              40),
    ("DOGE coin",         15),
    ("PEPE coin",         15),
    ("Shiba Inu",         12),
    ("BONK",               8),
    ("FLOKI",              5),
    ("WIF",                4),
    ("BRETT",              3),
    ("$PNDC Degen Boys",   2),
]

NFT_STORY_TEMPLATES = [
    "Bought the dip at ${BUY}, HODL till ${SELL}.",
    "Survived the ${COIN} pump & dump. Still standing.",
    "Diamond hands through the ${COIN} crash. No regrets.",
    "YOLO'd my savings into ${COIN} at midnight. Worth it.",
    "Lost everything on ${COIN}, found myself on the other side.",
    "Caught the ${COIN} bottom. Hands of pure diamond.",
    "Watched the portfolio go -90% and bought more ${COIN}.",
    "Early ${COIN} adopter. The degen life chose me.",
    "Rug pulled three times. Still in the game. #{PHASE}",
    "From zero to hero on ${COIN}. The degen way.",
    "Community OG, ${COIN} believer since day one.",
    "Survived bear market {YEAR}. Battle-hardened degen.",
]

MEME_COINS_FOR_STORIES = [
    "DOGE", "SHIB", "PEPE", "BONK", "FLOKI", "WIF",
    "BRETT", "$PNDC", "ELON", "TURBO", "MEME", "LADYS",
]

PHASES_FOR_STORIES = [
    "DiamondHandsMode", "RugPullSurvivor", "MoonshotBelief",
    "YOLOInvestor", "DegenForever", "HodlKing",
]


def weighted_choice(pool):
    """Pick one item from pool of (value, weight) tuples."""
    items, weights = zip(*pool)
    return random.choices(items, weights=weights, k=1)[0]


def generate_story():
    template = random.choice(NFT_STORY_TEMPLATES)
    coin = random.choice(MEME_COINS_FOR_STORIES)
    phase = random.choice(PHASES_FOR_STORIES)
    buy = f"0.00{random.randint(1, 9)}{random.randint(0, 9)}"
    sell = f"0.{random.randint(1, 9)}{random.randint(0, 9)}"
    year = random.choice(["2022", "2023", "2024", "2025"])
    return (template
            .replace("${COIN}", coin)
            .replace("${BUY}", buy)
            .replace("${SELL}", sell)
            .replace("#{PHASE}", phase)
            .replace("{YEAR}", year))


def generate_traits():
    """Return a dict of randomly-weighted traits for one NFT."""
    return {
        "background": weighted_choice(BACKGROUNDS),
        "headwear":   weighted_choice(HEADWEAR),
        "eyes":       weighted_choice(EYES),
        "mouth":      weighted_choice(MOUTH),
        "outfit":     weighted_choice(OUTFIT),
        "accessory":  weighted_choice(ACCESSORY),
        "rare_feature": weighted_choice(RARE_FEATURE),
        "degen_phase":  weighted_choice(DEGEN_PHASE),
        "meme_reference": weighted_choice(MEME_REFERENCE),
        "story":      generate_story(),
    }


def build_prompt(traits: dict) -> str:
    """Build the full image-generation prompt from traits."""
    rare_line = (
        f"rare traits: {traits['rare_feature']},"
        if traits["rare_feature"] != "none"
        else ""
    )
    meme_line = (
        f"meme reference: {traits['meme_reference']} iconography on outfit,"
        if traits["meme_reference"] != "none"
        else ""
    )

    prompt = f"""Ultra clean pixel-art NFT avatar, inspired by CryptoPunks, Degen Boys Club style, \
single character centered, minimal background, retro 8-bit aesthetic, sharp edges, high contrast colors, \
character is a crypto degen trader, expressive face, \
degen phase: {traits['degen_phase']}, \
traits: headwear [{traits['headwear']}], eyes [{traits['eyes']}], \
mouth [{traits['mouth']}], accessory [{traits['accessory']}], outfit [{traits['outfit']}], \
{meme_line} \
theme: crypto meme culture, trading lifestyle, YOLO investing, \
{rare_line} \
background: {traits['background']}, \
lighting: flat pixel lighting no shadows, \
composition: front-facing portrait symmetrical, \
style: iconic collectible clean NFT design"""

    # Collapse whitespace
    return " ".join(prompt.split())


def generate_collection(size: int = 1000, seed: int = 42) -> list[dict]:
    """
    Generate `size` unique NFT metadata dicts.
    Uniqueness is enforced by trait-combo hashing; collisions are rerolled.
    """
    random.seed(seed)
    collection = []
    seen_hashes = set()

    i = 1
    attempts = 0
    while len(collection) < size:
        attempts += 1
        if attempts > size * 10:
            raise RuntimeError("Too many collisions – expand trait pools.")

        traits = generate_traits()
        # Hash on all traits except the story (story is always unique enough)
        combo_key = json.dumps(
            {k: v for k, v in traits.items() if k != "story"},
            sort_keys=True
        )
        combo_hash = hashlib.md5(combo_key.encode()).hexdigest()

        if combo_hash in seen_hashes:
            continue

        seen_hashes.add(combo_hash)
        rarity = compute_rarity(traits)
        collection.append({
            "id":     i,
            "name":   f"Degen Avatar #{i:04d}",
            "traits": traits,
            "prompt": build_prompt(traits),
            "rarity": rarity,
            "combo_hash": combo_hash,
        })
        i += 1

    return collection


def compute_rarity(traits: dict) -> str:
    """Assign a rarity tier based on rare_feature and degen_phase."""
    rare = traits["rare_feature"]
    phase = traits["degen_phase"]

    if "1/1" in rare or "Ultra Rare" in rare or "Legendary" in rare.lower():
        return "Legendary"
    if "Whale" in rare or "Moonshot" in rare or "Elon" in rare or "$PNDC" in rare:
        return "Epic"
    if rare != "none" or "Whale" in phase or "Legendary" in phase:
        return "Rare"
    if traits["meme_reference"] != "none":
        return "Uncommon"
    return "Common"


if __name__ == "__main__":
    collection = generate_collection(10)
    for nft in collection:
        print(f"[{nft['rarity']:10}] {nft['name']}")
        print(f"  PROMPT: {nft['prompt'][:120]}...")
        print(f"  STORY:  {nft['traits']['story']}")
        print()
