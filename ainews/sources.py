"""Read a primary source as raw text, choosing the most direct route for each kind of link."""
import http.client
import json
import os
import re
import ssl
import subprocess
import urllib.error
import urllib.request
import xml.etree.ElementTree as ElementTree
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from ainews import urls

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ai-morning-wire/1.0"
MAX_SOURCE_CHARS = 40000
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # a longer page is cut here, long before the text is cut to MAX_SOURCE_CHARS
MIN_PAGE_TEXT_CHARS = 200
PDF_MESSAGE = "PDF document, not readable as text"
BOT_CHECK_MARKERS = (
    "just a moment", "verify you are human", "access denied", "enable javascript and cookies",
    "checking your browser", "prove your humanity", "not for bots",
)
BOT_CHECK_MAX_CHARS = 1000
SOURCE_URL_PREFIX = "URL: "
BROWSER_MARKER = "\n\n(text read in a browser)\n\n"
ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
TWEET_ID = re.compile(r"/status(?:es)?/(\d+)")
ATOM = "{http://www.w3.org/2005/Atom}"
HUGGINGFACE_NON_REPO_SECTIONS = ("blog", "papers", "docs", "collections", "posts", "learn", "models", "spaces", "datasets")
SKIPPED_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "button", "iframe", "template"}
BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "tr", "table", "pre", "blockquote", "header", "title",
}


TWITTERAPI_ACCOUNT_STATUSES = (401, 402, 403)
twitterapi_unavailable = False  # set once twitterapi.io rejects the key or account, so the rest of the run goes straight to fxtwitter


