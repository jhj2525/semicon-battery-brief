"""Process Brief v10.

Builds a fixed-size current brief and a separate on-site archive.
Candidate dates are limited to today, yesterday, and the day before yesterday
in Korea time. Existing verified summaries are reused and never summarized
again.
"""

import json
import os
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import update_news_v6 as v6


v3 = v6.v3
base = v6.base
TARGETS = {"semiconductor": 6, "battery": 8}
DOMESTIC_TARGETS = {"semiconductor": 4, "battery": 6}
MAX_ATTEMPTS = {"semiconductor": 14, "battery": 20}
ARCHIVE_LIMIT = 800


def canonical_url(value):
    try:
        parts = urlsplit(value or "")
        blocked = {"fbclid", "gclid", "ref", "source"}
        query = [
            (key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in blocked
        ]
        path = re.sub(r"/+$", "", parts.path) or "/"
        return urlunsplit(
            (parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), "")
        )
    except Exception:
        return value or ""


def published_date(item):
    try:
        return datetime.strptime(item.get("published", "")[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def within_three_calendar_days(item, today):
    date = published_date(item)
    return date is not None and today - timedelta(days=2) <= date <= today


def unique_rows(rows):
    # Run title-similarity deduplication separately per sector so one field can
    # never remove candidates belonging to the other field.
    result = []
    for sector in TARGETS:
        sector_rows = [item for item in rows if item.get("sector") == sector]
        result.extend(base.deduplicate(sector_rows))
    return result


def useful_candidate(item):
    text = f"{item.get('title', '')} {item.get('rss_description', '')}".lower()
    if any(term in text for term in base.EXCLUDE):
        return False
    if any(term in text for term in v3.PURE_RESEARCH_TERMS):
        return False
    if item.get("region") == "global":
        # Accept impactful battery/semiconductor industry reporting even when
        # the headline does not name one of the hard-coded major companies.
        return (
            any(term in text for term in v3.HIGH_IMPACT_TERMS)
            or item.get("score", 0) >= 12
        )
    return True


def priority_key(item):
    date = published_date(item)
    return (
        date.isoformat() if date else "",
        1 if item.get("official_source") else 0,
        item.get("score", 0),
        item.get("published", ""),
    )


def source_diverse(rows, source_cap=2):
    selected, counts = [], {}
    for item in sorted(rows, key=priority_key, reverse=True):
        group = item.get("source", "").split()[0]
        if counts.get(group, 0) >= source_cap:
            continue
        selected.append(item)
        counts[group] = counts.get(group, 0) + 1
    # Keep remaining candidates as reserves after the diverse first pass.
    used = {item["id"] for item in selected}
    selected.extend(
        item for item in sorted(rows, key=priority_key, reverse=True)
        if item["id"] not in used
    )
    return selected


def ordered_sector_candidates(rows, sector):
    domestic = source_diverse(
        [item for item in rows if item.get("region") == "domestic"]
    )
    global_items = source_diverse(
        [item for item in rows if item.get("region") == "global"]
    )

    domestic_target = DOMESTIC_TARGETS[sector]
    preferred = domestic[:domestic_target] + global_items[:2]
    used = {item["id"] for item in preferred}
    reserves = [item for item in domestic + global_items if item["id"] not in used]
    return preferred + reserves


def existing_lookup(items):
    by_id, by_url = {}, {}
    for item in items:
        if item.get("verified_source") is not True:
            continue
        if item.get("id"):
            by_id[item["id"]] = item
        if item.get("link"):
            by_url[canonical_url(item["link"])] = item
    return by_id, by_url


def select_sector(rows, sector, existing_items, api_key):
    target = TARGETS[sector]
    by_id, by_url = existing_lookup(existing_items)
    current, newly_verified = [], []
    attempts = reused = 0

    for candidate in ordered_sector_candidates(rows, sector):
        if len(current) >= target:
            break
        old = by_id.get(candidate.get("id")) or by_url.get(
            canonical_url(candidate.get("link"))
        )
        if old:
            current.append(old)
            reused += 1
            continue
        if attempts >= MAX_ATTEMPTS[sector]:
            break
        attempts += 1
        try:
            verified = base.enrich_one(candidate, api_key)
        except Exception as error:
            print(
                f"{sector} verification failed: {candidate.get('source')} "
                f"({type(error).__name__})"
            )
            continue
        if verified:
            current.append(verified)
            newly_verified.append(verified)
        else:
            print(
                f"{sector} unreadable: {candidate.get('source')}; "
                "trying reserve"
            )

    print(
        f"{sector}: eligible {len(rows)}, reused {reused}, "
        f"verification attempts {attempts}, new {len(newly_verified)}, "
        f"current {len(current)}/{target}"
    )
    return current, newly_verified


def merge_archive(all_existing, newly_verified, current):
    merged = {}
    for item in all_existing + newly_verified:
        if item.get("verified_source") is not True or not item.get("link"):
            continue
        merged[canonical_url(item["link"])] = item
    current_urls = {canonical_url(item.get("link")) for item in current}
    archive = [item for url, item in merged.items() if url not in current_urls]
    return sorted(
        archive,
        key=lambda item: (item.get("published", ""), item.get("collected", "")),
        reverse=True,
    )[:ARCHIVE_LIMIT]


def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required")

    old = v3.read_old()
    legacy = old.get("items", [])
    old_current = old.get("current_items", [])
    old_archive = old.get("archive_items", [])
    all_existing = old_current + old_archive + legacy

    today = datetime.now(base.KST).date()
    fetched = base.collect()
    # Keep already verified articles inside the three-day window as reusable
    # candidates even if a feed is temporarily unavailable on this run.
    reusable_candidates = []
    for item in all_existing:
        if item.get("verified_source") is not True:
            continue
        candidate = dict(item)
        candidate.setdefault("score", 0)
        candidate.setdefault("rss_description", candidate.get("overview", ""))
        reusable_candidates.append(candidate)
    collected = unique_rows(fetched + reusable_candidates)
    eligible = [
        item for item in collected
        if within_three_calendar_days(item, today) and useful_candidate(item)
    ]
    print(
        f"publication window: {today - timedelta(days=2)} to {today}; "
        f"collected {len(collected)}, eligible {len(eligible)}"
    )
    print(
        "eligible by sector: "
        f"semiconductor {sum(x.get('sector') == 'semiconductor' for x in eligible)}, "
        f"battery {sum(x.get('sector') == 'battery' for x in eligible)}"
    )

    current, new_items = [], []
    for sector in ("semiconductor", "battery"):
        sector_rows = [item for item in eligible if item.get("sector") == sector]
        selected, fresh = select_sector(
            sector_rows, sector, all_existing + new_items, api_key
        )
        current.extend(selected)
        new_items.extend(fresh)

    archive = merge_archive(all_existing, new_items, current)
    payload = {
        "updated_at": datetime.now(base.KST).strftime("%Y-%m-%d %H:%M KST"),
        "publication_window": {
            "from": (today - timedelta(days=2)).isoformat(),
            "to": today.isoformat(),
        },
        "today_verified_count": len(new_items),
        "current_items": current,
        "archive_items": archive,
        "market_items": old.get("market_items", []),
    }
    base.OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    base.OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"saved current {len(current)} "
        f"(semiconductor {sum(x['sector'] == 'semiconductor' for x in current)}, "
        f"battery {sum(x['sector'] == 'battery' for x in current)}); "
        f"new {len(new_items)}; archive {len(archive)}"
    )


if __name__ == "__main__":
    main()
