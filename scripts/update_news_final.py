"""Process Brief final manual-first edition.

Builds a fresh current brief and separate on-site archives. Automatic news is
limited to the latest 24 hours, extended up to 36 hours only when a delayed
scheduled run would otherwise leave a collection gap.
"""

import json
import os
import re
import hashlib
import html
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlparse, urlsplit, urlunsplit
from urllib.request import Request
from xml.etree import ElementTree

import update_news_v6 as v6


v3 = v6.v3
base = v6.base
TARGETS = {"semiconductor": 5, "battery": 5}
DOMESTIC_TARGETS = {"semiconductor": 4, "battery": 4}
MAX_ATTEMPTS = {"semiconductor": 12, "battery": 15}
ARCHIVE_LIMIT = 3000
SEMI_ARCHIVE_LIMIT = 1000
MANUAL_LIMIT = 2500
MANUAL_SECTORS = {"semiconductor", "battery", "semi_market"}

# KIPOST and TheElec remain visible in the historical archive, but are no
# longer admitted into the daily brief. The daily pool uses primary sources,
# wire services, and established general technology/business publications.
base.FEEDS[:] = [
    feed for feed in base.FEEDS
    if not feed.get("source", "").startswith("디일렉")
    and feed.get("source") != "KIPOST"
]

DOMESTIC_SPECIALIST_FEEDS = [
    {
        "url": "https://www.thelec.kr/rss/S1N2.xml",
        "source": "디일렉",
        "region": "domestic",
        "fixed_sector": "semiconductor",
    },
    {
        "url": "https://www.thelec.kr/rss/S1N3.xml",
        "source": "디일렉",
        "region": "domestic",
        "fixed_sector": None,
    },
    {
        "url": "https://www.kipost.net/rss/allArticle.xml",
        "source": "KIPOST",
        "region": "domestic",
        "fixed_sector": None,
    },
]

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

TRUSTED_NEWS_NAMES = {
    "연합뉴스": ("연합뉴스", "통신사 보도"),
    "뉴시스": ("뉴시스", "통신사 보도"),
    "전자신문": ("전자신문", "산업 전문언론"),
    "ZDNet Korea": ("ZDNet Korea", "산업 전문언론"),
    "지디넷코리아": ("ZDNet Korea", "산업 전문언론"),
    "한국경제": ("한국경제", "경제지 보도"),
    "서울경제": ("서울경제", "경제지 보도"),
    "매일경제": ("매일경제", "경제지 보도"),
    "조선비즈": ("조선비즈", "경제지 보도"),
}

GOOGLE_NEWS_QUERIES = {
    "semiconductor": [
        "(반도체 OR HBM OR 파운드리 OR D램 OR 낸드) when:3d",
        "(삼성전자 OR SK하이닉스 OR TSMC) 반도체 when:3d",
        "반도체 (공급망 OR 장비 OR 소재 OR 투자 OR 공장) when:3d",
    ],
    "battery": [
        "(배터리 OR 이차전지 OR 양극재 OR 전고체 OR ESS) when:3d",
        "(LG에너지솔루션 OR 삼성SDI OR SK온 OR CATL) when:3d",
    ],
}

SOURCE_RANK = {
    "정부·협회": 5,
    "기업 공식 발표": 4,
    "시장조사기관": 4,
    "통신사 보도": 3,
    "산업 전문언론": 2,
    "경제지 보도": 2,
    "해외 산업 보도": 1,
}


def trusted_news_identity(raw_name):
    raw_name = base.clean(raw_name)
    for token, identity in TRUSTED_NEWS_NAMES.items():
        if token.lower() in raw_name.lower():
            return identity
    return None