class SourceError(Exception):
    """A source could not be read; the message says why. status is the HTTP status when the server answered with one."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def is_pdf(body, content_type):
    return content_type == "application/pdf" or body[:5] == b"%PDF-"


def curl_get(url, headers, timeout):
    """Fetch with the system curl, which on Windows uses the OS TLS stack and its certificate store."""
    header_lines = []
    for name, value in headers.items():
        header_lines.append(f"{name}: {value}")
    command = ["curl", "--silent", "--show-error", "--location", "--fail", "--max-time", str(timeout), "--max-filesize", str(MAX_RESPONSE_BYTES), "--header", "@-", url]
    try:
        completed = subprocess.run(
            command,
            input="\n".join(header_lines).encode("utf-8"),  # on stdin so keys never appear in the process list
            capture_output=True,
            timeout=timeout + 10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SourceError(f"curl could not run for {urlsplit(url).netloc}: {error}") from None
    if completed.returncode != 0:
        raise SourceError(f"curl could not fetch {urlsplit(url).netloc}: {completed.stderr.decode('utf-8', errors='replace').strip()[:200]}")
    if is_pdf(completed.stdout, ""):
        raise SourceError(PDF_MESSAGE)
    return completed.stdout.decode("utf-8", errors="replace")


def http_get(url, headers=None, timeout=30):
    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES)
            content_type = response.headers.get_content_type()
            charset = response.headers.get_content_charset() or "utf-8"
        if is_pdf(body, content_type):
            raise SourceError(PDF_MESSAGE)
        return body.decode(charset, errors="replace")
    except urllib.error.HTTPError as error:
        raise SourceError(f"HTTP {error.code} from {urlsplit(url).netloc}", status=error.code) from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            # A server that omits an intermediate certificate fails Python's check; the OS TLS stack fetches it.
            return curl_get(url, request_headers, timeout)
        raise SourceError(f"could not reach {urlsplit(url).netloc}: {error}") from None
    except OSError as error:
        raise SourceError(f"could not reach {urlsplit(url).netloc}: {error}") from None
    except (http.client.HTTPException, ValueError, LookupError) as error:
        raise SourceError(f"unusable URL or response ({type(error).__name__}: {error})") from None


def classify(url):
    host = urls.host_of(url)
    if host == "huggingface.co":
        return "huggingface"
    if host == "github.com":
        return "github"
    if host in ("arxiv.org", "export.arxiv.org"):
        return "arxiv"
    if host in urls.X_HOSTS:
        return "x"
    return "web"


def path_parts(url):
    parts = []
    for part in urlsplit(url).path.split("/"):
        if part != "":
            parts.append(part)
    return parts


def huggingface_readme_url(url):
    """Raw README URL for a model, dataset or Space; None for other Hugging Face pages."""
    parts = path_parts(url)
    if len(parts) >= 3 and parts[0] in ("datasets", "spaces"):
        return f"https://huggingface.co/{parts[0]}/{parts[1]}/{parts[2]}/raw/main/README.md"
    if len(parts) >= 2 and parts[0] not in HUGGINGFACE_NON_REPO_SECTIONS:
        return f"https://huggingface.co/{parts[0]}/{parts[1]}/raw/main/README.md"
    return None


def github_readme_api_url(url):
    """README API URL for a repository root; None for deeper pages such as pull requests."""
    parts = path_parts(url)
    if len(parts) == 2:
        return f"https://api.github.com/repos/{parts[0]}/{parts[1].removesuffix('.git')}/readme"
    return None


def github_headers():
    headers = {"Accept": "application/vnd.github.raw"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    return headers


def arxiv_id(url):
    paper_match = ARXIV_ID.search(urlsplit(url).path)
    if paper_match is None:
        return None
    return paper_match.group(1)


def tweet_id(url):
    post_match = TWEET_ID.search(urlsplit(url).path)
    if post_match is None:
        return None
    return post_match.group(1)


def parse_arxiv_atom(atom_xml):
    entry = ElementTree.fromstring(atom_xml).find(f"{ATOM}entry")
    if entry is None:
        raise SourceError("arXiv returned no entry")
    abstract = " ".join((entry.findtext(f"{ATOM}summary") or "").split())
    if abstract == "":
        raise SourceError("arXiv entry has no abstract")
    author_names = []
    for author_element in entry.findall(f"{ATOM}author"):
        author_names.append(author_element.findtext(f"{ATOM}name") or "")
    return (
        f"Title: {' '.join((entry.findtext(f'{ATOM}title') or '').split())}\n"
        f"Published: {(entry.findtext(f'{ATOM}published') or '')[:10]}\n"
        f"Authors: {', '.join(author_names)}\n\n"
        f"Abstract:\n{abstract}"
    )


def parse_twitterapi_response(payload):
    tweets = payload.get("tweets") or []
    if len(tweets) == 0:
        raise SourceError(payload.get("message") or "twitterapi.io returned no post")
    tweet = tweets[0]
    author = tweet.get("author") or {}
    return f"Post by @{author.get('userName', '?')} on {tweet.get('createdAt', '?')}:\n\n{tweet.get('text', '')}"


def parse_fxtwitter_response(payload):
    tweet = payload.get("tweet")
    if not tweet:
        raise SourceError(payload.get("message") or "fxtwitter returned no post")
    author = tweet.get("author") or {}
    return f"Post by @{author.get('screen_name', '?')} on {tweet.get('created_at', '?')}:\n\n{tweet.get('text', '')}"


def read_x_post(url):
    global twitterapi_unavailable
    post_id = tweet_id(url)
    if post_id is None:
        raise SourceError("the X link has no post id")
    if os.environ.get("TWITTERAPI_IO_KEY") and not twitterapi_unavailable:
        try:
            response_text = http_get(
                f"https://api.twitterapi.io/twitter/tweets?tweet_ids={post_id}",
                {"X-API-Key": os.environ["TWITTERAPI_IO_KEY"]},
            )
        except SourceError as error:
            if error.status in TWITTERAPI_ACCOUNT_STATUSES:
                twitterapi_unavailable = True  # out of credits or a bad key: it would fail on every X link, so skip it from now on
        else:
            try:
                return parse_twitterapi_response(json.loads(response_text))
            except (SourceError, json.JSONDecodeError):
                pass  # this post is not there; the public mirror below may still have it
    return parse_fxtwitter_response(json.loads(http_get(f"https://api.fxtwitter.com/status/{post_id}")))


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIPPED_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth > 0:
            return
        if tag in BLOCK_TAGS:
            self.chunks.append("\n")
        if tag == "li":
            self.chunks.append("- ")

    def handle_startendtag(self, tag, attrs):
        if self.skip_depth == 0 and tag in BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIPPED_TAGS:
            if self.skip_depth > 0:
                self.skip_depth -= 1
            return
        if self.skip_depth == 0 and tag in BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data):
        if self.skip_depth == 0:
            self.chunks.append(data)


def html_to_text(html_text):
    extractor = TextExtractor()
    extractor.feed(html_text)
    extractor.close()
    lines = []
    for raw_line in "".join(extractor.chunks).splitlines():
        line = " ".join(raw_line.split())
        if line != "":
            lines.append(line)
    return "\n".join(lines)


def truncate(text):
    if len(text) <= MAX_SOURCE_CHARS:
        return text
    return text[:MAX_SOURCE_CHARS] + f"\n\n[truncated: source is {len(text)} characters, kept the first {MAX_SOURCE_CHARS}]"


def format_source_file(url, text):
    """The contents of a saved source file: a "URL: <url>" line, a blank line, then the text."""
    return f"{SOURCE_URL_PREFIX}{url}\n\n{text}\n"


def source_file_url(text):
    """The URL on the first line of a saved source file, or None when the file does not start with one."""
    first_line = text.split("\n", 1)[0]
    if first_line.startswith(SOURCE_URL_PREFIX):
        return first_line[len(SOURCE_URL_PREFIX):]
    return None


def browser_capture_text(raw_capture):
    """Tidy a page captured in a real browser: "URL: ..." header lines, a blank line, then the page text.

    The browser tool saves the page script's return value as a JSON string, so decode that first.
    Raises SourceError when the capture is a bot-check page or has almost no text.
    """
    capture = raw_capture.strip()
    if capture.startswith('"'):
        try:
            capture = json.loads(capture)
        except json.JSONDecodeError as error:
            raise SourceError(f"browser capture is not valid JSON: {error}") from None
    if source_file_url(capture) is None:
        raise SourceError("browser capture does not start with a URL line")
    header, found_marker, page_text = capture.partition(BROWSER_MARKER)
    if found_marker == "":
        header, _, page_text = capture.partition("\n\n")
    lines = []
    previous_blank = True
    for raw_line in page_text.splitlines():
        line = " ".join(raw_line.split())
        if line == "":
            if not previous_blank:
                lines.append("")
            previous_blank = True
            continue
        lines.append(line)
        previous_blank = False
    tidy_text = "\n".join(lines).strip()
    if len(tidy_text) < BOT_CHECK_MAX_CHARS:
        for marker in BOT_CHECK_MARKERS:
            if marker in tidy_text.lower():
                raise SourceError(f"the browser was stopped by a bot check ({marker!r})")
    if len(tidy_text) < MIN_PAGE_TEXT_CHARS:
        raise SourceError("page has almost no readable text, even in a browser")
    return header.strip() + BROWSER_MARKER + truncate(tidy_text)


def is_converted_browser_capture(capture_path):
    """True for a *-browser.txt that browser_source.py has converted: plain text, not the raw JSON-quoted capture
    and not a bot-check or near-empty page. Run browser_source.py on a capture before it counts as a source."""
    try:
        with open(capture_path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return False
    if source_file_url(text) is None:
        return False
    try:
        browser_capture_text(text)
    except SourceError:
        return False
    return True


def story_source_files(story_dir):
    """The saved source files of one story, by name: every *.txt except a *-browser.txt capture that
    browser_source.py has not converted (raw, or a bot-check page)."""
    paths = []
    for source_path in sorted(Path(story_dir).glob("*.txt")):
        if source_path.name.endswith("-browser.txt") and not is_converted_browser_capture(source_path):
            continue
        paths.append(source_path)
    return paths


def read_source(url):
    """Returns (kind, text). Raises SourceError when the source cannot be read."""
    kind = classify(url)
    try:
        if kind == "huggingface":
            readme_url = huggingface_readme_url(url)
            if readme_url is not None:
                try:
                    return kind, truncate(http_get(readme_url))
                except SourceError:
                    pass  # no README on main, or a gated repo; read the page itself below
        elif kind == "github":
            readme_api_url = github_readme_api_url(url)
            if readme_api_url is not None:
                try:
                    return kind, truncate(http_get(readme_api_url, github_headers()))
                except SourceError:
                    pass
        elif kind == "arxiv":
            paper_id = arxiv_id(url)
            if paper_id is not None:
                try:
                    return kind, parse_arxiv_atom(http_get(f"https://export.arxiv.org/api/query?id_list={paper_id}"))
                except (SourceError, ElementTree.ParseError):
                    pass  # the export API rate-limits often; the abstract page below carries the same abstract
                url = f"https://arxiv.org/abs/{paper_id}"
        elif kind == "x":
            return kind, read_x_post(url)
        page_text = html_to_text(http_get(url))
    except (json.JSONDecodeError, ElementTree.ParseError) as error:
        raise SourceError(f"unreadable response from {urlsplit(url).netloc}: {error}") from None
    if len(page_text) < MIN_PAGE_TEXT_CHARS:
        raise SourceError("page has almost no readable text (it is probably built by JavaScript)")
    return kind, truncate(page_text)
