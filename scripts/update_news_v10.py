"""Process Brief v10.

Builds a fixed-size current brief and a separate on-site archive.
Candidate dates are limited to today, yesterday, and the day before yesterday
in Korea time. Existing verified summaries are reused and never summarized
again.
"""

import json
import os
import re
import hashlib
import html
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit
from urllib.request import Request
from xml.etree import ElementTree

import update_news_v6 as v6


v3 = v6.v3
base = v6.base
TARGETS = {"semiconductor": 6, "battery": 8}
DOMESTIC_TARGETS = {"semiconductor": 5, "battery": 7}
MAX_ATTEMPTS = {"semiconductor": 14, "battery": 20}
ARCHIVE_LIMIT = 800
SEMI_CURRENT_LIMIT = 10
SEMI_ARCHIVE_LIMIT = 300

# Verified publisher-operated RSS feeds.  These are candidate sources only;
# every selected article still has to pass original-URL verification.
EXTRA_FEEDS = [
    {
        "url": "https://www.kipost.net/rss/allArticle.xml",
        "source": "KIPOST",
        "region": "domestic",
        "fixed_sector": None,
    },
]

for extra_feed in EXTRA_FEEDS:
    if not any(feed.get("url") == extra_feed["url"] for feed in base.FEEDS):
        base.FEEDS.append(extra_feed)

SOURCE_TYPES = {
    "KIPOST": "산업 전문언론",
    "디일렉": "산업 전문언론",
    "디일렉 소재·장비": "산업 전문언론",
    "전자신문": "산업 전문언론",
    "전자신문 경제": "산업 전문언론",
    "전자신문 소재": "산업 전문언론",
    "전자신문 장비": "산업 전문언론",
    "전자신문 모빌리티": "산업 전문언론",
    "Semiconductor Engineering": "해외 산업 보도",
    "Battery Power Online": "해외 산업 보도",
    "electrive": "해외 산업 보도",
}

SOURCE_RANK = {
    "정부·협회": 5,
    "기업 공식 발표": 4,
    "시장조사기관": 4,
    "통신사 보도": 3,
    "산업 전문언론": 2,
    "해외 산업 보도": 1,
}

# List pages that publish authoritative material but do not expose a dependable
# public RSS feed.  Failures are isolated and never stop the daily brief.
TRUSTED_PAGES = [
    {
        "url": "https://www.motir.go.kr/kor/article/ATCL3f49a5a8c",
        "source": "산업통상부",
        "source_type": "정부·협회",
        "include_path": ("/kor/article/ATCL3f49a5a8c/",),
        "fixed_sector": None,
    },
    {
        "url": "https://www.sneresearch.com/kr/insight/release/",
        "source": "SNE리서치",
        "source_type": "시장조사기관",
        "include_path": ("/kr/insight/release_view/",),
        "fixed_sector": "battery",
    },
    {
        "url": "https://www.ksia.or.kr/infomationKSIA.php?data_tab=1",
        "source": "한국반도체산업협회",
        "source_type": "정부·협회",
        "fixed_sector": "semiconductor",
    },
    {
        "url": "https://www.k-bia.or.kr/openRecent.do",
        "source": "한국배터리산업협회",
        "source_type": "정부·협회",
        "fixed_sector": "battery",
    },
    {
        "url": "https://zdnet.co.kr/news/?lstcode=0050&page=1",
        "source": "ZDNet Korea",
        "source_type": "산업 전문언론",
        "include_path": ("/view/",),
        "fixed_sector": "semiconductor",
        "require_title_sector": True,
    },
    {
        "url": "https://zdnet.co.kr/newskey/?lstcode=%EB%B0%B0%ED%84%B0%EB%A6%AC",
        "source": "ZDNet Korea",
        "source_type": "산업 전문언론",
        "include_path": ("/view/",),
        "fixed_sector": "battery",
        "require_title_sector": True,
    },
]

SEMI_RSS = "https://rss.blog.naver.com/semi_blog.xml"
SEMI_CATEGORIES = {"시장동향 뉴스", "SEMI 회원사 동향 뉴스"}

# A dead publisher endpoint must not hold the whole workflow for 40-45 seconds.
# Gemini summarisation keeps its original 120-second allowance.
_base_urlopen = base.urlopen
_blog_urlopen = v6.v5.v4.urlopen


def _capped_base_urlopen(request, timeout=20, *args, **kwargs):
    url = getattr(request, "full_url", str(request))
    allowed = timeout if "generativelanguage.googleapis.com" in url else min(timeout, 20)
    return _base_urlopen(request, timeout=allowed, *args, **kwargs)


