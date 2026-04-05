"""
Post-generation metadata utilities for the Degen Boys Club collection.

Usage:
    python metadata.py summary          # rarity breakdown + stats
    python metadata.py collection-json  # export ERC-721 compatible collection.json
    python metadata.py fix-missing      # detect NFTs without images
"""

import json
import sys
from collections import Counter
from pathlib import Path

OUTPUT_DIR  = Path("output")
IMAGES_DIR  = OUTPUT_DIR / "images"
META_DIR    = OUTPUT_DIR / "metadata"
COLL_FILE   = OUTPUT_DIR / "collection.json"


def load_metadata() -> list[dict]:
    if not META_DIR.exists():
        return []
    results = []
    for f in sorted(META_DIR.glob("*.json")):
        with open(f) as fh:
            results.append(json.load(fh))
    return results


def summary():
    metas = load_metadata()
    if not metas:
        print("No metadata found. Run bot.py first.")
        return

    rarity_count = Counter(m["rarity"] for m in metas)
    trait_counts = {
        "Headwear":       Counter(),
        "Eyes":           Counter(),
        "Mouth":          Counter(),
        "Outfit":         Counter(),
        "Accessory":      Counter(),
        "Rare Feature":   Counter(),
        "Degen Phase":    Counter(),
        "Meme Reference": Counter(),
        "Background":     Counter(),
    }

    for m in metas:
        for attr in m.get("attributes", []):
            tt = attr["trait_type"]
            if tt in trait_counts:
                trait_counts[tt][attr["value"]] += 1

    total = len(metas)
    print(f"\n{'='*60}")
    print(f"  Degen Boys Club – Collection Summary")
    print(f"  Total NFTs generated: {total}")
    print(f"{'='*60}")

    print("\n  RARITY TIERS:")
    for tier in ["Legendary", "Epic", "Rare", "Uncommon", "Common"]:
        count = rarity_count.get(tier, 0)
        pct   = count / total * 100 if total else 0
        bar   = "#" * int(pct / 2)
        print(f"  {tier:12} {count:5d}  ({pct:5.1f}%)  {bar}")

    print("\n  TOP TRAITS:")
    for trait_type, counts in trait_counts.items():
        if counts:
            top = counts.most_common(3)
            print(f"\n  [{trait_type}]")
            for val, cnt in top:
                print(f"    {cnt:5d}x  {val[:60]}")

    # Missing images
    missing = []
    for m in metas:
        img = OUTPUT_DIR / m["image"]
        if not img.exists():
            missing.append(m["id"])
    if missing:
        print(f"\n  [!] Missing images: {len(missing)} NFTs")
        print(f"      IDs: {missing[:20]}{'...' if len(missing) > 20 else ''}")
        print(f"      Re-run: python bot.py --resume")
    else:
        print(f"\n  [+] All {total} images present.")

    print(f"\n{'='*60}\n")


def export_collection_json():
    """Export ERC-721 compatible metadata array."""
    metas = load_metadata()
    if not metas:
        print("No metadata found.")
        return

    out_path = OUTPUT_DIR / "nft_collection_metadata.json"
    with open(out_path, "w") as f:
        json.dump(metas, f, indent=2)
    print(f"[+] ERC-721 metadata exported → {out_path}")
    print(f"    {len(metas)} NFTs")


def fix_missing():
    """Print IDs of NFTs with missing images so you can re-run them."""
    metas = load_metadata()
    missing = [m["id"] for m in metas if not (OUTPUT_DIR / m["image"]).exists()]
    if not missing:
        print("[+] No missing images found.")
        return

    print(f"[!] {len(missing)} images missing:")
    # Print re-run commands in batches of 50
    batch_size = 50
    for i in range(0, len(missing), batch_size):
        batch = missing[i:i + batch_size]
        print(f"\n  python bot.py --start {batch[0]} --end {batch[-1]} --resume")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"
    if cmd == "summary":
        summary()
    elif cmd == "collection-json":
        export_collection_json()
    elif cmd == "fix-missing":
        fix_missing()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python metadata.py [summary|collection-json|fix-missing]")
