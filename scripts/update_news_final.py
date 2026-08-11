"""Process Brief final manual-first edition.

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
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlparse, urlsplit, urlunsplit
from urllib.request import Request
from xml.etree import ElementTree

import update_news_v6 as v6


v3 = v6.v3
base = v6.base
TARGETS = {"semiconductor": 5, "battery": 5}
DOMESTIC_TARGETS = {"semiconductor": 4, "battery": 4}
# 하루 1회 실행으로도 목표치를 채울 수 있도록 넉넉하게 잡는다.
# (원문 접근 실패로 시도 예산이 낭비되는 문제를 감안한 여유값)
MAX_ATTEMPTS = {"semiconductor": 24, "battery": 28}
# 같은 사안이 다른 매체·다른 날짜로 재등장하는 것을 막기 위해, 최근 며칠간
# 이미 다룬 기사와도 비교한다.
CROSS_DAY_DEDUP_DAYS = 2
ARCHIVE_LIMIT = 3000
SEMI_ARCHIVE_LIMIT = 1000
MANUAL_LIMIT = 10000
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
        # 디일렉은 배터리 섹션 RSS(S1N9)를 공개하지 않으므로 전체 기사
        # 공식 RSS에서 분야를 판별한다. 이 피드에는 배터리 기사 본문 링크가
        # 안정적으로 포함된다. 이 피드 변경은 자동 실행에서 함께 검증한다.
        "url": "https://www.thelec.kr/rss/allArticle.xml",
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
    "디일렉 배터리": "산업 전문언론",
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
    "디지털데일리": ("디지털데일리", "산업 전문언론"),
    "IT조선": ("IT조선", "산업 전문언론"),
    "블로터": ("블로터", "산업 전문언론"),
    "한국경제": ("한국경제", "경제지 보도"),
    "서울경제": ("서울경제", "경제지 보도"),
    "매일경제": ("매일경제", "경제지 보도"),
    "조선비즈": ("조선비즈", "경제지 보도"),
    "이데일리": ("이데일리", "경제지 보도"),
    "헤럴드경제": ("헤럴드경제", "경제지 보도"),
    "아시아경제": ("아시아경제", "경제지 보도"),
    "파이낸셜뉴스": ("파이낸셜뉴스", "경제지 보도"),
    "뉴스핌": ("뉴스핌", "통신사 보도"),
}

GOOGLE_NEWS_QUERIES = {
    "semiconductor": [
        "(반도체 OR HBM OR 파운드리 OR D램 OR 낸드) when:1d",
        "(삼성전자 OR SK하이닉스 OR TSMC) 반도체 when:1d",
        "반도체 (공급망 OR 장비 OR 소재 OR 투자 OR 공장) when:1d",
        "반도체 (신기술 OR 신공정 OR 차세대 OR 개발 성공 OR 세계 최초) when:1d",
        "반도체 (증착 OR ALD OR CVD OR PVD OR 박막 OR 원자층증착) when:1d",
    ],
    "battery": [
        "(배터리 OR 이차전지 OR 양극재 OR 전고체 OR ESS) when:1d",
        "(LG에너지솔루션 OR 삼성SDI OR SK온 OR CATL) when:1d",
        "배터리 (신기술 OR 신공정 OR 차세대 OR 개발 성공 OR 세계 최초) when:1d",
    ],
}

SOURCE_RANK = {
    # 정부·협회 자료가 기술·공정 기사보다 먼저 도배되지 않도록 낮게 둔다.
    "정부·협회": 1,
    "기업 공식 발표": 4,
    "시장조사기관": 4,
    "통신사 보도": 3,
    "산업 전문언론": 5,
    "경제지 보도": 2,
    "해외 산업 보도": 1,
}

SEMICON_LOW_VALUE_TERMS = (
    "목표주가", "투자의견", "비중확대", "주주환원", "배당", "자사주",
    "증권가", "증권사", "애널리스트", "리포트", "매수 의견", "매도 의견",
    "주가 전망", "상승 여력", "실적 기대감", "저평가", "고평가",
    "특징주", "주가", "주식", "테마주", "급등주", "상한가", "하한가",
    "상승세에", "하락세에", "장중 급등", "장중 강세", "장중 약세",
    "매각설", "인수설", "확정된 바 없다", "사실무근",
)

SEMICON_CORE_TECH_TERMS = (
    "공정", "수율", "결함", "검사", "계측", "장비", "소재", "웨이퍼",
    "노광", "euv", "식각", "etch", "증착", "deposition", "ald", "cvd", "pvd",
    "박막", "포토레지스트", "패키징", "칩렛", "하이브리드 본딩",
    "hbm", "d램", "dram", "낸드", "파운드리", "공정 노드", "나노 공정",
)

SEMICON_PROCESS_TERMS = (
    "수율", "결함", "검사", "계측", "노광", "euv", "식각", "etch",
    "증착", "deposition", "ald", "cvd", "pvd", "박막", "포토레지스트",
    "패키징", "칩렛", "하이브리드 본딩",
)


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
    try:
        return datetime.strptime(
            item.get("published", "")[:16], "%Y-%m-%d %H:%M"
        ).replace(tzinfo=base.KST)
    except (TypeError, ValueError):
        return None


def within_recent_hours(item, now, hours=24):
    """오전 9시 발행 기준, 직전 24시간 안에 나온 기사만 오늘 후보로 인정한다."""
    date_only = published_datetime(item) or (
        datetime.combine(published_date(item), datetime.min.time(), tzinfo=base.KST)
        if published_date(item) else None
    )
    if date_only is None:
        return False
    return timedelta(0) <= now - date_only <= timedelta(hours=hours)


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
    if item.get("sector") == "semiconductor" and any(
        term in text for term in SEMICON_LOW_VALUE_TERMS
    ):
        return False
    if any(term in text for term in (
        "목표가", "투자의견", "증권", "컨센서스", "주식", "etf",
        "관련주", "수혜주", "테마주", "급등주", "52주", "상한가", "하한가",
        "코스피", "코스닥", "외국인 순매수", "기관 순매수",
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
        1 if item.get("manual_added") else 0,
        # 핵심도(score)를 최우선 기준으로 삼는다. 후보는 이미 최근 3일
        # 이내로 제한돼 있으므로, "오늘 기사인지"보다 "얼마나 핵심적인
        # 내용인지"가 먼저 반영돼야 한다.
        item.get("score", 0),
        SOURCE_RANK.get(source_type, 0),
        date.isoformat() if date else "",
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


TECH_TREND_TERMS = (
    "신기술", "신공정", "차세대", "세계 최초", "국내 최초", "개발 성공",
    "기술 개발", "기술 혁신", "공정 전환", "공정 혁신", "양산 돌입",
    "양산 준비", "시제품", "파일럿 라인", "실증", "로드맵", "특허",
    "breakthrough", "next-generation", "novel process", "first-ever",
)

# 증착 공정·장비·소재는 반도체 분야에서 특별히 더 우선 노출한다.
DEPOSITION_TERMS = (
    "증착", "원자층증착", "화학기상증착", "물리기상증착", "박막",
    "atomic layer deposition", "chemical vapor deposition",
    "physical vapor deposition", "thin film", "ald", "cvd", "pvd",
    "deposition equipment", "deposition tool",
)

# 배터리는 산업 전반 동향이 메인이고, 그중에서도 전극 공정은 더 우선한다.
ELECTRODE_TERMS = (
    "전극", "양극", "음극", "양극재", "음극재", "전극공정", "극판",
    "슬러리", "코팅", "압연", "건조", "믹싱", "합제",
    "electrode", "cathode", "anode", "slurry", "coating", "calendering",
    "electrode process",
)


def apply_tech_trend_bonus(rows):
    for item in rows:
        text = f"{item.get('title', '')} {item.get('rss_description', '')}".lower()
        if any(term.lower() in text for term in TECH_TREND_TERMS):
            item["score"] = item.get("score", 0) + 9
        if item.get("sector") == "semiconductor" and any(
            term.lower() in text for term in DEPOSITION_TERMS
        ):
            item["score"] = item.get("score", 0) + 12
            item["category"] = "기술·공정"
        if item.get("sector") == "semiconductor":
            core_hits = sum(term in text for term in SEMICON_CORE_TECH_TERMS)
            if core_hits:
                item["score"] = item.get("score", 0) + min(core_hits, 4) * 4
            if any(term in text for term in SEMICON_PROCESS_TERMS):
                item["category"] = "기술·공정"
            if item.get("category") == "기술·공정":
                item["score"] = item.get("score", 0) + 10
            elif item.get("category") == "투자·양산":
                item["score"] = item.get("score", 0) - 5
            elif item.get("category") == "정책":
                item["score"] = item.get("score", 0) - 10
            if item.get("source_type") == "경제지 보도":
                item["score"] = item.get("score", 0) - 3
        if item.get("sector") == "battery" and any(
            term.lower() in text for term in ELECTRODE_TERMS
        ):
            item["score"] = item.get("score", 0) + 12
            item["category"] = "기술·공정"
        if item.get("source", "").startswith("디일렉"):
            # 디일렉은 기사 본문을 안정적으로 읽을 수 있어 실제 자동요약까지
            # 완성될 가능성이 높다. 제목·링크만 남는 매체보다 먼저 검증한다.
            item["score"] = item.get("score", 0) + 14
    return rows


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
    return apply_tech_trend_bonus(rows)


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
        [item for item in rows if item.get("region") == "domestic"],
        source_cap=3 if sector == "battery" else 2,
    )
    global_items = source_diverse(
        [item for item in rows if item.get("region") == "global"]
    )

    domestic_target = DOMESTIC_TARGETS[sector]
    if sector == "semiconductor":
        # 기술·공정 3건을 우선 확보하고 투자·정책 기사는 합계 2건까지만
        # 앞 순서에 둔다. 후보가 부족하면 아래 reserves에서 자동 보충된다.
        tech = [x for x in domestic if x.get("category") == "기술·공정"]
        industry = [x for x in domestic if x.get("category") == "산업"]
        investment = [x for x in domestic if x.get("category") == "투자·양산"]
        policy = [x for x in domestic if x.get("category") == "정책"]
        preferred_domestic = (tech[:3] + industry[:1] + investment[:2] + policy[:1])
        preferred_domestic = preferred_domestic[:domestic_target]
        preferred = preferred_domestic + global_items[:1]
    else:
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
    left_text = left.get("title", "").lower()
    right_text = right.get("title", "").lower()
    left_tokens = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", left_text))
    right_tokens = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", right_text))
    overlap = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    if overlap >= 0.35:
        return True
    markers = (
        "삼성전자", "sk하이닉스", "tsmc", "마이크론", "엘앤에프",
        "lg에너지솔루션", "삼성sdi", "sk온", "에코프로", "포스코퓨처엠",
        "catl", "hbm", "d램", "낸드", "파운드리", "lfp", "ess",
        "양극재", "음극재", "전고체", "출하", "수주", "증설", "양산",
    )
    left_markers = {word for word in markers if word in left_text}
    right_markers = {word for word in markers if word in right_text}
    return len(left_markers & right_markers) >= 3


def make_link_only_fallback(candidate):
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
    return fallback


def select_sector(
    rows, sector, existing_items, api_key, avoid_recent=(),
    current=None, target=None, attempts_budget=None,
):
    target = TARGETS[sector] if target is None else target
    attempts_budget = MAX_ATTEMPTS[sector] if attempts_budget is None else attempts_budget
    by_id, by_url = existing_lookup(existing_items)
    current = list(current) if current else []
    newly_verified, pending_fallback = [], []
    attempts = reused = 0

    def already_covered(candidate):
        if candidate.get("manual_added"):
            return False
        return any(same_story(candidate, selected) for selected in current) or any(
            same_story(candidate, recent) for recent in avoid_recent
        )

    manual_rows = sorted(
        [item for item in rows if item.get("manual_added")],
        key=priority_key,
        reverse=True,
    )
    manual_ids = {item.get("id") for item in manual_rows}
    candidates = manual_rows + [
        item for item in ordered_sector_candidates(rows, sector)
        if item.get("id") not in manual_ids
    ]

    # 1차: 실제 원문 요약이 가능한 후보를 최대한 채운다. 통신사 헤드라인이
    # 검증에 실패해도 즉시 link_only로 채우지 않고, 남은 후보를 계속
    # 시도한 뒤 예산이 다 떨어졌을 때만 보류 목록에서 채워 넣는다.
    for candidate in candidates:
        if len(current) >= target:
            break
        if already_covered(candidate):
            continue
        old = by_id.get(candidate.get("id")) or by_url.get(
            canonical_url(candidate.get("link"))
        )
        if old:
            current.append(old)
            reused += 1
            continue
        if attempts >= attempts_budget:
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
        elif candidate.get("trusted_headline"):
            pending_fallback.append(candidate)
        else:
            print(
                f"{sector} unreadable: {candidate.get('source')}; "
                "trying reserve"
            )

    # 2차: 그래도 목표치에 못 미치면, 보류해뒀던 link_only 후보로 채운다.
    for candidate in pending_fallback:
        if len(current) >= target:
            break
        if already_covered(candidate):
            continue
        fallback = make_link_only_fallback(candidate)
        current.append(fallback)
        newly_verified.append(fallback)

    print(
        f"{sector}: eligible {len(rows)}, reused {reused}, "
        f"verification attempts {attempts}, link_only held back "
        f"{len(pending_fallback)}, new {len(newly_verified)}, "
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
    ordered = sorted(
        merged.values(),
        key=lambda item: (item.get("collected", ""), item.get("published", "")),
        reverse=True,
    )
    # URL이 다른 같은 사안(예: 같은 기사를 다른 매체 링크로 두 번 추가)도
    # 걸러낸다. 같은 분야끼리만 비교하고, 먼저(최신순) 들어온 쪽을 남긴다.
    deduped, kept_by_sector = [], {}
    for item in ordered:
        sector = item.get("sector")
        bucket = kept_by_sector.setdefault(sector, [])
        if any(same_story(item, kept) for kept in bucket):
            continue
        bucket.append(item)
        deduped.append(item)
    return deduped[:MANUAL_LIMIT]


def without_deleted(items, delete_id):
    if not delete_id:
        return list(items)
    return [item for item in items if str(item.get("id", "")) != delete_id]


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


def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required")

    old = v3.read_old()
    delete_id = os.getenv("DELETE_ARTICLE_ID", "").strip()
    edit_id = os.getenv("EDIT_ARTICLE_ID", "").strip()
    edit_title = os.getenv("EDIT_ARTICLE_TITLE", "").strip()
    legacy = old.get("items", [])
    old_current = old.get("current_items", [])
    old_archive = old.get("archive_items", [])
    legacy = without_deleted(legacy, delete_id)
    old_current = without_deleted(old_current, delete_id)
    old_archive = without_deleted(old_archive, delete_id)
    all_existing = [tag_source(item) for item in old_current + old_archive + legacy]
    old_semi_current = old.get("semi_items", [])
    old_semi_archive = old.get("semi_archive_items", [])
    legacy_market = old.get("market_items", [])
    old_semi_current = without_deleted(old_semi_current, delete_id)
    old_semi_archive = without_deleted(old_semi_archive, delete_id)
    legacy_market = without_deleted(legacy_market, delete_id)
    all_existing_semi = old_semi_current + old_semi_archive + legacy_market
    old_manual_items = old.get("manual_items", [])
    old_manual_items = without_deleted(old_manual_items, delete_id)
    old_manual_items = with_edited_title(old_manual_items, edit_id, edit_title)
    if delete_id:
        print(f"deleted stored article id: {delete_id}")
    if edit_id and edit_title:
        print(f"edited manual article title: {edit_id}")

    manual_url = os.getenv("MANUAL_ARTICLE_URL", "").strip()
    manual_sector = os.getenv("MANUAL_ARTICLE_SECTOR", "").strip()
    if manual_url:
        # 수동 추가는 자동 뉴스 전체 수집과 완전히 분리한다. 예전에는 기사 한
        # 건을 넣을 때도 모든 피드와 Gemini 검증을 다시 실행해, 제한시간이나
        # 외부 사이트 오류가 나면 수동 기사까지 저장되지 않았다.
        requested_urls = list(dict.fromkeys(
            value.strip() for value in manual_url.splitlines() if value.strip()
        ))
        new_manual_items = [
            summarize_manual(build_manual_candidate(url, manual_sector), api_key)
            for url in requested_urls
        ]
        for item in new_manual_items:
            item["source_type"] = "직접 선택"
        manual_items = merge_manual_items(old_manual_items, new_manual_items)
        payload = dict(old)
        payload["manual_items"] = manual_items
        payload["manual_updated_at"] = datetime.now(base.KST).strftime(
            "%Y-%m-%d %H:%M KST"
        )
        base.OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        base.OUTPUT.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"manual-only update saved: {manual_sector} · {len(new_manual_items)}")
        return

    now = datetime.now(base.KST)
    today = now.date()
    cycle_started = old.get("automatic_cycle_started_at", "")
    try:
        cycle_started_at = datetime.strptime(
            cycle_started[:16], "%Y-%m-%d %H:%M"
        ).replace(tzinfo=base.KST)
    except (TypeError, ValueError):
        cycle_started_at = None
    cycle_open = (
        cycle_started_at is not None
        and timedelta(0) <= now - cycle_started_at < timedelta(hours=24)
    )
    fetched = collect_all_candidates()
    # Keep already verified articles inside the three-day window as reusable
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
        if within_recent_hours(item, now, hours=24)
        and (item.get("manual_added") is True or useful_candidate(item))
    ]
    # 24시간 안에서는 부족한데 정말 그 분야 기사가 드문 날을 위한 보충 풀.
    # 평소에는 안 쓰이고, 24시간 후보만으로 목표치를 못 채울 때만 사용된다.
    backup_eligible = [
        item for item in collected
        if not within_recent_hours(item, now, hours=24)
        and within_recent_hours(item, now, hours=48)
        and (item.get("manual_added") is True or useful_candidate(item))
    ]
    print(
        f"publication window: last 24h from {now.strftime('%Y-%m-%d %H:%M')} KST; "
        f"collected {len(collected)}, eligible {len(eligible)}, "
        f"backup(24-48h) {len(backup_eligible)}"
    )
    print(
        "eligible by sector: "
        f"semiconductor {sum(x.get('sector') == 'semiconductor' for x in eligible)}, "
        f"battery {sum(x.get('sector') == 'battery' for x in eligible)}"
    )

    # 같은 사안이 다른 매체·다른 날짜로 며칠에 걸쳐 재등장하는 것을 막기
    # 위해, 최근 며칠간 이미 노출됐던(요약 완료 또는 link_only) 기사를
    # cross-day 중복 비교 대상으로 삼는다.
    recent_cutoff = today - timedelta(days=CROSS_DAY_DEDUP_DAYS)
    avoid_recent = [
        item for item in all_existing
        if item.get("published", "")[:10] >= recent_cutoff.isoformat()
    ]

    current, new_items = [], []
    for sector in ("semiconductor", "battery"):
        sector_rows = [item for item in eligible if item.get("sector") == sector]
        sector_avoid = [item for item in avoid_recent if item.get("sector") == sector]
        # 같은 24시간 묶음 안의 재실행은 기존 기사를 고정하고 빈자리만
        # 보충한다. 이전에는 기존 묶음을 중복 기사로 제외한 뒤 새로 뽑아
        # 재실행 직후 5건이 0~2건으로 줄어들 수 있었다.
        preserved = [
            item for item in old_current
            if cycle_open
            and item.get("sector") == sector
            and (item.get("verified_source") is True or item.get("summary_status") == "link_only")
            and useful_candidate(item)
        ][:TARGETS[sector]]
        selected, fresh = select_sector(
            sector_rows, sector, all_existing + new_items, api_key,
            avoid_recent=sector_avoid, current=preserved,
        )
        if len(selected) < TARGETS[sector]:
            selected_ids = {item.get("id") for item in selected}
            backup_rows = [
                item for item in backup_eligible
                if item.get("sector") == sector and item.get("id") not in selected_ids
            ]
            print(
                f"{sector}: only {len(selected)}/{TARGETS[sector]} within 24h; "
                f"backfilling from 24-48h pool ({len(backup_rows)} candidates)"
            )
            selected, backfill_fresh = select_sector(
                backup_rows, sector, all_existing + new_items + fresh, api_key,
                avoid_recent=sector_avoid, current=selected,
                target=TARGETS[sector], attempts_budget=8,
            )
            fresh = fresh + backfill_fresh
        current.extend(selected)
        new_items.extend(fresh)

    manual_items = merge_manual_items(old_manual_items, [])

    archive = merge_archive(all_existing, new_items, current)

    # Naver's RSS and live board endpoints are not stable from GitHub Actions.
    # Keep old SEMI records in the automatic archive, but stop automatic SEMI
    # collection. New SEMI articles are added through the manual form.
    semi_current = []
    new_semi = []
    semi_archive = merge_semi_archive(all_existing_semi, [], [])

    completed_at = datetime.now(base.KST).strftime("%Y-%m-%d %H:%M KST")
    # 자동 뉴스 갱신 시 수동 아카이브·즐겨찾기 등 사용자 저장 영역을
    # 새 객체로 덮어쓰지 않는다. 자동 생성 필드만 교체한다.
    payload = dict(old)
    payload.update({
        "updated_at": completed_at,
        "last_automatic_update_at": completed_at,
        "automatic_cycle_started_at": now.strftime("%Y-%m-%d %H:%M KST"),
        "publication_window": {
            "from": (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M KST"),
            "to": now.strftime("%Y-%m-%d %H:%M KST"),
        },
        "today_verified_count": len(new_items),
        "current_items": current,
        "archive_items": archive,
        "semi_items": semi_current,
        "semi_archive_items": semi_archive,
        "manual_items": manual_items,
    })
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