def _capped_blog_urlopen(request, timeout=20, *args, **kwargs):
    return _blog_urlopen(request, timeout=min(timeout, 20), *args, **kwargs)


base.urlopen = _capped_base_urlopen
v6.v5.v4.urlopen = _capped_blog_urlopen


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
    if item.get("sector") == "battery":
        consumer_terms = ("스마트폰", "갤럭시", "아이폰", "노트북", "보조배터리", "웨어러블")
        industrial_terms = (
            "이차전지", "전기차", "ess", "에너지저장", "양극", "음극", "전극",
            "전해질", "분리막", "lfp", "전고체", "셀", "공장", "양산", "수주",
        )
        if any(term in text for term in consumer_terms) and not any(
            term in text for term in industrial_terms
        ):
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
    source_type = item.get("source_type") or SOURCE_TYPES.get(item.get("source"), "")
    return (
        date.isoformat() if date else "",
        SOURCE_RANK.get(source_type, 0),
        item.get("score", 0),
        item.get("published", ""),
    )


def tag_source(item):
    item = dict(item)
    if item.get("source_type") == "협회 동향":
        item["source_type"] = "정부·협회"
    item.setdefault(
        "source_type",
        "기업 공식 발표" if item.get("official_source") else
        SOURCE_TYPES.get(item.get("source"), "산업 전문언론"),
    )
    if item["source_type"] in {"정부·협회", "기업 공식 발표", "시장조사기관"}:
        item["official_source"] = True
    item["source_rank"] = SOURCE_RANK.get(item["source_type"], 0)
    return item


