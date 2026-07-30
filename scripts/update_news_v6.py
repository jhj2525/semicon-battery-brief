"""Process Brief v6

- Keep the v5 SEMI collector unchanged.
- Prefer useful official company/association posts when they are available.
- Fall back to ordinary domestic industry reporting when official posts are
  absent, stale, promotional, duplicated, or inaccessible.
- Select up to 6 semiconductor articles and up to 8 battery articles.
"""

import hashlib
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse

import update_news_v5 as v5


v3 = v5.v4.v3
base = v3.base
base.MAX_ARCHIVE_ITEMS = 800


# RSS feeds that are actually exposed by the official newsroom.
OFFICIAL_FEEDS = [
    {
        "url": "https://news.skhynix.co.kr/feed/",
        "source": "SK하이닉스 뉴스룸",
        "region": "domestic",
        "fixed_sector": "semiconductor",
        "source_type": "기업 공식 발표",
    },
]


# Official list pages without a dependable public RSS feed.
# include_path prevents navigation, recruitment, culture, and unrelated links
# from entering the candidate pool.
OFFICIAL_PAGES = [
    {
        "url": "https://news.samsungsemiconductor.com/global/category/news/",
        "source": "삼성전자 반도체 뉴스룸",
        "sector": "semiconductor",
        "include_path": ("/global/",),
        "source_type": "기업 공식 발표",
    },
    {
        "url": "https://news.lgensol.com/company-news/press-releases/",
        "source": "LG에너지솔루션 뉴스룸",
        "sector": "battery",
        "include_path": ("/company-news/press-releases/",),
        "source_type": "기업 공식 발표",
    },
    {
        "url": "https://www.samsungsdi.com/sdi-now/sdi-news/list.html",
        "source": "삼성SDI 뉴스룸",
        "sector": "battery",
        "include_path": ("/sdi-now/sdi-news/",),
        "source_type": "기업 공식 발표",
    },
    {
        "url": "https://eng.sk-on.com/company/press.asp",
        "source": "SK온 뉴스룸",
        "sector": "battery",
        "include_path": ("press_view.asp",),
        "source_type": "기업 공식 발표",
    },
    {
        "url": "https://www.k-bia.or.kr/",
        "source": "한국배터리산업협회",
        "sector": "battery",
        "include_title": (
            "news daily", "news weekly", "daily news", "weekly",
            "주간 배터리", "배터리 동향", "정책·기술동향",
        ),
        "source_type": "협회 동향",
    },
]


PROMOTIONAL_TERMS = [
    "채용", "인재", "봉사", "캠페인", "수상", "기념", "대학생", "앰버서더",
    "브랜드", "전시 관람", "이벤트", "인터뷰", "조직문화", "임직원 이야기",
    "recruit", "career", "award", "anniversary", "volunteer", "campaign",
]

OFFICIAL_IMPACT_TERMS = [
    "실적", "매출", "영업이익", "수주", "계약", "공급", "투자", "증설",
    "공장", "라인", "양산", "생산", "출하", "공정", "장비", "소재",
    "hbm", "dram", "낸드", "파운드리", "웨이퍼", "배터리", "전극",
    "양극", "음극", "전해질", "분리막", "ess", "lfp", "전고체",
    "earnings", "revenue", "contract", "supply", "investment", "factory",
    "plant", "production", "manufacturing", "capacity", "battery",
    "semiconductor", "foundry", "memory",
]


def tag_official(item, source_type):
    item["source_type"] = source_type
    item["official_source"] = True
    item["score"] = v3.industry_score(item) + 8
    return item


def read_official_feed(config):
    feed_config = {
        key: config[key]
        for key in ("url", "source", "region", "fixed_sector")
    }
    return [
        tag_official(item, config["source_type"])
        for item in base.read_feed(feed_config)
        if useful_official_title(item["title"])
    ]


def useful_official_title(title):
    lower = title.lower()
    return (
        not any(term in lower for term in PROMOTIONAL_TERMS)
        and any(term in lower for term in OFFICIAL_IMPACT_TERMS)
        and not any(term in lower for term in base.EXCLUDE)
    )