def collect_trusted_headlines():
    """Collect dated headlines from an allow-listed set of publishers.

    These entries are also the quota fallback when a publisher blocks article
    body access. In that case only publisher, title, date, and link are shown.
    """
    rows = []
    now = datetime.now(base.KST)
    for sector, queries in GOOGLE_NEWS_QUERIES.items():
        accepted = 0
        for query in queries:
            url = (
                "https://news.google.com/rss/search?q=" + quote_plus(query)
                + "&hl=ko&gl=KR&ceid=KR:ko"
            )
            try:
                root = ElementTree.fromstring(v6.v5.v4.fetch(url))
            except Exception as error:
                print(f"trusted headline feed skipped: {sector} ({type(error).__name__})")
                continue
            for entry in v6.v5.find_entries(root):
                source_raw = v6.v5.any_node_text(entry, "source")
                identity = trusted_news_identity(source_raw)
                if not identity:
                    continue
                source, source_type = identity
                title = base.clean(v6.v5.any_node_text(entry, "title"))
                title = re.sub(rf"\s+-\s+{re.escape(source_raw)}\s*$", "", title).strip()
                link = base.clean(v6.v5.any_node_text(entry, "link"))
                published = base.parse_date(
                    v6.v5.any_node_text(entry, "pubDate", "published", "updated", "date")
                )
                if not title or not link:
                    continue
                item = {
                "id": hashlib.sha1(canonical_url(link).encode()).hexdigest()[:12],
                "sector": sector,
                "region": "domestic",
                "category": base.detect_category(title),
                "title": title,
                "source": source,
                "source_type": source_type,
                "published": published.strftime("%Y-%m-%d %H:%M"),
                "collected": now.strftime("%Y-%m-%d"),
                "link": link,
                "rss_description": title,
                "trusted_headline": True,
                "score": base.relevance_score({
                    "title": title,
                    "rss_description": title,
                    "region": "domestic",
                }) + 6,
                }
                if useful_candidate(item):
                    rows.append(item)
                    accepted += 1
        print(f"trusted headlines: {sector} {accepted} candidates")
    return unique_rows(rows)

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
# Naver RSS exposes the boards under "반도체업계 뉴스" as separate
# category values. "SEMI 뉴스" contains most of the daily article posts.
SEMI_CATEGORIES = {"SEMI 뉴스", "시장동향 뉴스", "SEMI 회원사 동향 뉴스"}
SEMI_MARKET_CATEGORY_NO = "45"
SEMI_TITLE_LIST = "https://blog.naver.com/PostTitleListAsync.naver"

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


def published_datetime(item):
    value = str(item.get("published", "")).strip()
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value[:16] if "%H" in pattern else value[:10], pattern).replace(
                tzinfo=base.KST
            )
        except (TypeError, ValueError):
            continue
    return None


def parse_update_datetime(value):
    try:
        return datetime.strptime(str(value)[:16], "%Y-%m-%d %H:%M").replace(tzinfo=base.KST)
    except (TypeError, ValueError):
        return None


def automatic_window_start(now, previous_update):
    normal_start = now - timedelta(hours=24)
    if previous_update and previous_update < normal_start:
        return max(now - timedelta(hours=36), previous_update)
    return normal_start


def within_automatic_window(item, start, end):
    published = published_datetime(item)
    return published is not None and start <= published <= end


def unique_rows(rows):
    # Run title-similarity deduplication separately per sector so one field can
    # never remove candidates belonging to the other field.
    result = []
    for sector in TARGETS:
        sector_rows = [item for item in rows if item.get("sector") == sector]
        result.extend(base.deduplicate(sector_rows))
    return result


def useful_candidate(item):
    title_text = item.get("title", "").lower()
    text = f"{title_text} {item.get('rss_description', '')}".lower()
    if any(term in text for term in base.EXCLUDE):
        return False
    if any(term in text for term in (
        "목표가", "투자의견", "증권", "컨센서스", "주식", "etf",
        "채용", "성과급", "대학", "한양대", "성균관대", "kaist", "포스텍",
        "연구팀", "연구진", "원리 규명", "원인 규명",
    )):
        return False
    if any(term in text for term in (
        "콘퍼런스", "컨퍼런스", "세미나", "포럼", "웨비나", "전시회",
        "박람회", "행사 개최", "참가 모집", "설명회", "기념식", "시상식",
        "인터뷰", "기고", "칼럼", "기자24시", "오피니언",
        "conference", "seminar", "forum", "webinar", "exhibition",
    )):
        return False
    if any(term in title_text for term in (
        "공포의", "살 천재", "알고 보니", "알고보니", "대체 왜", "정체는",
        "무슨 일이", "충격", "발칵", "들썩", "개미", "돈 몰린", "베팅",
    )):
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
    impact_terms = tuple(v3.HIGH_IMPACT_TERMS) + (
        "공정", "장비", "소재", "수율", "웨이퍼", "hbm", "d램", "dram",
        "낸드", "파운드리", "euv", "패키징", "칩렛", "양극재", "음극재",
        "전해질", "분리막", "전고체", "lfp", "ess", "생산능력", "가동률",
        "출하", "점유율", "가격", "수요", "매출", "영업이익", "공급망",
        "협약", "인수", "합작", "규제", "관세", "보조금", "본격화", "개발",
        "재가동", "가동 중단", "복구", "회복", "감소", "증가", "전환", "재편",
        "수출", "진입", "확대", "추격", "선두", "점유율",
    )
    sector_title_terms = (
        "반도체", "웨이퍼", "hbm", "d램", "dram", "낸드", "파운드리",
        "euv", "패키징", "칩렛", "soc", "mcu", "차량용 칩", "ai 칩",
        "삼성전자", "sk하이닉스", "tsmc", "asml", "르네사스", "미디어텍", "퀄컴",
        "배터리", "이차전지", "양극", "음극", "전극", "전해질", "분리막",
        "전고체", "lfp", "ess", "lg에너지솔루션", "삼성sdi", "sk온", "catl",
    )
    return item.get("manual_added") is True or (
        any(term in title_text for term in sector_title_terms)
        and any(term in title_text for term in impact_terms)
    )


