"""Story discovery beside AlphaSignal: AI stories on Hacker News, Hugging Face Daily Papers and
two Russian Telegram channels.

All point straight at primary sources: a Hacker News story or a Telegram post links the repository,
post or paper itself, and a daily paper comes with its arXiv id. Their own pages and post texts are
never summarized.
"""
import html
import re
from datetime import datetime, timedelta, timezone

from ainews import sources, urls

HACKER_NEWS = "Hacker News"
DAILY_PAPERS = "Hugging Face Daily Papers"
TELEGRAM = "Telegram"
HN_SEARCH_URL = (
    "https://hn.algolia.com/api/v1/search_by_date?tags=story"
    "&numericFilters=created_at_i>{since},points>={min_points}&hitsPerPage=200"
)
HN_ITEM_URL = "https://news.ycombinator.com/item?id={}"
HN_MIN_POINTS = 100
DAILY_PAPERS_URL = "https://huggingface.co/api/daily_papers?date={}"
PAPER_PAGE_URL = "https://huggingface.co/papers/{}"
ARXIV_ABSTRACT_URL = "https://arxiv.org/abs/{}"
PAPERS_MIN_UPVOTES = 10
TELEGRAM_PREVIEW_URL = "https://t.me/s/{}"
TELEGRAM_POST_URL = "https://t.me/{}"
TELEGRAM_MAX_PAGES = 4
TELEGRAM_MAX_STORIES_PER_CHANNEL = 6
TELEGRAM_HOSTS = ("t.me", "telegram.me", "telegram.org")
AI_TERMS = re.compile(
    r"\b(ai|agi|llms?|chatgpt|openai|anthropic|deepmind|ии|нейросет\w*|нейронк\w*|"
    r"(?:gpt|claude|gemini|gemma|llama|qwen|mistral|deepseek|kimi|grok)[\w.-]*|phi-?\d[\w.-]*|"
    r"hugging ?face|transformers?|diffusion|neural|machine learning|deep learning|"
    r"artificial intelligence|fine-?tun\w*|embeddings?|agents?|agentic|copilot|vllm|ollama|gguf|lora|"
    r"multimodal|language models?|reasoning models?|inference)\b",
    re.IGNORECASE,
)
AI_DOMAINS = (
    "huggingface.co", "arxiv.org", "openai.com", "anthropic.com", "claude.com", "deepmind.google",
    "ai.google.dev", "mistral.ai", "ollama.com", "ai.meta.com",
)


def is_ai_story(title, url):
    host = urls.host_of(url)
    for domain in AI_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return True
    return AI_TERMS.search(title) is not None


def hacker_news_search_url(now, since_hours):
    since = int(now.timestamp()) - since_hours * 3600
    return HN_SEARCH_URL.format(since=since, min_points=HN_MIN_POINTS)


def hacker_news_stories(payload, max_stories):
    """AI stories from an Algolia search payload, most points first. Self-posts without a link are skipped."""
    candidates = []
    hits = []
    if isinstance(payload, dict) and isinstance(payload.get("hits"), list):
        hits = payload["hits"]
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        link = hit.get("url")
        title = hit.get("title")
        if not link or not title or not is_ai_story(title, link):
            continue
        candidates.append(hit)
    candidates.sort(key=lambda hit: hit.get("points") or 0, reverse=True)
    stories = []
    for hit in candidates[:max_stories]:
        stories.append({
            "url": HN_ITEM_URL.format(hit["objectID"]),
            "slug": f"hn-{hit['objectID']}-{urls.slugify(hit['title'], 50)}",
            "title": hit["title"],
            "published": datetime.fromtimestamp(hit["created_at_i"], tz=timezone.utc),
            "source_urls": [hit["url"]],
            "via": HACKER_NEWS,
            "note": f"{hit.get('points', 0)} points",
        })
    return stories


def daily_papers_url(papers_date):
    return DAILY_PAPERS_URL.format(papers_date.isoformat())


def daily_paper_stories(payload, max_stories):
    """The most upvoted papers of one Daily Papers list, read through their arXiv abstracts."""
    candidates = []
    for entry in payload if isinstance(payload, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("paper"), dict):
            continue
        paper = entry["paper"]
        if not paper.get("id") or (paper.get("upvotes") or 0) < PAPERS_MIN_UPVOTES:
            continue
        candidates.append(paper)
    candidates.sort(key=lambda paper: paper.get("upvotes") or 0, reverse=True)
    stories = []
    for paper in candidates[:max_stories]:
        published = None
        if paper.get("publishedAt"):
            published = datetime.fromisoformat(paper["publishedAt"].replace("Z", "+00:00"))
        stories.append({
            "url": PAPER_PAGE_URL.format(paper["id"]),
            "slug": f"paper-{paper['id'].replace('.', '-')}-{urls.slugify(paper.get('title', ''), 50)}",
            "title": " ".join(str(paper.get("title", "")).split()),
            "published": published,
            "source_urls": [ARXIV_ABSTRACT_URL.format(paper["id"])],
            "via": DAILY_PAPERS,
            "note": f"{paper.get('upvotes', 0)} upvotes",
        })
    return stories


