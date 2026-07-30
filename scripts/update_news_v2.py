import hashlib
import html
import json
import os
import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

KST = timezone(timedelta(hours=9))
OUTPUT = Path("data/news.json")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
MAX_PER_SECTOR = 5
MAX_ARCHIVE_ITEMS = 300

# 기사 원문으로 직접 연결되는 RSS만 사용한다.
FEEDS = [
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
        "url": "https://spectrum.ieee.org/feeds/topic/semiconductors.rss",
        "source": "IEEE Spectrum",
        "region": "global",
        "fixed_sector": "semiconductor",
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

SEMICONDUCTOR_TERMS = [
    "반도체", "웨이퍼", "파운드리", "낸드", "dram", "hbm", "fab", "semiconductor",
    "wafer", "foundry", "lithography", "photoresist", "etch", "deposition", "cmp",
    "euv", "duv", "chip manufacturing", "process node",
]
BATTERY_TERMS = [
    "배터리", "이차전지", "양극", "음극", "전극", "전해질", "분리막", "lithium-ion",
    "battery", "cathode", "anode", "electrolyte", "electrode", "cell manufacturing",
    "solid-state", "lithium metal", "lithium iron phosphate", "lfp",
]
EXCLUDE = [
    "주가", "목표주가", "매수", "매도", "급등", "급락", "종목", "배당",
    "stock price", "buy rating", "sell rating", "price target",
]
TECH_TERMS = [
    "공정", "장비", "소재", "양산", "라인", "증설", "수율", "식각", "증착",
    "전극", "코팅", "압연", "화성", "fab", "process", "equipment", "material",
    "manufacturing", "yield", "electrode", "cathode", "anode",
]


def clean(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def node_text(node, tag):
    found = node.find(tag)
    if found is None:
        found = node.find(f"{{*}}{tag}")
    return found.text.strip() if found is not None and found.text else ""


def parse_date(value):
    try:
        return parsedate_to_datetime(value).astimezone(KST)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(KST)
        except (TypeError, ValueError):
            return datetime.now(KST)


def detect_sector(text, fixed=None):
    if fixed:
        return fixed
    lower = text.lower()
    semi = sum(term in lower for term in SEMICONDUCTOR_TERMS)
    battery = sum(term in lower for term in BATTERY_TERMS)
    if semi == battery == 0:
        return None
    return "semiconductor" if semi > battery else "battery"


def detect_category(text):
    lower = text.lower()
    if any(word in lower for word in ["정책", "정부", "법안", "보조금", "policy", "regulation"]):
        return "정책"
    if any(word in lower for word in ["투자", "증설", "라인", "공장", "fab", "plant", "investment"]):
        return "투자·양산"
    if any(word in lower for word in TECH_TERMS):
        return "기술·공정"
    return "산업"


def relevance_score(item):
    text = f"{item['title']} {item['rss_description']}".lower()
    score = sum(3 for word in TECH_TERMS if word in text)
    score += sum(2 for word in SEMICONDUCTOR_TERMS + BATTERY_TERMS if word in text)
    if item["category"] == "기술·공정":
        score += 5
    return score


def read_feed(config):
    request = Request(config["url"], headers={"User-Agent": "Mozilla/5.0 ProcessBrief/2.0"})
    with urlopen(request, timeout=40) as response:
        root = ElementTree.fromstring(response.read())
    entries = root.findall(".//item")
    results = []
    for entry in entries[:40]:
        title = clean(node_text(entry, "title"))
        description = clean(node_text(entry, "description"))
        link = clean(node_text(entry, "link"))
        published_raw = clean(node_text(entry, "pubDate") or node_text(entry, "published"))
        combined = f"{title} {description}"
        lower = combined.lower()
        sector = detect_sector(combined, config["fixed_sector"])
        if not title or not link or not sector or any(term in lower for term in EXCLUDE):
            continue
        published_dt = parse_date(published_raw)
        if datetime.now(KST) - published_dt > timedelta(days=4):
            continue
        item = {
            "id": hashlib.sha1(link.encode()).hexdigest()[:12],
            "sector": sector,
            "region": config["region"],
            "category": detect_category(combined),
            "title": title,
            "source": config["source"],
            "published": published_dt.strftime("%Y-%m-%d %H:%M"),
            "collected": datetime.now(KST).strftime("%Y-%m-%d"),
            "link": link,
            "rss_description": description[:1000],
        }
        item["score"] = relevance_score(item)
        results.append(item)
    return results


def collect():
    rows = []
    for feed in FEEDS:
        try:
            rows.extend(read_feed(feed))
        except Exception as error:
            print(f"feed skipped: {feed['source']} ({type(error).__name__})")
    return rows


def deduplicate(rows):
    selected, signatures, links = [], [], set()
    for item in sorted(rows, key=lambda x: (x["score"], x["published"]), reverse=True):
        if item["link"] in links:
            continue
        tokens = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", item["title"].lower()))
        if any(len(tokens & old) / max(1, len(tokens | old)) > 0.58 for old in signatures):
            continue
        links.add(item["link"])
        signatures.append(tokens)
        selected.append(item)
    return selected


def choose(rows):
    result = []
    for sector in ("semiconductor", "battery"):
        pool = [x for x in rows if x["sector"] == sector]
        domestic = [x for x in pool if x["region"] == "domestic"][:3]
        global_items = [x for x in pool if x["region"] == "global"][:2]
        picked = domestic + global_items
        if len(picked) < MAX_PER_SECTOR:
            used = {x["id"] for x in picked}
            picked += [x for x in pool if x["id"] not in used][:MAX_PER_SECTOR-len(picked)]
        result.extend(picked[:MAX_PER_SECTOR])
    return result


def parse_model_json(text):
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    return json.loads(stripped)


def enrich_one(item, api_key):
    prompt = f"""
아래 URL의 기사 원문만 읽고 한국어로 정리하라.
URL: {item['link']}

절대 규칙:
1. 기사 원문에 명시된 사실만 사용한다.
2. 외부 지식, 다른 기사, 추측, 전망을 추가하지 않는다.
3. 숫자·날짜·기업명·기술명은 원문과 일치할 때만 쓴다.
4. 원문을 충분히 읽지 못했으면 accessible을 false로 설정한다.
5. 해외 기사는 의미를 바꾸지 않고 한국어로 번역한다.

JSON 객체 하나만 반환:
{{
  "accessible": true 또는 false,
  "overview": "기사 전체를 설명하는 3~5문장 요약",
  "key_points": ["핵심 사실 1", "핵심 사실 2", "핵심 사실 3"],
  "numbers": ["원문에 나온 핵심 수치·날짜"] 또는 [],
  "keywords": ["핵심 키워드 3~6개"],
  "stated_outlook": "기사에서 직접 언급한 영향·계획·전망. 없으면 빈 문자열"
}}
"""
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{MODEL}:generateContent?{urlencode({'key': api_key})}"
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
    with urlopen(request, timeout=120) as response:
        raw = json.loads(response.read().decode("utf-8"))
    candidate = raw["candidates"][0]
    metadata = candidate.get("urlContextMetadata") or candidate.get("url_context_metadata") or {}
    url_metadata = metadata.get("urlMetadata") or metadata.get("url_metadata") or []
    retrieved = any(
        (entry.get("urlRetrievalStatus") or entry.get("url_retrieval_status"))
        == "URL_RETRIEVAL_STATUS_SUCCESS"
        for entry in url_metadata
    )
    text = candidate["content"]["parts"][0]["text"]
    result = parse_model_json(text)
    if not retrieved or not result.get("accessible"):
        return None
    item.update({
        "verified_source": True,
        "overview": clean(result.get("overview", "")),
        "key_points": [clean(x) for x in result.get("key_points", []) if clean(x)][:5],
        "numbers": [clean(x) for x in result.get("numbers", []) if clean(x)][:6],
        "keywords": [clean(x) for x in result.get("keywords", []) if clean(x)][:6],
        "stated_outlook": clean(result.get("stated_outlook", "")),
    })
    if not item["overview"] or len(item["key_points"]) < 2:
        return None
    return item


def enrich(items):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for verified article summaries")
    verified = []
    for item in items:
        try:
            result = enrich_one(item, api_key)
            if result:
                verified.append(result)
            else:
                print(f"article excluded: original unavailable ({item['source']})")
        except Exception as error:
            print(f"article excluded: {item['source']} ({type(error).__name__})")
    return verified


def merge_archive(new_items):
    try:
        old_items = json.loads(OUTPUT.read_text(encoding="utf-8")).get("items", [])
    except (FileNotFoundError, json.JSONDecodeError):
        old_items = []
    # 기존의 짧은 RSS 기반 요약은 버리고, 원문 검증을 통과한 기사만 보존한다.
    verified_old = [x for x in old_items if x.get("verified_source") is True]
    merged = {x["id"]: x for x in verified_old}
    for item in new_items:
        item.pop("rss_description", None)
        item.pop("score", None)
        merged[item["id"]] = item
    return sorted(
        merged.values(),
        key=lambda x: (x.get("published", ""), x.get("collected", "")),
        reverse=True,
    )[:MAX_ARCHIVE_ITEMS]


def main():
    candidates = choose(deduplicate(collect()))
    verified = enrich(candidates)
    archive = merge_archive(verified)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "today_verified_count": len(verified),
        "items": archive,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {len(verified)} verified articles; archive {len(archive)}")


if __name__ == "__main__":
    main()
