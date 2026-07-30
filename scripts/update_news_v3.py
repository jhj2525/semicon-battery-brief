"""Process Brief v3

- 분야별 국내 4개 + 해외 2개
- 디일렉 외 전자신문 공식 RSS 추가
- 해외는 주요 기업/공장/양산/투자/정책 기사만 허용
- SEMI Korea 네이버 블로그 시장동향을 별도 수집
  (상위 10개 원문 요약, 나머지는 원문 링크만 보관)
"""

import hashlib
import html
import json
import re
from datetime import datetime, timedelta
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

try:
    import update_news_v2 as base
except ImportError:
    # 로컬 검사용 호환 경로. GitHub 저장소에서는 update_news_v2를 사용한다.
    import update_news as base


base.MAX_PER_SECTOR = 6
base.MAX_ARCHIVE_ITEMS = 600

# 공식 RSS 또는 매체가 직접 운영하는 피드만 사용한다.
base.FEEDS = [
    {
        "url": "https://www.thelec.kr/rss/S1N2.xml",
        "source": "디일렉",
        "region": "domestic",
        "fixed_sector": "semiconductor",
    },
    {
        "url": "https://www.thelec.kr/rss/S1N3.xml",
        "source": "디일렉 소재·장비",
        "region": "domestic",
        "fixed_sector": None,
    },
    {
        "url": "https://www.thelec.kr/rss/allArticle.xml",
        "source": "디일렉",
        "region": "domestic",
        "fixed_sector": None,
    },
    {
        "url": "https://rss.etnews.com/Section901.xml",
        "source": "전자신문",
        "region": "domestic",
        "fixed_sector": None,
    },
    {
        "url": "https://rss.etnews.com/02.xml",
        "source": "전자신문 경제",
        "region": "domestic",
        "fixed_sector": None,
    },
    {
        "url": "https://rss.etnews.com/06064.xml",
        "source": "전자신문 소재",
        "region": "domestic",
        "fixed_sector": None,
    },
    {
        "url": "https://rss.etnews.com/06061.xml",
        "source": "전자신문 장비",
        "region": "domestic",
        "fixed_sector": None,
    },
    {
        "url": "https://rss.etnews.com/17.xml",
        "source": "전자신문 모빌리티",
        "region": "domestic",
        "fixed_sector": None,
    },
    {
        "url": "https://semiengineering.com/feed/",
        "source": "Semiconductor Engineering",
        "region": "global",
        "fixed_sector": "semiconductor",
    },
    {
        "url": "https://www.batterypoweronline.com/feed/",
        "source": "Battery Power Online",
        "region": "global",
        "fixed_sector": "battery",
    },
    {
        "url": "https://www.electrive.com/feed/",
        "source": "electrive",
        "region": "global",
        "fixed_sector": "battery",
    },
]

COMPANY_TERMS = [
    "삼성전자", "sk하이닉스", "하이닉스", "lg에너지솔루션", "엔솔", "삼성sdi",
    "sk온", "에코프로", "포스코퓨처엠", "한미반도체", "원익", "주성엔지니어링",
    "tsmc", "asml", "micron", "intel", "samsung", "sk hynix", "catl",
    "panasonic", "globalfoundries", "umc", "smic", "lam research",
    "applied materials", "tokyo electron", "lg energy solution",
]
MARKET_TERMS = [
    "실적", "매출", "영업이익", "수주", "계약", "공급", "점유율", "가격",
    "수요", "출하", "생산능력", "생산량", "가동률", "투자", "증설", "공장",
    "라인", "양산", "착공", "준공", "보조금", "관세", "수출규제", "정책",
    "earnings", "revenue", "order", "contract", "supply", "market share",
    "price", "demand", "shipment", "capacity", "production", "investment",
    "expansion", "factory", "fab", "plant", "mass production", "subsidy",
    "tariff", "export control", "policy",
]
HIGH_IMPACT_TERMS = [
    "공장", "fab", "plant", "양산", "mass production", "생산", "production",
    "증설", "capacity", "투자", "investment", "수주", "contract", "공급",
    "supply", "실적", "earnings", "정책", "policy", "보조금", "subsidy",
    "관세", "tariff", "수출규제", "export control",
]
PURE_RESEARCH_TERMS = [
    "연구팀", "연구진", "학술지", "논문", "실험실", "prototype study",
    "researchers", "journal", "university study",
]