def telegram_preview_url(channel, before=None):
    """The channel's public web preview: its latest ~20 posts, or the ones before a post id."""
    if before is None:
        return TELEGRAM_PREVIEW_URL.format(channel)
    return TELEGRAM_PREVIEW_URL.format(channel) + f"?before={before}"


def element_inner_html(page, tag_start):
    """The markup inside the <div> that opens at tag_start, nested divs included."""
    content_start = page.index(">", tag_start) + 1
    depth = 1
    position = content_start
    while depth > 0:
        next_open = page.find("<div", position)
        next_close = page.find("</div>", position)
        if next_close == -1:
            return page[content_start:]
        if next_open != -1 and next_open < next_close:
            depth += 1
            position = next_open + len("<div")
        else:
            depth -= 1
            position = next_close + len("</div>")
    return page[content_start:position - len("</div>")]


def fully_unescaped(markup):
    """Unescape until stable: some channels' links are escaped twice (&amp;amp;)."""
    for attempt in range(3):
        unescaped = html.unescape(markup)
        if unescaped == markup:
            break
        markup = unescaped
    return markup


def telegram_posts(page_html):
    """Posts of one preview page: channel, id, published, text, outside links and whether it is an ad.

    Links come only from the post's own text (not link previews, replies or author links). A post is an
    ad when any of its links carries an erid (Russia's ad-labelling id) or it is marked "Реклама".
    """
    posts = []
    for block in page_html.split('class="tgme_widget_message_wrap')[1:]:
        post_match = re.search(r'data-post="([^"/]+)/(\d+)"', block)
        time_match = re.search(r'<time datetime="([^"]+)"', block)
        if post_match is None or time_match is None:
            continue
        text_html = ""
        text_marker = block.find('js-message_text"')
        if text_marker != -1:
            text_html = element_inner_html(block, block.rfind("<div", 0, text_marker))
        links = []
        for raw_href in re.findall(r'href="([^"]+)"', text_html):
            href = fully_unescaped(raw_href)
            if not href.startswith(("http://", "https://")) or urls.host_of(href) in TELEGRAM_HOSTS:
                continue
            link = urls.without_tracking(href)
            if link not in links:
                links.append(link)
        unescaped_block = fully_unescaped(block)
        is_ad = "erid=" in unescaped_block.lower() or re.search(r"реклама", unescaped_block, re.IGNORECASE) is not None
        posts.append({
            "channel": post_match.group(1),
            "id": int(post_match.group(2)),
            "published": datetime.fromisoformat(time_match.group(1)),
            "text": sources.html_to_text(text_html),
            "links": links,
            "is_ad": is_ad,
        })
    return posts


def post_headline(text):
    """The post's first line, shortened at a word boundary."""
    first_line = ""
    for line in text.split("\n"):
        if line.strip():
            first_line = line.strip()
            break
    if len(first_line) <= 140:
        return first_line
    return first_line[:140].rsplit(" ", 1)[0] + "…"


def telegram_skip_reason(post, now, since_hours):
    """Why a post is not a story, or None when it is one."""
    if post["published"] < now - timedelta(hours=since_hours):
        return "older than the window"
    if post["is_ad"]:
        return "ad"
    if len(post["links"]) == 0:
        return "no outside link"
    for link in post["links"]:
        if is_ai_story(post["text"], link):
            return None
    return "not about AI"


def telegram_stories(posts, now, since_hours, max_stories=TELEGRAM_MAX_STORIES_PER_CHANNEL):
    """The newest AI posts of one channel that link outside Telegram; the linked pages are the sources."""
    newest_first = sorted(posts, key=lambda post: post["id"], reverse=True)
    stories = []
    taken_ids = set()
    for post in newest_first:
        if post["id"] in taken_ids or telegram_skip_reason(post, now, since_hours) is not None:
            continue
        taken_ids.add(post["id"])
        stories.append({
            "url": TELEGRAM_POST_URL.format(f"{post['channel']}/{post['id']}"),
            "slug": f"tg-{post['channel']}-{post['id']}",
            "title": post_headline(post["text"]),
            "published": post["published"],
            "source_urls": post["links"],
            "via": TELEGRAM,
            "note": f"@{post['channel']}",
        })
        if len(stories) == max_stories:
            break
    return stories