def admitted_daily_source(item):
    return True


def priority_key(item):
    date = published_date(item)
    source_type = item.get("source_type") or SOURCE_TYPES.get(item.get("source"), "")
    return (
        date.isoformat() if date else "",
        1 if item.get("manual_added") else 0,
        SOURCE_RANK.get(source_type, 0),
        item.get("score", 0),
        item.get("published", ""),
    )


def tag_source(item):
    item = dict(item)
    if item.get("source") == "KIPOST" or item.get("source", "").startswith("디일렉"):
        item["source_type"] = "산업 전문언론"
        item.pop("fallback_specialist", None)
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
    rows = [tag_source(item) for item in base.collect() if admitted_daily_source(item)]
    rows.extend(collect_trusted_headlines())
    for config in DOMESTIC_SPECIALIST_FEEDS:
        try:
            found = base.read_feed(config)
            for item in found:
                item["source_type"] = "산업 전문언론"
                item["source_rank"] = SOURCE_RANK["산업 전문언론"]
            rows.extend(found)
            print(f"domestic specialist: {config['source']} {len(found)} candidates")
        except Exception as error:
            print(f"domestic specialist skipped: {config['source']} ({type(error).__name__})")
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
        if item.get("verified_source") is not True and item.get("summary_status") != "link_only":
            continue
        if item.get("id"):
            by_id[item["id"]] = item
        if item.get("link"):
            by_url[canonical_url(item["link"])] = item
    return by_id, by_url


def same_story(left, right):
    replacements = {
        "디램": "d램", "dram": "d램", "낸드플래시": "낸드",
        "엘지에너지솔루션": "lg에너지솔루션", "삼성 에스디아이": "삼성sdi",
        "에스케이온": "sk온", "급등": "상승", "껑충": "상승",
        "사상 최고": "최고", "역대 최고": "최고",
    }
    left_text = left.get("title", "").lower()
    right_text = right.get("title", "").lower()
    for old, new in replacements.items():
        left_text = left_text.replace(old, new)
        right_text = right_text.replace(old, new)
    left_tokens = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", left_text))
    right_tokens = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", right_text))
    overlap = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    if overlap >= 0.32:
        return True
    companies = (
        "삼성전자", "sk하이닉스", "tsmc", "마이크론", "엘앤에프",
        "lg에너지솔루션", "삼성sdi", "sk온", "에코프로", "포스코퓨처엠",
        "catl",
    )
    products = (
        "hbm", "d램", "낸드", "파운드리", "lfp", "ess",
        "양극재", "음극재", "전고체", "웨이퍼", "euv", "패키징",
    )
    event_groups = (
        ("가격", "상승", "최고", "고정거래가격"),
        ("실적", "매출", "영업이익", "흑자", "적자"),
        ("투자", "증설", "공장", "팹", "생산능력"),
        ("수주", "공급계약", "장기계약", "납품"),
        ("양산", "출하", "생산", "가동", "재가동"),
        ("규제", "관세", "보조금", "수출통제", "정책"),
        ("인수", "합병", "합작", "지분"),
        ("개발", "공개", "출시", "인증"),
    )
    shared_companies = {word for word in companies if word in left_text and word in right_text}
    shared_products = {word for word in products if word in left_text and word in right_text}
    shared_event = any(
        any(word in left_text for word in group)
        and any(word in right_text for word in group)
        for group in event_groups
    )
    if shared_event and (shared_products or (shared_companies and overlap >= 0.20)):
        return True
    left_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", left_text))
    right_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", right_text))
    return bool(left_numbers & right_numbers) and overlap >= 0.22