def trusted_page_candidates(config):
    """Read authoritative list pages without inventing missing dates."""
    request = Request(
        config["url"],
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
            )
        },
    )
    with base.urlopen(request, timeout=20) as response:
        raw = response.read().decode("utf-8", errors="ignore")
    anchor_pattern = re.compile(
        r'<a\b[^>]*href=(["\'])(.*?)\1[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    results = []
    for match in anchor_pattern.finditer(raw):
        href = html.unescape(match.group(2))
        motir_id = re.search(r"article\.view\(['\"]?(\d+)", href)
        if motir_id and config["source"] == "산업통상부":
            link = f"{config['url'].rstrip('/')}/{motir_id.group(1)}/view"
        else:
            link = urljoin(config["url"], href).split("#", 1)[0]
        inner = match.group(3)
        heading = re.search(r"<h[1-4]\b[^>]*>(.*?)</h[1-4]>", inner, re.IGNORECASE | re.DOTALL)
        title = base.clean(heading.group(1) if heading else inner)
        if len(title) < 8 or not link.startswith(("http://", "https://")):
            continue
        if config.get("include_path") and not any(
            token in link for token in config["include_path"]
        ):
            continue
        before = base.clean(raw[max(0, match.start() - 600):match.start()])
        after = base.clean(raw[match.end():match.end() + 700])
        context = f"{before} {base.clean(inner)} {after}"
        zdnet_date = re.search(r"[?&]no=(20\d{6})", link)
        published = v6.extract_date(title) or v6.extract_date(after)
        if published is None and zdnet_date and config["source"] == "ZDNet Korea":
            published = datetime.strptime(zdnet_date.group(1), "%Y%m%d").replace(tzinfo=base.KST)
        if published is None:
            published = v6.extract_date(before)
        if published is None:
            continue
        if config["source"] == "SNE리서치":
            title = re.sub(r"\s+20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}\s*$", "", title)
        sector = base.detect_sector(
            f"{title} {context[:600]}", config.get("fixed_sector")
        )
        if config.get("require_title_sector"):
            title_sector = base.detect_sector(title, None)
            if title_sector != config.get("fixed_sector"):
                continue
        if not sector or sector not in TARGETS:
            continue
        combined = f"{title} {context[:600]}"
        if any(term in combined.lower() for term in base.EXCLUDE):
            continue
        is_authority = config["source_type"] in {
            "정부·협회", "기업 공식 발표", "시장조사기관"
        }
        item = {
            "id": hashlib.sha1(link.encode()).hexdigest()[:12],
            "sector": sector,
            "region": "domestic",
            "category": base.detect_category(combined),
            "title": title,
            "source": config["source"],
            "source_type": config["source_type"],
            "official_source": is_authority,
            "published": published.strftime("%Y-%m-%d %H:%M"),
            "collected": datetime.now(base.KST).strftime("%Y-%m-%d"),
            "link": link,
            "rss_description": context[:1000],
        }
        item["score"] = base.relevance_score(item) + 10
        results.append(tag_source(item))
    return base.deduplicate(results)


def collect_all_candidates():
    rows = [tag_source(item) for item in base.collect()]
    authority_count = 0
    for config in TRUSTED_PAGES:
        try:
            found = trusted_page_candidates(config)
            rows.extend(found)
            authority_count += len(found)
            print(f"trusted source: {config['source']} {len(found)} candidates")
        except Exception as error:
            print(f"trusted source skipped: {config['source']} ({type(error).__name__})")
    print(f"trusted page candidates: {authority_count}")
    return rows


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
    preferred = domestic[:domestic_target] + global_items[:1]
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


def semi_source(url):
    host = urlparse(url).netloc.lower().removeprefix("www.")
    known = {
        "thelec.kr": "디일렉",
        "etnews.com": "전자신문",
        "yna.co.kr": "연합뉴스",
        "kipost.net": "KIPOST",
        "zdnet.co.kr": "ZDNet Korea",
        "ddaily.co.kr": "디지털데일리",
    }
    return known.get(host, host or "원문 매체")


def semi_article_urls(raw):
    """Extract actual outbound article anchors, excluding page assets/metadata."""
    decoded = v6.v5.v4.decode_embedded(raw)
    urls = []
    for anchor in re.findall(r"<a\b[^>]*>", decoded, flags=re.IGNORECASE | re.DOTALL):
        href = re.search(r'href=["\']([^"\']+)', anchor, flags=re.IGNORECASE)
        if href:
            urls.append(html.unescape(href.group(1)))
    # Naver's smart editor also stores the same target in JSON module data.
    urls.extend(
        html.unescape(value)
        for value in re.findall(r'["\']link["\']\s*:\s*["\'](https?://[^"\']+)', decoded)
    )
    blocked = (
        "naver.com", "pstatic.net", "ogp.me", "w3.org", "schema.org",
        "realityripple.com", "facebook.com", "instagram.com", "youtube.com",
    )
    result = []
    for value in urls:
        value = canonical_url(value.rstrip(".,)]}"))
        host = urlparse(value).netloc.lower().removeprefix("www.")
        if not host or any(host == domain or host.endswith(f".{domain}") for domain in blocked):
            continue
        if value not in result:
            result.append(value)
    result.sort(
        key=lambda value: not any(
            token in urlparse(value).path.lower()
            for token in ("article", "/view", "/news", "articleview")
        )
    )
    return result


def collect_semi_candidates():
    """Collect the latest posts from the two requested SEMI blog boards.

    SEMI posts are intentionally not restricted to the main brief's three-day
    window because these boards publish irregularly.  The tab always represents
    the latest ten curated links instead.
    """
    raw = v6.v5.v4.fetch(SEMI_RSS)
    root = ElementTree.fromstring(raw)
    entries = v6.v5.find_entries(root)
    board_entries = []
    category_counts = {}
    for entry in entries[:100]:
        category = base.clean(v6.v5.any_node_text(entry, "category", "subject"))
        category_counts[category or "(없음)"] = category_counts.get(category or "(없음)", 0) + 1
        if category not in SEMI_CATEGORIES:
            continue
        board_entries.append(entry)

    print(
        f"SEMI RSS entries {len(entries)}; requested-board posts {len(board_entries)}; "
        f"categories {category_counts}"
    )
    results = []
    body_access = link_count = 0
    for entry in board_entries[:SEMI_CURRENT_LIMIT * 2]:
        title = base.clean(v6.v5.any_node_text(entry, "title"))
        description_raw = v6.v5.any_node_text(
            entry, "description", "encoded", "content", "summary"
        )
        post_url = base.clean(v6.v5.any_node_text(entry, "link"))
        category = base.clean(v6.v5.any_node_text(entry, "category", "subject"))
        published = base.parse_date(
            v6.v5.any_node_text(entry, "pubDate", "published", "updated", "date")
        )
        urls = semi_article_urls(description_raw)
        if not urls and post_url:
            try:
                body = v6.v5.v4.fetch(v6.v5.v4.mobile_post_url(post_url))
                body_access += 1
                urls = semi_article_urls(body)
            except Exception as error:
                print(f"SEMI body skipped: {title[:45]} ({type(error).__name__})")
        if not urls:
            print(f"SEMI original link not found: {title[:55]}")
            continue
        article_url = canonical_url(urls[0])
        link_count += 1
        item = {
            "id": hashlib.sha1(article_url.encode()).hexdigest()[:12],
            "sector": "semi_market",
            "region": "domestic",
            "category": v3.market_category(title),
            "title": re.sub(r"^\s*\[공유\]\s*", "", title).strip(),
            "source": semi_source(article_url),
            "source_type": "SEMI 큐레이션",
            "published": published.strftime("%Y-%m-%d %H:%M"),
            "collected": datetime.now(base.KST).strftime("%Y-%m-%d"),
            "link": article_url,
            "rss_description": base.clean(description_raw)[:1000] or title,
            "discovered_via": f"SEMI Korea 블로그 · {category}",
            "summary_status": "link_only",
            "score": v3.industry_score({
                "title": title,
                "rss_description": base.clean(description_raw),
                "region": "domestic",
            }),
        }
        results.append(item)
        if len(results) >= SEMI_CURRENT_LIMIT:
            break
    print(
        f"SEMI stages: board {len(board_entries)}, body accessible {body_access}, "
        f"original links {link_count}, eligible {len(results)}"
    )
    ordered, seen_urls = [], set()
    for item in results:
        url = canonical_url(item.get("link"))
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        ordered.append(item)
    return ordered[:SEMI_CURRENT_LIMIT]


def select_semi(candidates, old_items, api_key):
    by_id, by_url = existing_lookup(old_items)
    current, newly_verified = [], []
    for candidate in candidates:
        old = by_id.get(candidate.get("id")) or by_url.get(canonical_url(candidate.get("link")))
        if old:
            reused = dict(old)
            reused["source_type"] = "SEMI 큐레이션"
            reused["discovered_via"] = candidate.get("discovered_via")
            current.append(reused)
            continue
        try:
            verified = base.enrich_one(candidate, api_key)
        except Exception as error:
            print(f"SEMI summary failed: {candidate.get('source')} ({type(error).__name__})")
            continue
        if verified:
            verified["summary_status"] = "summarized"
            current.append(verified)
            newly_verified.append(verified)
    print(
        f"SEMI selected: candidates {len(candidates)}, new {len(newly_verified)}, "
        f"current {len(current)}/{SEMI_CURRENT_LIMIT}"
    )
    return current, newly_verified


def merge_semi_archive(old_items, newly_verified, current):
    merged = {}
    for item in old_items + newly_verified:
        if item.get("verified_source") is True and item.get("link"):
            merged[canonical_url(item["link"])] = item
    current_urls = {canonical_url(item.get("link")) for item in current}
    return sorted(
        [item for url, item in merged.items() if url not in current_urls],
        key=lambda item: (item.get("published", ""), item.get("collected", "")),
        reverse=True,
    )[:SEMI_ARCHIVE_LIMIT]


def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required")

    old = v3.read_old()
    legacy = old.get("items", [])
    old_current = old.get("current_items", [])
    old_archive = old.get("archive_items", [])
    all_existing = [tag_source(item) for item in old_current + old_archive + legacy]
    old_semi_current = old.get("semi_items", [])
    old_semi_archive = old.get("semi_archive_items", [])
    legacy_market = old.get("market_items", [])
    all_existing_semi = old_semi_current + old_semi_archive + legacy_market

    today = datetime.now(base.KST).date()
    fetched = collect_all_candidates()
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

    try:
        semi_candidates = collect_semi_candidates()
        if semi_candidates:
            semi_current, new_semi = select_semi(
                semi_candidates, all_existing_semi, api_key
            )
        else:
            print("SEMI collector found no requested-board links; preserving previous tab")
            semi_current = old_semi_current[:SEMI_CURRENT_LIMIT]
            new_semi = []
    except Exception as error:
        print(f"SEMI collection skipped: {type(error).__name__}; preserving previous tab")
        semi_current = old_semi_current[:SEMI_CURRENT_LIMIT]
        new_semi = []
    semi_archive = merge_semi_archive(
        all_existing_semi, new_semi, semi_current
    )

    payload = {
        "updated_at": datetime.now(base.KST).strftime("%Y-%m-%d %H:%M KST"),
        "publication_window": {
            "from": (today - timedelta(days=2)).isoformat(),
            "to": today.isoformat(),
        },
        "today_verified_count": len(new_items),
        "current_items": current,
        "archive_items": archive,
        "semi_items": semi_current,
        "semi_archive_items": semi_archive,
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
    print(
        f"saved SEMI current {len(semi_current)}; "
        f"new {len(new_semi)}; archive {len(semi_archive)}"
    )


if __name__ == "__main__":
    main()
