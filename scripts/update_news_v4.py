"""v3 hotfix: ETNews transport and Naver SEMI link extraction."""

import html
import re
from datetime import datetime, timedelta
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import update_news_v3 as v3


# 전자신문 공식 RSS 안내 페이지에 기재된 주소는 HTTP다.
for feed in v3.base.FEEDS:
    if feed["url"].startswith("https://rss.etnews.com/"):
        feed["url"] = feed["url"].replace("https://", "http://", 1)


def decode_embedded(value):
    decoded = html.unescape(value or "").replace("\\/", "/")
    replacements = {
        "\\u003A": ":", "\\u003a": ":",
        "\\u002F": "/", "\\u002f": "/",
        "\\u003D": "=", "\\u003d": "=",
        "\\u0026": "&",
        "\\u0025": "%",
        "&quot;": '"',
    }
    for old, new in replacements.items():
        decoded = decoded.replace(old, new)
    for _ in range(5):
        newer = unquote(html.unescape(decoded))
        if newer == decoded:
            break
        decoded = newer
    return decoded


def article_urls(value):
    decoded = decode_embedded(value)
    urls = re.findall(r"https?://[^\s\"'<>\\]+", decoded)
    result = []
    for url in urls:
        url = url.rstrip(").,]}")
        host = urlparse(url).netloc.lower()
        if not host:
            continue
        if host in {
            "blog.naver.com", "m.blog.naver.com", "rss.blog.naver.com",
            "static.naver.net", "ssl.pstatic.net", "postfiles.pstatic.net",
        }:
            continue
        result.append(url)
    result.sort(
        key=lambda url: urlparse(url).netloc.lower()
        not in {"n.news.naver.com", "news.naver.com"}
    )
    return result


def mobile_post_url(post_url):
    log_no = None
    match = re.search(r"/semi_blog/(\d+)", post_url)
    if match:
        log_no = match.group(1)
    if not log_no:
        match = re.search(r"[?&]logNo=(\d+)", post_url)
        if match:
            log_no = match.group(1)
    return (
        f"https://m.blog.naver.com/semi_blog/{log_no}"
        if log_no else post_url
    )


def fetch(url):
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
                "Chrome/124.0 Mobile Safari/537.36"
            ),
            "Referer": "https://m.blog.naver.com/",
        },
    )
    with urlopen(request, timeout=45) as response:
        return response.read().decode("utf-8", errors="ignore")


def collect_semi_market_fixed():
    root = ElementTree.fromstring(fetch(v3.SEMI_RSS))
    results = []
    for entry in root.findall(".//item")[:50]:
        title = v3.base.clean(v3.raw_node_text(entry, "title"))
        description_raw = v3.raw_node_text(entry, "description")
        post_url = v3.base.clean(v3.raw_node_text(entry, "link"))
        category = v3.base.clean(v3.raw_node_text(entry, "category"))
        published = v3.base.parse_date(v3.raw_node_text(entry, "pubDate"))

        # 시장동향 카테고리 또는 시장동향에서 사용하는 [공유] 글만 허용한다.
        if "시장동향" not in category and "[공유]" not in title:
            continue
        if datetime.now(v3.base.KST) - published > timedelta(days=2):
            continue

        urls = article_urls(description_raw)
        if not urls and post_url:
            try:
                urls = article_urls(fetch(mobile_post_url(post_url)))
            except Exception as error:
                print(f"SEMI mobile post skipped: {type(error).__name__}")
        if not urls:
            print(f"SEMI link not found: {title[:45]}")
            continue

        article_url = urls[0]
        clean_title = re.sub(r"^\s*\[공유\]\s*", "", title).strip()
        item = {
            "id": v3.hashlib.sha1(article_url.encode()).hexdigest()[:12],
            "sector": "semi_market",
            "region": "domestic",
            "category": v3.market_category(clean_title),
            "title": clean_title,
            "source": "SEMI 시장동향 공유",
            "published": published.strftime("%Y-%m-%d %H:%M"),
            "collected": datetime.now(v3.base.KST).strftime("%Y-%m-%d"),
            "link": article_url,
            "rss_description": v3.base.clean(description_raw)[:1000],
            "discovered_via": "SEMI Korea 네이버 블로그",
            "summary_status": "link_only",
        }
        item["score"] = v3.industry_score(item)
        if not any(term in clean_title.lower() for term in v3.base.EXCLUDE):
            results.append(item)
    return v3.base.deduplicate(results)


v3.collect_semi_market = collect_semi_market_fixed


if __name__ == "__main__":
    v3.main()