def select_sector(rows, sector, existing_items, api_key):
    target = TARGETS[sector]
    by_id, by_url = existing_lookup(existing_items)
    current, newly_verified = [], []
    attempts = reused = 0

    ordered = [
        item for item in ordered_sector_candidates(rows, sector)
    ]
    # A previously summarized article is cheap to reuse, but it must never
    # occupy all five slots before genuinely new candidates are inspected.
    fresh_candidates, reusable_candidates = [], []
    for candidate in ordered:
        old = by_id.get(candidate.get("id")) or by_url.get(
            canonical_url(candidate.get("link"))
        )
        (reusable_candidates if old else fresh_candidates).append(candidate)
    candidates = fresh_candidates + reusable_candidates
    for candidate in candidates:
        if len(current) >= target:
            break
        if not candidate.get("manual_added") and any(
            same_story(candidate, selected) for selected in current
        ):
            continue
        old = by_id.get(candidate.get("id")) or by_url.get(
            canonical_url(candidate.get("link"))
        )
        if old:
            current.append(old)
            reused += 1
            continue
        if attempts >= MAX_ATTEMPTS[sector]:
            # Continue walking so reusable verified reserves can still fill
            # the brief after the new-article verification budget is spent.
            continue
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
        elif candidate.get("trusted_headline"):
            fallback = dict(candidate)
            fallback.pop("rss_description", None)
            fallback.pop("score", None)
            fallback["verified_source"] = False
            fallback["summary_status"] = "link_only"
            fallback["overview"] = "원문 자동접근이 제한되어 제목과 원문 링크를 표시합니다."
            fallback["key_points"] = []
            fallback["numbers"] = []
            fallback["keywords"] = []
            fallback["stated_outlook"] = ""
            current.append(fallback)
            newly_verified.append(fallback)
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
        if (
            item.get("verified_source") is not True
            and item.get("summary_status") != "link_only"
        ) or not item.get("link"):
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
    # Smart Editor link cards often keep the target only inside an escaped
    # data-linkdata attribute, not in href or a JSON "link" key.
    urls.extend(v6.v5.v4.article_urls(decoded))
    blocked = (
        "blog.naver.com", "m.blog.naver.com", "rss.blog.naver.com",
        "link.naver.com", "pstatic.net", "ogp.me", "w3.org", "schema.org",
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


def parse_naver_add_date(value):
    """Parse the timestamp returned by Naver's category title-list endpoint."""
    if isinstance(value, (int, float)) or str(value or "").isdigit():
        stamp = int(value)
        if stamp > 10_000_000_000:
            stamp /= 1000
        return datetime.fromtimestamp(stamp, tz=base.KST)
    text = base.clean(str(value or ""))
    match = re.search(
        r"(20\d{2})\D+(\d{1,2})\D+(\d{1,2})(?:\D+(\d{1,2})\D+(\d{1,2}))?",
        text,
    )
    if not match:
        return None
    year, month, day, hour, minute = match.groups()
    return datetime(
        int(year), int(month), int(day), int(hour or 0), int(minute or 0),
        tzinfo=base.KST,
    )


def collect_semi_market_board():
    """Read the live 시장동향 뉴스 list instead of the stale Naver RSS."""
    query = urlencode({
        "blogId": "semi_blog",
        "viewdate": "",
        "currentPage": "1",
        "categoryNo": SEMI_MARKET_CATEGORY_NO,
        "parentCategoryNo": "",
        "countPerPage": "50",
    })
    raw = v6.v5.v4.fetch(f"{SEMI_TITLE_LIST}?{query}")
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Naver title-list response did not contain JSON")
    payload = json.loads(raw[start:end + 1])
    posts = payload.get("postList") or payload.get("result", {}).get("postList") or []
    parsed = []
    for post in posts:
        log_no = str(post.get("logNo") or post.get("log_no") or "").strip()
        title = base.clean(html.unescape(str(post.get("title") or "")))
        published = parse_naver_add_date(
            post.get("addDate") or post.get("add_date") or post.get("writeDate")
        )
        if not log_no or not title or published is None:
            continue
        parsed.append({
            "title": title,
            "published": published,
            "post_url": f"https://blog.naver.com/semi_blog/{log_no}",
            "category": "시장동향 뉴스",
            "description": title,
        })
    if not parsed:
        raise ValueError("Naver title-list response contained no usable posts")
    latest_day = max(post["published"].date() for post in parsed)
    latest = [post for post in parsed if post["published"].date() == latest_day]
    print(f"SEMI live board latest day: {latest_day}; posts {len(latest)}")
    return latest


def collect_semi_candidates():
    """Collect the latest posts from every board under 반도체업계 뉴스.

    The live 시장동향 board is authoritative because Naver's RSS can lag by
    several days. RSS remains only as a fallback when the live list is blocked.
    """
    try:
        latest_posts = collect_semi_market_board()
        board_count = len(latest_posts)
    except Exception as error:
        print(f"SEMI live board skipped ({type(error).__name__}); falling back to RSS")
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
        dated_entries = []
        for entry in board_entries:
            published = base.parse_date(
                v6.v5.any_node_text(entry, "pubDate", "published", "updated", "date")
            )
            dated_entries.append((entry, published))
        if not dated_entries:
            return []
        latest_day = max(published.date() for _, published in dated_entries)
        latest_posts = []
        for entry, published in dated_entries:
            if published.date() != latest_day:
                continue
            latest_posts.append({
                "title": base.clean(v6.v5.any_node_text(entry, "title")),
                "description": v6.v5.any_node_text(
                    entry, "description", "encoded", "content", "summary"
                ),
                "post_url": base.clean(v6.v5.any_node_text(entry, "link")),
                "category": base.clean(v6.v5.any_node_text(entry, "category", "subject")),
                "published": published,
            })
        board_count = len(board_entries)
        print(f"SEMI RSS latest publication day: {latest_day}; posts {len(latest_posts)}")

    results = []
    body_access = link_count = 0
    for post in latest_posts:
        title = post["title"]
        description_raw = post.get("description", "")
        post_url = post["post_url"]
        category = post["category"]
        published = post["published"]
        urls = semi_article_urls(description_raw)
        if post_url:
            try:
                body = v6.v5.v4.fetch(v6.v5.v4.mobile_post_url(post_url))
                body_access += 1
                urls = list(dict.fromkeys(urls + semi_article_urls(body)))
            except Exception as error:
                print(f"SEMI body skipped: {title[:45]} ({type(error).__name__})")
        article_url = canonical_url(urls[0]) if urls else canonical_url(post_url)
        if urls:
            link_count += 1
        else:
            print(f"SEMI external link not found; keeping blog post: {title[:55]}")
        if not article_url:
            continue
        original_source = semi_source(article_url) if urls else "SEMI Korea 블로그"
        item = {
            "id": hashlib.sha1(article_url.encode()).hexdigest()[:12],
            "sector": "semi_market",
            "region": "domestic",
            "category": v3.market_category(title),
            "title": re.sub(r"^\s*\[공유\]\s*", "", title).strip(),
            "source": "SEMI Korea 블로그",
            "source_type": f"반도체업계 뉴스 · {category}",
            "original_source": original_source,
            "published": published.strftime("%Y-%m-%d %H:%M"),
            "collected": datetime.now(base.KST).strftime("%Y-%m-%d"),
            "link": article_url,
            "rss_description": base.clean(description_raw)[:1000] or title,
            "semi_post_link": post_url,
            "discovered_via": f"SEMI Korea 블로그 · 반도체업계 뉴스 > {category}",
            "summary_status": "link_only",
            "score": v3.industry_score({
                "title": title,
                "rss_description": base.clean(description_raw),
                "region": "domestic",
            }),
        }
        results.append(item)
    print(
        f"SEMI stages: board {board_count}, body accessible {body_access}, "
        f"original links {link_count}, eligible {len(results)}"
    )
    ordered, seen_urls = [], set()
    for item in results:
        url = canonical_url(item.get("link"))
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        ordered.append(item)
    return ordered


def select_semi(candidates, old_items, api_key):
    by_id, by_url = existing_lookup(old_items)
    current, newly_verified = [], []
    for candidate in candidates:
        old = by_id.get(candidate.get("id")) or by_url.get(canonical_url(candidate.get("link")))
        if old:
            reused = dict(old)
            for key in ("source", "source_type", "original_source", "semi_post_link", "discovered_via"):
                reused[key] = candidate.get(key)
            current.append(reused)
            continue
        # Yonhap marks its articles as unavailable for AI training/use. Keep
        # SEMI-curated Yonhap titles and links, but do not send the body to the
        # summarisation model.
        if candidate.get("original_source") == "연합뉴스":
            fallback = dict(candidate)
            fallback.pop("rss_description", None)
            fallback.pop("score", None)
            fallback["verified_source"] = False
            fallback["summary_status"] = "link_only"
            fallback["overview"] = "연합뉴스 이용 조건에 따라 제목과 원문 링크만 표시합니다."
            fallback["key_points"] = []
            fallback["numbers"] = []
            fallback["keywords"] = []
            fallback["stated_outlook"] = ""
            current.append(fallback)
            continue
        try:
            verified = base.enrich_one(candidate, api_key)
        except Exception as error:
            print(f"SEMI summary failed: {candidate.get('source')} ({type(error).__name__})")
            verified = None
        if verified:
            verified["summary_status"] = "summarized"
            current.append(verified)
            newly_verified.append(verified)
        else:
            fallback = dict(candidate)
            fallback.pop("rss_description", None)
            fallback.pop("score", None)
            fallback["verified_source"] = False
            fallback["summary_status"] = "link_only"
            fallback["overview"] = "자동요약에 실패해 제목과 원문 링크만 표시합니다."
            fallback["key_points"] = []
            fallback["numbers"] = []
            fallback["keywords"] = []
            fallback["stated_outlook"] = ""
            current.append(fallback)
    print(
        f"SEMI selected: candidates {len(candidates)}, new {len(newly_verified)}, "
        f"current {len(current)} (latest-day posts)"
    )
    return current, newly_verified


def merge_semi_archive(old_items, newly_verified, current):
    merged = {}
    for item in old_items + newly_verified:
        if (item.get("verified_source") is True or item.get("summary_status") == "link_only") and item.get("link"):
            merged[canonical_url(item["link"])] = item
    current_urls = {canonical_url(item.get("link")) for item in current}
    return sorted(
        [item for url, item in merged.items() if url not in current_urls],
        key=lambda item: (item.get("published", ""), item.get("collected", "")),
        reverse=True,
    )[:SEMI_ARCHIVE_LIMIT]


def build_manual_candidate(url, sector):
    if sector not in MANUAL_SECTORS:
        raise ValueError("manual sector must be semiconductor, battery, or semi_market")
    requested_url = url
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("manual article URL must be http or https")
    raw = ""
    try:
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 ProcessBrief/1.0"})
        with base.urlopen(request, timeout=25) as response:
            url = response.geturl()
            raw = response.read().decode("utf-8", errors="ignore")
    except Exception as error:
        print(f"manual article page unreadable ({type(error).__name__}); trying Gemini URL access")
    parsed = urlparse(url)
    title = ""
    for pattern in (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
        r'<title[^>]*>(.*?)</title>',
    ):
        match = re.search(pattern, raw, re.IGNORECASE | re.DOTALL)
        if match:
            title = base.clean(match.group(1))
            break
    published = None
    for pattern in (
        r'(?:article:published_time|datePublished)[^>\n]{0,100}?(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}(?:[T ]\d{1,2}:\d{2})?)',
        r'(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}(?:[T ]\d{1,2}:\d{2})?)[^>\n]{0,100}?(?:article:published_time|datePublished)',
    ):
        match = re.search(pattern, raw, re.IGNORECASE)
        if match:
            published = base.parse_date(match.group(1))
            break
    now = datetime.now(base.KST)
    published = published or now
    source = semi_source(url)
    return {
        "id": hashlib.sha1(canonical_url(url).encode()).hexdigest()[:12],
        "sector": sector,
        "region": "domestic" if parsed.netloc.lower().endswith(".kr") else "global",
        "category": base.detect_category(title),
        "title": title or f"수동 추가 기사 · {source}",
        "source": source,
        "source_type": "사용자 선택 기사",
        "published": published.strftime("%Y-%m-%d %H:%M"),
        "collected": now.strftime("%Y-%m-%d"),
        "link": canonical_url(url),
        "requested_link": canonical_url(requested_url),
        "rss_description": title,
        "manual_added": True,
    }


def enrich_manual(candidate, api_key):
    prompt = f"""
다음 기사 원문을 읽고 사용자가 산업 동향을 공부하기 좋은 한국어 브리핑으로 정리하라.
URL: {candidate['link']}

규칙:
1. 기사 요약과 수치·전망은 원문에 명시된 사실만 사용한다.
2. 첫 문단은 무엇이 바뀌었는지, 관련 기업·제품·공정·시장 영향을 4~6문장으로 설명한다.
3. 둘째 문단은 관련 기업의 국가, 주력 사업, 기술, 공급망 위치처럼 널리 확립된 기본 배경을 2~4문장으로 설명한다. 최신 실적·점유율·전망은 원문에 없으면 추가하지 않는다.
4. 숫자·날짜·기업명·기술명은 원문과 일치할 때만 쓴다.
5. 원문을 충분히 읽지 못하면 accessible을 false로 설정한다.

JSON 객체 하나만 반환:
{{
  "accessible": true 또는 false,
  "title": "원문에 표시된 기사 제목",
  "overview": "산업 변화와 의미를 설명하는 4~6문장",
  "industry_context": "기업·기술·공급망 배경 2~4문장. 원문에 없으면 빈 문자열",
  "key_points": ["핵심 사실 1", "핵심 사실 2", "핵심 사실 3"],
  "numbers": ["핵심 수치·날짜"] 또는 [],
  "keywords": ["핵심 키워드 3~6개"],
  "stated_outlook": "원문에 직접 언급된 계획·전망. 없으면 빈 문자열"
}}
"""
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{base.MODEL}:generateContent?{urlencode({'key': api_key})}"
    )
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"url_context": {}}],
        "generationConfig": {"responseMimeType": "application/json"},
    }).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with base.urlopen(request, timeout=120) as response:
        raw = json.loads(response.read().decode("utf-8"))
    model_candidate = raw["candidates"][0]
    metadata = model_candidate.get("urlContextMetadata") or model_candidate.get("url_context_metadata") or {}
    url_metadata = metadata.get("urlMetadata") or metadata.get("url_metadata") or []
    retrieved = any(
        (entry.get("urlRetrievalStatus") or entry.get("url_retrieval_status"))
        == "URL_RETRIEVAL_STATUS_SUCCESS"
        for entry in url_metadata
    )
    result = base.parse_model_json(model_candidate["content"]["parts"][0]["text"])
    if not retrieved or not result.get("accessible"):
        return None
    item = dict(candidate)
    model_title = base.clean(result.get("title", ""))
    item.update({
        "title": model_title or item.get("title", ""),
        "verified_source": True,
        "overview": base.clean(result.get("overview", "")),
        "industry_context": base.clean(result.get("industry_context", "")),
        "key_points": [base.clean(x) for x in result.get("key_points", []) if base.clean(x)][:5],
        "numbers": [base.clean(x) for x in result.get("numbers", []) if base.clean(x)][:6],
        "keywords": [base.clean(x) for x in result.get("keywords", []) if base.clean(x)][:6],
        "stated_outlook": base.clean(result.get("stated_outlook", "")),
    })
    return item if item["overview"] and len(item["key_points"]) >= 2 else None


