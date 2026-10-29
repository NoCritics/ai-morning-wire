"""Story discovery: AlphaSignal's RSS feed and the citation list on each article page."""
import json
import re
import xml.etree.ElementTree as ElementTree
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime

from ainews import urls

FEED_URL = "https://alphasignal.ai/feed.xml"
LD_JSON_BLOCK = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.DOTALL)


def parse_publish_time(text):
    try:
        published = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return published


def parse_feed(feed_xml):
    """Stories in feed order: url, title, published (aware datetime, or None when unparseable)."""
    stories = []
    for item_element in ElementTree.fromstring(feed_xml).iter("item"):
        url = (item_element.findtext("link") or "").strip()
        if url == "":
            continue
        stories.append({
            "url": url,
            "title": (item_element.findtext("title") or "").strip(),
            "published": parse_publish_time((item_element.findtext("pubDate") or "").strip()),
        })
    return stories


def select_new_stories(stories, seen, now, since_hours):
    """Stories not seen yet and published within the window, oldest first. Undated ones are skipped."""
    cutoff = now - timedelta(hours=since_hours)
    selected = []
    for story in stories:
        if story["url"] in seen:
            continue
        if story["published"] is None or story["published"] < cutoff:
            continue
        selected.append(story)
    selected.sort(key=lambda story: story["published"])
    return selected


def add_citation_values(value, found):
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for key in ("url", "@id"):
            if isinstance(value.get(key), str):
                found.append(value[key])
                break
    elif isinstance(value, list):
        for element in value:
            add_citation_values(element, found)


def collect_citations(node, found):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "citation":
                add_citation_values(value, found)
            else:
                collect_citations(value, found)
    elif isinstance(node, list):
        for element in node:
            collect_citations(element, found)


def extract_citations(article_html):
    """Source URLs from the article's JSON-LD 'citation' field: tracking removed, in order, no duplicates."""
    found = []
    for block_text in LD_JSON_BLOCK.findall(article_html):
        try:
            block = json.loads(block_text)
        except json.JSONDecodeError:
            continue
        collect_citations(block, found)
    unique_urls = []
    for url in found:
        if not url.startswith(("http://", "https://")):
            continue
        if urls.host_of(url) == "alphasignal.ai":
            continue  # AlphaSignal only discovers stories; its own pages are never a source
        clean_url = urls.without_tracking(url)
        if clean_url not in unique_urls:
            unique_urls.append(clean_url)
    return unique_urls