def industry_score(item):
    """산업 중요도 점수. 기술 키워드 개수만으로 순위를 만들지 않는다."""
    text = f"{item['title']} {item.get('rss_description', '')}".lower()
    score = sum(5 for word in MARKET_TERMS if word in text)
    score += sum(4 for word in COMPANY_TERMS if word in text)
    score += sum(2 for word in base.TECH_TERMS if word in text)
    if re.search(r"\d[\d,.]*\s*(조|억|만|%|兆|億|gwh|mwh|nm|달러|원)", text):
        score += 6
    if item.get("category") in {"투자·양산", "정책"}:
        score += 7
    if any(word in text for word in PURE_RESEARCH_TERMS):
        score -= 15
    return score


base.relevance_score = industry_score


def select_diverse(pool, count, source_cap=2):
    picked, source_counts = [], {}
    for item in pool:
        source_group = item["source"].split()[0]
        if source_counts.get(source_group, 0) >= source_cap:
            continue
        picked.append(item)
        source_counts[source_group] = source_counts.get(source_group, 0) + 1
        if len(picked) == count:
            break
    if len(picked) < count:
        used = {x["id"] for x in picked}
        picked.extend(x for x in pool if x["id"] not in used)
    return picked[:count]


def is_useful_global(item):
    text = f"{item['title']} {item.get('rss_description', '')}".lower()
    has_company = any(word in text for word in COMPANY_TERMS)
    has_impact = any(word in text for word in HIGH_IMPACT_TERMS)
    is_pure_research = any(word in text for word in PURE_RESEARCH_TERMS)
    return has_impact and not is_pure_research and (has_company or item["score"] >= 18)


def choose_main(rows):
    result = []
    for sector in ("semiconductor", "battery"):
        pool = [x for x in rows if x["sector"] == sector]
        domestic = [x for x in pool if x["region"] == "domestic"]
        global_items = [
            x for x in pool if x["region"] == "global" and is_useful_global(x)
        ]
        result.extend(select_diverse(domestic, 4))
        result.extend(select_diverse(global_items, 2))
    return result


SEMI_RSS = "https://rss.blog.naver.com/semi_blog.xml"
SEMI_ARCHIVE_LIMIT = 2000
SEMI_SUMMARY_COUNT = 10


def raw_node_text(node, tag):
    found = node.find(tag)
    if found is None:
        found = node.find(f"{{*}}{tag}")
    return found.text or "" if found is not None else ""


def expanded_urls(value):
    decoded = html.unescape(value or "").replace("\\/", "/")
    for _ in range(3):
        newer = unquote(decoded)
        if newer == decoded:
            break
        decoded = newer
    return re.findall(r"https?://[^\s\"'<>]+", decoded)


def usable_news_url(url):
    url = url.rstrip(").,]")
    host = urlparse(url).netloc.lower()
    blocked = {
        "blog.naver.com", "m.blog.naver.com", "rss.blog.naver.com",
        "static.naver.net", "ssl.pstatic.net", "postfiles.pstatic.net",
    }
    if not host or host in blocked:
        return None
    return url


def find_article_url(raw):
    candidates = []
    for url in expanded_urls(raw):
        clean_url = usable_news_url(url)
        if clean_url:
            candidates.append(clean_url)
    # 네이버 뉴스의 언론사 원문 페이지를 가장 먼저 사용한다.
    for url in candidates:
        if urlparse(url).netloc.lower() in {"n.news.naver.com", "news.naver.com"}:
            return url
    return candidates[0] if candidates else None


def fetch_text(url):
    request = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; ProcessBrief/3.0)"},
    )
    with urlopen(request, timeout=40) as response:
        return response.read().decode("utf-8", errors="ignore")