def summarize_manual(candidate, api_key):
    if candidate.get("source") == "연합뉴스":
        verified = None
    else:
        try:
            verified = enrich_manual(candidate, api_key)
        except Exception as error:
            print(f"manual summary failed ({type(error).__name__})")
            verified = None
    if verified:
        verified["summary_status"] = "summarized"
        verified["manual_added"] = True
        return verified
    fallback = dict(candidate)
    fallback.pop("rss_description", None)
    fallback["verified_source"] = False
    fallback["summary_status"] = "link_only"
    fallback["overview"] = (
        "연합뉴스 이용 조건에 따라 제목과 원문 링크만 표시합니다."
        if candidate.get("source") == "연합뉴스"
        else "원문 자동접근이 제한되어 제목과 원문 링크를 표시합니다."
    )
    fallback["key_points"] = []
    fallback["numbers"] = []
    fallback["keywords"] = []
    fallback["stated_outlook"] = ""
    return fallback


def merge_manual_items(old_items, new_items):
    merged = {}
    for item in old_items + new_items:
        if not item.get("link"):
            continue
        item = dict(item)
        item["manual_added"] = True
        item["source_type"] = "직접 선택"
        merged[canonical_url(item["link"])] = item
    return sorted(
        merged.values(),
        key=lambda item: (item.get("collected", ""), item.get("published", "")),
        reverse=True,
    )[:MANUAL_LIMIT]


