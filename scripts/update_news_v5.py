"""v4 hotfix: support namespaced Naver Blog RSS items and log each stage."""

import re
from datetime import datetime, timedelta
from xml.etree import ElementTree

import update_news_v4 as v4


def find_entries(root):
    entries = root.findall(".//item")
    if not entries:
        entries = root.findall(".//{*}item")
    if not entries and root.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}:
        entries = [root]
    if not entries:
        entries = [
            node for node in root.iter()
            if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}
        ]
    return entries


def any_node_text(node, *names):
    wanted = {name.lower() for name in names}
    for child in node.iter():
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local in wanted and child.text:
            return child.text
    return ""


def collect_semi_market_v5():
    raw = v4.fetch(v4.v3.SEMI_RSS)
    root = ElementTree.fromstring(raw)
    entries = find_entries(root)
    print(f"SEMI RSS entries: {len(entries)}")

    recent, shared, linked, results = 0, 0, 0, []
    for entry in entries[:100]:
        title = v4.v3.base.clean(any_node_text(entry, "title"))
        description_raw = any_node_text(
            entry, "description", "encoded", "content", "summary"
        )
        post_url = v4.v3.base.clean(any_node_text(entry, "link"))
        category = v4.v3.base.clean(
            any_node_text(entry, "category", "subject")
        )
        published = v4.v3.base.parse_date(
            any_node_text(entry, "pubDate", "published", "updated", "date")
        )

        if datetime.now(v4.v3.base.KST) - published > timedelta(days=3):
            continue
        recent += 1

        # SEMI 시장동향 게시물은 제목에 [공유]를 사용한다.
        # RSS가 카테고리를 제공하면 '시장동향'도 함께 인정한다.
        if "[공유]" not in title and "시장동향" not in category:
            continue
        shared += 1

        urls = v4.article_urls(description_raw)
        if not urls and post_url:
            try:
                urls = v4.article_urls(v4.fetch(v4.mobile_post_url(post_url)))
            except Exception as error:
                print(f"SEMI mobile post skipped: {type(error).__name__}")
        if not urls:
            print(f"SEMI link not found: {title[:55]}")
            continue
        linked += 1

        article_url = urls[0]
        clean_title = re.sub(r"^\s*\[공유\]\s*", "", title).strip()
        item = {
            "id": v4.v3.hashlib.sha1(article_url.encode()).hexdigest()[:12],
            "sector": "semi_market",
            "region": "domestic",
            "category": v4.v3.market_category(clean_title),
            "title": clean_title,
            "source": "SEMI 시장동향 공유",
            "published": published.strftime("%Y-%m-%d %H:%M"),
            "collected": datetime.now(v4.v3.base.KST).strftime("%Y-%m-%d"),
            "link": article_url,
            "rss_description": v4.v3.base.clean(description_raw)[:1000],
            "discovered_via": "SEMI Korea 네이버 블로그",
            "summary_status": "link_only",
        }
        item["score"] = v4.v3.industry_score(item)
        if not any(
            term in clean_title.lower() for term in v4.v3.base.EXCLUDE
        ):
            results.append(item)

    print(
        f"SEMI stages: recent {recent}, shared {shared}, "
        f"linked {linked}, eligible {len(results)}"
    )
    return v4.v3.base.deduplicate(results)


v4.v3.collect_semi_market = collect_semi_market_v5


def choose_main_with_lges_priority(rows):
    """국내 LG에너지솔루션 기사가 있으면 배터리 국내 4개 안에 우선 포함."""
    result = []
    for sector in ("semiconductor", "battery"):
        pool = [x for x in rows if x["sector"] == sector]
        domestic = [x for x in pool if x["region"] == "domestic"]
        global_items = [
            x for x in pool
            if x["region"] == "global" and v4.v3.is_useful_global(x)
        ]

        if sector == "battery":
            lges = [
                x for x in domestic
                if any(
                    term in f"{x['title']} {x.get('rss_description', '')}".lower()
                    for term in [
                        "lg에너지솔루션", "엘지에너지솔루션",
                        "lg energy solution", "lg엔솔", "엔솔",
                    ]
                )
            ]
            domestic_picks = lges[:1]
            used = {x["id"] for x in domestic_picks}
            remaining = [x for x in domestic if x["id"] not in used]
            domestic_picks.extend(
                v4.v3.select_diverse(remaining, 4 - len(domestic_picks))
            )
        else:
            domestic_picks = v4.v3.select_diverse(domestic, 4)

        result.extend(domestic_picks[:4])
        result.extend(v4.v3.select_diverse(global_items, 2))
    return result


v4.v3.choose_main = choose_main_with_lges_priority


if __name__ == "__main__":
    v4.v3.main()