def market_category(text):
    lower = text.lower()
    if any(x in lower for x in ["정책", "정부", "보조금", "규제", "관세"]):
        return "정책"
    if any(x in lower for x in ["공장", "라인", "양산", "증설", "투자"]):
        return "투자·양산"
    if any(x in lower for x in ["실적", "매출", "영업이익", "가격", "수요", "출하"]):
        return "시장·실적"
    return "산업동향"


def collect_semi_market():
    root = ElementTree.fromstring(fetch_text(SEMI_RSS))
    results = []
    for entry in root.findall(".//item")[:50]:
        title = base.clean(raw_node_text(entry, "title"))
        description_raw = raw_node_text(entry, "description")
        post_url = base.clean(raw_node_text(entry, "link"))
        category = base.clean(raw_node_text(entry, "category"))
        published = base.parse_date(raw_node_text(entry, "pubDate"))
        # RSS가 카테고리를 제공하면 시장동향만, 미제공 시 [공유] 글만 후보로 둔다.
        if category and "시장동향" not in category:
            continue
        if not category and "[공유]" not in title:
            continue
        if datetime.now(base.KST) - published > timedelta(days=2):
            continue
        article_url = find_article_url(description_raw)
        if not article_url and post_url:
            try:
                article_url = find_article_url(fetch_text(post_url))
            except Exception as error:
                print(f"SEMI post skipped: {type(error).__name__}")
        if not article_url:
            continue
        clean_title = re.sub(r"^\s*\[공유\]\s*", "", title).strip()
        item = {
            "id": hashlib.sha1(article_url.encode()).hexdigest()[:12],
            "sector": "semi_market",
            "region": "domestic",
            "category": market_category(clean_title),
            "title": clean_title,
            "source": "SEMI 시장동향 공유",
            "published": published.strftime("%Y-%m-%d %H:%M"),
            "collected": datetime.now(base.KST).strftime("%Y-%m-%d"),
            "link": article_url,
            "rss_description": base.clean(description_raw)[:1000],
            "discovered_via": "SEMI Korea 네이버 블로그",
            "summary_status": "link_only",
        }
        item["score"] = industry_score(item)
        if not any(term in clean_title.lower() for term in base.EXCLUDE):
            results.append(item)
    return base.deduplicate(results)


def summarize_market(items):
    top = items[:SEMI_SUMMARY_COUNT]
    verified = base.enrich(top)
    verified_ids = {x["id"] for x in verified}
    for item in verified:
        item["summary_status"] = "summarized"
        item["discovered_via"] = "SEMI Korea 네이버 블로그"
    link_only = []
    for item in items:
        if item["id"] in verified_ids:
            continue
        item.pop("rss_description", None)
        item.pop("score", None)
        link_only.append(item)
    return verified + link_only


def read_old():
    try:
        return json.loads(base.OUTPUT.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def merge_market(old_items, new_items):
    merged = {x["id"]: x for x in old_items if x.get("link")}
    for item in new_items:
        item.pop("rss_description", None)
        item.pop("score", None)
        merged[item["id"]] = item
    return sorted(
        merged.values(),
        key=lambda x: (x.get("published", ""), x.get("collected", "")),
        reverse=True,
    )[:SEMI_ARCHIVE_LIMIT]


def main():
    old_data = read_old()

    main_candidates = choose_main(base.deduplicate(base.collect()))
    verified_main = base.enrich(main_candidates)
    main_archive = base.merge_archive(verified_main)

    try:
        market_candidates = collect_semi_market()
        new_market = summarize_market(market_candidates)
    except Exception as error:
        print(f"SEMI market collection skipped: {type(error).__name__}")
        new_market = []
    market_archive = merge_market(old_data.get("market_items", []), new_market)

    payload = {
        "updated_at": datetime.now(base.KST).strftime("%Y-%m-%d %H:%M KST"),
        "today_verified_count": len(verified_main),
        "today_market_count": len(new_market),
        "items": main_archive,
        "market_items": market_archive,
    }
    base.OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    base.OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"saved {len(verified_main)} main articles; "
        f"SEMI market {len(new_market)}; "
        f"archives {len(main_archive)}/{len(market_archive)}"
    )


if __name__ == "__main__":
    main()