def extract_date(fragment):
    patterns = [
        r"(20\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})",
        r"(\d{1,2})[.\-/]\s*(\d{1,2})[.\-/]\s*(20\d{2})",
    ]
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, fragment)
        if not match:
            continue
        values = [int(value) for value in match.groups()]
        year, month, day = (
            values if index == 0 else (values[2], values[0], values[1])
        )
        try:
            return datetime(year, month, day, tzinfo=base.KST)
        except ValueError:
            pass
    return None


def absolute_link(page_url, href):
    link = urljoin(page_url, href.replace("&amp;", "&")).split("#", 1)[0]
    parsed = urlparse(link)
    if parsed.scheme not in {"http", "https"}:
        return ""
    return link


def page_candidates(config):
    raw = v5.v4.fetch(config["url"])
    # Keep a wide local context around each anchor because several official
    # sites put the date in a sibling element instead of inside the anchor.
    anchor_pattern = re.compile(
        r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    now = datetime.now(base.KST)
    results = []
    for match in anchor_pattern.finditer(raw):
        link = absolute_link(config["url"], match.group(1))
        title = base.clean(match.group(2))
        if not link or len(title) < 8:
            continue
        if config.get("include_path") and not any(
            token in link for token in config["include_path"]
        ):
            continue
        if config.get("include_title") and not any(
            token in title.lower() for token in config["include_title"]
        ):
            continue
        if not useful_official_title(title) and config["source_type"] != "협회 동향":
            continue

        context = raw[max(0, match.start() - 500):match.end() + 500]
        published = extract_date(base.clean(context))
        if published is None or now - published > timedelta(days=7):
            continue

        item = {
            "id": hashlib.sha1(link.encode()).hexdigest()[:12],
            "sector": config["sector"],
            "region": "domestic",
            "category": base.detect_category(title),
            "title": title,
            "source": config["source"],
            "published": published.strftime("%Y-%m-%d %H:%M"),
            "collected": now.strftime("%Y-%m-%d"),
            "link": link,
            "rss_description": title,
        }
        results.append(tag_official(item, config["source_type"]))
    return base.deduplicate(results)


original_collect = base.collect


def collect_v6():
    rows = original_collect()
    official_count = 0

    for config in OFFICIAL_FEEDS:
        try:
            found = read_official_feed(config)
            rows.extend(found)
            official_count += len(found)
        except Exception as error:
            print(
                f"official feed skipped: {config['source']} "
                f"({type(error).__name__})"
            )

    for config in OFFICIAL_PAGES:
        try:
            found = page_candidates(config)
            rows.extend(found)
            official_count += len(found)
        except Exception as error:
            print(
                f"official page skipped: {config['source']} "
                f"({type(error).__name__})"
            )

    print(f"official candidates: {official_count}")
    return rows


base.collect = collect_v6


def official_first(domestic, limit, official_limit=2):
    official = [item for item in domestic if item.get("official_source")]
    general = [item for item in domestic if not item.get("official_source")]

    # LGES is the first battery-company priority when a useful recent item exists.
    lges = [item for item in official if "lg에너지솔루션" in item["source"].lower()]
    picked = lges[:1]
    used = {item["id"] for item in picked}

    remaining_official = [item for item in official if item["id"] not in used]
    picked.extend(
        v3.select_diverse(
            remaining_official,
            max(0, official_limit - len(picked)),
            source_cap=1,
        )
    )
    used = {item["id"] for item in picked}
    remaining_general = [item for item in general if item["id"] not in used]
    picked.extend(v3.select_diverse(remaining_general, limit - len(picked)))

    # If ordinary news is scarce, allow more relevant official posts.
    if len(picked) < limit:
        used = {item["id"] for item in picked}
        picked.extend(
            item for item in official
            if item["id"] not in used
        )
    return picked[:limit]


def choose_main_v6(rows):
    result = []
    for sector in ("semiconductor", "battery"):
        pool = [item for item in rows if item["sector"] == sector]
        domestic = [item for item in pool if item["region"] == "domestic"]
        global_items = [
            item for item in pool
            if item["region"] == "global" and v3.is_useful_global(item)
        ]

        domestic_limit = 4 if sector == "semiconductor" else 6
        result.extend(official_first(domestic, domestic_limit))
        result.extend(v3.select_diverse(global_items, 2))
    return result


v3.choose_main = choose_main_v6


if __name__ == "__main__":
    v3.main()