def without_deleted(items, delete_ids):
    if not delete_ids:
        return list(items)
    return [item for item in items if str(item.get("id", "")) not in delete_ids]


def with_edited_title(items, edit_id, edit_title):
    if not edit_id or not edit_title:
        return list(items)
    result = []
    for item in items:
        item = dict(item)
        if str(item.get("id", "")) == edit_id:
            item["title"] = base.clean(edit_title)[:300]
        result.append(item)
    return result


def toggle_favorite(favorites, source_items, item_id, state):
    """Keep full article snapshots so archive trimming never deletes stars."""
    by_id = {str(item.get("id", "")): dict(item) for item in favorites if item.get("id")}
    if not item_id:
        return list(by_id.values())
    if state == "false":
        by_id.pop(item_id, None)
    elif state == "true":
        match = next((item for item in source_items if str(item.get("id", "")) == item_id), None)
        if match:
            by_id[item_id] = dict(match)
        else:
            print(f"favorite article not found: {item_id}")
    return sorted(
        by_id.values(),
        key=lambda item: (item.get("collected", ""), item.get("published", "")),
        reverse=True,
    )


def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required")

    old = v3.read_old()
    delete_ids = {
        value for value in re.split(r"[\s,]+", os.getenv("DELETE_ARTICLE_ID", "").strip())
        if value
    }
    deleted_article_ids = set(old.get("deleted_article_ids", [])) | delete_ids
    edit_id = os.getenv("EDIT_ARTICLE_ID", "").strip()
    edit_title = os.getenv("EDIT_ARTICLE_TITLE", "").strip()
    favorite_id = os.getenv("FAVORITE_ARTICLE_ID", "").strip()
    favorite_state = os.getenv("FAVORITE_STATE", "").strip().lower()
    legacy = old.get("items", [])
    old_current = old.get("current_items", [])
    old_archive = old.get("archive_items", [])
    legacy = without_deleted(legacy, deleted_article_ids)
    old_current = without_deleted(old_current, deleted_article_ids)
    old_archive = without_deleted(old_archive, deleted_article_ids)
    all_existing = [tag_source(item) for item in old_current + old_archive + legacy]
    old_semi_current = old.get("semi_items", [])
    old_semi_archive = old.get("semi_archive_items", [])
    legacy_market = old.get("market_items", [])
    old_semi_current = without_deleted(old_semi_current, deleted_article_ids)
    old_semi_archive = without_deleted(old_semi_archive, deleted_article_ids)
    legacy_market = without_deleted(legacy_market, deleted_article_ids)
    all_existing_semi = old_semi_current + old_semi_archive + legacy_market
    old_manual_items = old.get("manual_items", [])
    old_manual_items = without_deleted(old_manual_items, deleted_article_ids)
    old_manual_items = with_edited_title(old_manual_items, edit_id, edit_title)
    if delete_ids:
        print(f"deleted stored article ids: {len(delete_ids)}")
    if edit_id and edit_title:
        print(f"edited manual article title: {edit_id}")

    now = datetime.now(base.KST)
    previous_update = parse_update_datetime(
        old.get("last_automatic_update_at") or old.get("updated_at")
    )
    window_start = automatic_window_start(now, previous_update)
    fetched = without_deleted(collect_all_candidates(), deleted_article_ids)
    # Keep already verified articles inside the active time window as reusable
    # candidates even if a feed is temporarily unavailable on this run.
    reusable_candidates = []
    for item in all_existing:
        if item.get("verified_source") is not True and item.get("summary_status") != "link_only":
            continue
        candidate = dict(item)
        candidate.setdefault("score", 0)
        candidate.setdefault("rss_description", candidate.get("overview", ""))
        reusable_candidates.append(candidate)
    collected = unique_rows(fetched + reusable_candidates)
    eligible = [
        item for item in collected
        if within_automatic_window(item, window_start, now)
        and (item.get("manual_added") is True or useful_candidate(item))
    ]
    print(
        f"publication window: {window_start.isoformat()} to {now.isoformat()}; "
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

    manual_url = os.getenv("MANUAL_ARTICLE_URL", "").strip()
    manual_sector = os.getenv("MANUAL_ARTICLE_SECTOR", "").strip()
    new_manual_items = []
    if manual_url:
        requested_urls = list(dict.fromkeys(
            value.strip() for value in manual_url.splitlines() if value.strip()
        ))
        new_manual_items = [
            summarize_manual(build_manual_candidate(url, manual_sector), api_key)
            for url in requested_urls
        ]
        for item in new_manual_items:
            item["source_type"] = "직접 선택"
        print(f"manual articles added: {manual_sector} · {len(new_manual_items)}")
    manual_items = merge_manual_items(old_manual_items, new_manual_items)

    archive = merge_archive(all_existing, new_items, current)

    # Naver's RSS and live board endpoints are not stable from GitHub Actions.
    # Keep old SEMI records in the automatic archive, but stop automatic SEMI
    # collection. New SEMI articles are added through the manual form.
    semi_current = []
    new_semi = []
    semi_archive = merge_semi_archive(all_existing_semi, [], [])

    favorite_source = (
        old_current + old_archive + legacy + old_semi_current + old_semi_archive
        + legacy_market + old_manual_items + current + archive + manual_items
    )
    favorite_items = toggle_favorite(
        old.get("favorite_items", []), favorite_source, favorite_id, favorite_state
    )
    if favorite_id and favorite_state in {"true", "false"}:
        print(f"favorite updated: {favorite_id} -> {favorite_state}")

    payload = {
        "updated_at": now.strftime("%Y-%m-%d %H:%M KST"),
        "last_automatic_update_at": now.strftime("%Y-%m-%d %H:%M KST"),
        "publication_window": {
            "from": window_start.isoformat(),
            "to": now.isoformat(),
        },
        "today_verified_count": len(new_items),
        "current_items": current,
        "archive_items": archive,
        "semi_items": semi_current,
        "semi_archive_items": semi_archive,
        "manual_items": manual_items,
        "favorite_items": favorite_items,
        "deleted_article_ids": sorted(deleted_article_ids)[-10000:],
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
        f"SEMI automatic collection disabled; preserved archive {len(semi_archive)}"
    )


if __name__ == "__main__":
    main()
