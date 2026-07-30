import hashlib
import html
import json
import os
import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

KST = timezone(timedelta(hours=9))
OUTPUT = Path("data/news.json")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

QUERIES = [
    ("semiconductor", "domestic", '반도체 (공정 OR 장비 OR 소재 OR 증설 OR 양산) when:2d'),
    ("battery", "domestic", '배터리 (전극 OR 공정 OR 소재 OR 증설 OR 양산) when:2d'),
    ("semiconductor", "global", '(semiconductor process OR fab OR equipment OR materials) when:2d'),
    ("battery", "global", '("battery manufacturing" OR electrode OR cathode OR anode) when:2d'),
]

EXCLUDE = [
    "주가", "목표주가", "매수", "매도", "급등", "급락", "종목", "배당",
    "stock price", "buy rating", "sell rating", "price target",
]

TECH = [
    "공정", "장비", "소재", "양산", "라인", "증설", "수율", "식각", "증착",
    "전극", "코팅", "압연", "화성", "fab", "process", "equipment", "material",
    "manufacturing", "yield", "electrode", "cathode", "anode",
]


def clean(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def category(text):
    lower = text.lower()
    if any(word in lower for word in ["정책", "정부", "법안", "보조금", "policy", "act"]):
        return "정책"
    if any(word in lower for word in ["투자", "증설", "라인", "공장", "fab", "plant", "investment"]):
        return "투자·양산"
    if any(word in lower for word in TECH):
        return "기술·공정"
    return "산업"


def score(item):
    text = f"{item['title']} {item['description']}".lower()
    tech_score = sum(1 for word in TECH if word in text)
    official = 2 if any(x in item["source"].lower() for x in [
        "samsung", "sk hynix", "lg energy", "samsung sdi", "semi", "ieee"
    ]) else 0
    return tech_score * 3 + official + (2 if item["category"] == "기술·공정" else 0)


def collect():
    rows = []
    for sector, region, query in QUERIES:
        language = "ko&gl=KR&ceid=KR:ko" if region == "domestic" else "en-US&gl=US&ceid=US:en"
        url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl={language}"
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 NewsBrief/1.0"})
        with urlopen(request, timeout=30) as response:
            root = ElementTree.fromstring(response.read())
        for entry in root.findall("./channel/item")[:30]:
            def value(tag):
                node = entry.find(tag)
                return node.text if node is not None and node.text else ""

            title = clean(value("title"))
            description = clean(value("description"))
            lower = f"{title} {description}".lower()
            if not title or any(term in lower for term in EXCLUDE):
                continue
            source = clean(value("source")) or "Google News"
            published_raw = clean(value("pubDate"))
            try:
                published = parsedate_to_datetime(published_raw).astimezone(KST).strftime("%Y-%m-%d %H:%M")
            except (TypeError, ValueError):
                published = published_raw
            item = {
                "id": hashlib.sha1(title.lower().encode()).hexdigest()[:12],
                "sector": sector,
                "region": region,
                "category": category(lower),
                "title": title,
                "source": source,
                "published": published,
                "link": value("link"),
                "description": description[:800],
            }
            item["score"] = score(item)
            rows.append(item)
    return rows


def deduplicate(rows):
    selected = []
    seen = set()
    for item in sorted(rows, key=lambda x: x["score"], reverse=True):
        tokens = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", item["title"].lower()))
        signature = frozenset(tokens)
        if any(len(signature & old) / max(1, len(signature | old)) > 0.55 for old in seen):
            continue
        seen.add(signature)
        selected.append(item)
    return selected


def choose(rows):
    result = []
    for sector in ("semiconductor", "battery"):
        pool = [x for x in rows if x["sector"] == sector]
        domestic = [x for x in pool if x["region"] == "domestic"][:3]
        global_items = [x for x in pool if x["region"] == "global"][:2]
        picked = domestic + global_items
        if len(picked) < 5:
            used = {x["id"] for x in picked}
            picked += [x for x in pool if x["id"] not in used][:5-len(picked)]
        result.extend(picked[:5])
    return result


def ai_enrich(items):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        for item in items:
            item["summary"] = item["description"] or "원문에서 상세 내용을 확인하세요."
            item["insight"] = "AI 요약이 아직 연결되지 않았습니다."
        return

    payload = [{
        "id": x["id"], "sector": x["sector"], "title": x["title"],
        "source": x["source"], "description": x["description"]
    } for x in items]
    prompt = f"""
다음은 반도체·배터리 뉴스 후보다. 기사별로 제공된 제목과 설명 안에서만 판단하라.
확인되지 않은 숫자나 사실을 만들지 마라. description이 부족하면 그 한계를 명시하라.
해외 기사는 자연스러운 한국어로 작성한다.

각 id에 대해 JSON 배열로 반환:
- id
- summary: 핵심 사실 2~3문장, 180자 이내
- insight: 공정기술 지원자가 알아둘 의미 1문장, 100자 이내. 근거가 부족하면 "공정기술 영향은 원문 확인 필요"

입력:
{json.dumps(payload, ensure_ascii=False)}
"""
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{MODEL}:generateContent?{urlencode({'key': api_key})}"
    )
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=90) as response:
        raw = json.loads(response.read().decode("utf-8"))
    response_text = raw["candidates"][0]["content"]["parts"][0]["text"]
    enriched = {x["id"]: x for x in json.loads(response_text)}
    for item in items:
        result = enriched.get(item["id"], {})
        item["summary"] = result.get("summary", item["description"] or "원문 확인 필요")
        item["insight"] = result.get("insight", "공정기술 영향은 원문 확인 필요")


def main():
    items = choose(deduplicate(collect()))
    ai_enrich(items)
    for item in items:
        item.pop("description", None)
        item.pop("score", None)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "items": items,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {len(items)} items")


if __name__ == "__main__":
    main()
