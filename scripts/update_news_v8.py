"""Process Brief v8: new-only collection with verification backfill."""

import json
import os
from datetime import datetime

import update_news_v6 as v6


v3 = v6.v3
base = v6.base


def ordered_candidates(rows, sector):
    pool = [item for item in rows if item["sector"] == sector]
    domestic = [item for item in pool if item["region"] == "domestic"]
    global_items = [
        item for item in pool
        if item["region"] == "global" and v3.is_useful_global(item)
    ]
    domestic_target = 4 if sector == "semiconductor" else 6
    ordered = v6.official_first(domestic, domestic_target)
    ordered += v3.select_diverse(global_items, 2)
    used = {item["id"] for item in ordered}
    ordered += [item for item in domestic if item["id"] not in used]
    used = {item["id"] for item in ordered}
    ordered += [item for item in global_items if item["id"] not in used]
    return ordered


def verify_new(rows, sector, target, max_attempts):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required")
    verified = []
    attempts = 0
    for item in ordered_candidates(rows, sector):
        if len(verified) >= target or attempts >= max_attempts:
            break
        attempts += 1
        try:
            result = base.enrich_one(item, api_key)
            if result:
                verified.append(result)
            else:
                print(f"{sector} unreadable: {item['source']}; trying reserve")
        except Exception as error:
            print(
                f"{sector} failed: {item['source']} "
                f"({type(error).__name__}); trying reserve"
            )
    print(
        f"{sector}: candidates {len(rows)}, attempts {attempts}, "
        f"new verified {len(verified)}/{target}"
    )
    return verified


def main():
    old_data = v3.read_old()
    old_items = old_data.get("items", [])
    old_ids = {item.get("id") for item in old_items}
    old_links = {item.get("link") for item in old_items}

    collected = base.deduplicate(base.collect())
    fresh = [
        item for item in collected
        if item.get("id") not in old_ids and item.get("link") not in old_links
    ]
    duplicate_count = len(collected) - len(fresh)
    print(
        f"main candidates: total {len(collected)}, "
        f"existing duplicates {duplicate_count}, fresh {len(fresh)}"
    )

    semiconductor_rows = [x for x in fresh if x["sector"] == "semiconductor"]
    battery_rows = [x for x in fresh if x["sector"] == "battery"]
    print(
        f"fresh by sector: semiconductor {len(semiconductor_rows)}, "
        f"battery {len(battery_rows)}"
    )

    new_items = []
    new_items += verify_new(semiconductor_rows, "semiconductor", 6, 12)
    new_items += verify_new(battery_rows, "battery", 8, 16)
    archive = base.merge_archive(new_items)

    payload = {
        "updated_at": datetime.now(base.KST).strftime("%Y-%m-%d %H:%M KST"),
        "today_verified_count": len(new_items),
        "today_market_count": 0,
        "items": archive,
        # Preserve any old SEMI data without attempting the broken collector.
        "market_items": old_data.get("market_items", []),
    }
    base.OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    base.OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"saved {len(new_items)} genuinely new articles; "
        f"archive {len(archive)}"
    )


if __name__ == "__main__":
    main()
