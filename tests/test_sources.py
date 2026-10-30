import json

import pytest

from ainews import sources
from tests.helpers import converted_browser_capture, raw_browser_capture

ARXIV_ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<title>Training Report of
  TeleChat3-MoE</title>
<summary>  We present a
  model. </summary>
<published>2025-12-30T10:00:00Z</published>
<author><name>A. Author</name></author><author><name>B. Author</name></author>
</entry></feed>"""


def fake_http(responses):
    """An http_get stand-in: the first matching URL prefix answers (text) or fails (exception)."""
    calls = []

    def http_get(url, headers=None, timeout=30):
        calls.append(url)
        for prefix, reply in responses:
            if url.startswith(prefix):
                if isinstance(reply, Exception):
                    raise reply
                return reply
        raise sources.SourceError(f"HTTP 404 from {url}")

    return http_get, calls


@pytest.mark.parametrize(
    "url, kind",
    [
        ("https://huggingface.co/a/b", "huggingface"),
        ("https://www.github.com/o/r", "github"),
        ("https://arxiv.org/pdf/2609.18323", "arxiv"),
        ("https://twitter.com/u/status/1", "x"),
        ("https://x.com/u/status/1", "x"),
        ("https://docs.cohere.com/docs/north", "web"),
    ],
)
def test_classify(url, kind):
    assert sources.classify(url) == kind


@pytest.mark.parametrize(
    "url, readme_url",
    [
        ("https://huggingface.co/a/b", "https://huggingface.co/a/b/raw/main/README.md"),
        ("https://huggingface.co/a/b/tree/main", "https://huggingface.co/a/b/raw/main/README.md"),
        ("https://huggingface.co/datasets/o/d", "https://huggingface.co/datasets/o/d/raw/main/README.md"),
        ("https://huggingface.co/blog/some-post", None),
        ("https://huggingface.co/openbmb", None),
    ],
)
def test_huggingface_readme_url(url, readme_url):
    assert sources.huggingface_readme_url(url) == readme_url


def test_github_readme_api_url_only_for_repository_roots():
    assert sources.github_readme_api_url("https://github.com/o/r") == "https://api.github.com/repos/o/r/readme"
    assert sources.github_readme_api_url("https://github.com/o/r/pull/26") is None


@pytest.mark.parametrize(
    "url, paper_id",
    [
        ("https://arxiv.org/abs/2512.24157", "2512.24157"),
        ("https://arxiv.org/pdf/2609.18323v2", "2609.18323"),
        ("https://arxiv.org/html/2609.13916v1", "2609.13916"),
        ("https://arxiv.org/list/cs.AI/recent", None),
    ],
)
def test_arxiv_id(url, paper_id):
    assert sources.arxiv_id(url) == paper_id


def test_tweet_id():
    assert sources.tweet_id("https://x.com/cohere/status/2106422961494433962?s=20") == "2106422961494433962"
    assert sources.tweet_id("https://x.com/cohere") is None


def test_parse_arxiv_atom():
    assert sources.parse_arxiv_atom(ARXIV_ATOM) == (
        "Title: Training Report of TeleChat3-MoE\n"
        "Published: 2025-12-30\n"
        "Authors: A. Author, B. Author\n\n"
        "Abstract:\nWe present a model."
    )


def test_parse_arxiv_atom_without_entry_raises():
    with pytest.raises(sources.SourceError, match="no entry"):
        sources.parse_arxiv_atom('<feed xmlns="http://www.w3.org/2005/Atom"></feed>')


def test_parse_fxtwitter_response():
    payload = {"code": 200, "tweet": {"text": "We released North Small Translate", "created_at": "Sat Oct 03 2026", "author": {"screen_name": "cohere"}}}
    assert sources.parse_fxtwitter_response(payload) == "Post by @cohere on Sat Oct 03 2026:\n\nWe released North Small Translate"


def test_parse_twitterapi_response_without_tweets_raises():
    payload = {"error": "Unauthorized", "message": "Credits is not enough.Please recharge"}
    with pytest.raises(sources.SourceError, match="Credits"):
        sources.parse_twitterapi_response(payload)


def test_html_to_text_keeps_content_and_drops_scripts_and_navigation():
    html_text = (
        "<html><head><title>Kolibri-1</title><style>p{}</style></head><body>"
        "<nav>Home About</nav><h1>Kolibri-1</h1><p>78B total,&nbsp;3.46B active.</p>"
        "<script>var x=1;</script><ul><li>Apache 2.0</li><li>1M context</li></ul>"
        "<footer>Cookie settings</footer></body></html>"
    )
    assert sources.html_to_text(html_text) == "Kolibri-1\nKolibri-1\n78B total, 3.46B active.\n- Apache 2.0\n- 1M context"


def test_truncate_marks_long_text():
    truncated = sources.truncate("a" * 50000)
    assert truncated.startswith("a" * sources.MAX_SOURCE_CHARS)
    assert truncated.endswith("[truncated: source is 50000 characters, kept the first 40000]")
    assert sources.truncate("short") == "short"


def test_read_source_uses_huggingface_raw_readme(monkeypatch):
    http_get, calls = fake_http([("https://huggingface.co/a/b/raw/main/README.md", "---\nlicense: apache-2.0\n---\n# Model")])
    monkeypatch.setattr(sources, "http_get", http_get)
    kind, text = sources.read_source("https://huggingface.co/a/b")
    assert kind == "huggingface"
    assert "license: apache-2.0" in text
    assert calls == ["https://huggingface.co/a/b/raw/main/README.md"]


def test_read_source_falls_back_to_the_page_when_the_readme_is_missing(monkeypatch):
    page = "<html><body><p>" + "Model card text. " * 30 + "</p></body></html>"
    http_get, calls = fake_http([
        ("https://huggingface.co/a/b/raw", sources.SourceError("HTTP 404 from huggingface.co")),
        ("https://huggingface.co/a/b", page),
    ])
    monkeypatch.setattr(sources, "http_get", http_get)
    kind, text = sources.read_source("https://huggingface.co/a/b")
    assert kind == "huggingface"
    assert "Model card text." in text
    assert calls == ["https://huggingface.co/a/b/raw/main/README.md", "https://huggingface.co/a/b"]


def test_read_source_github_repository_uses_the_readme_api(monkeypatch):
    http_get, calls = fake_http([("https://api.github.com/repos/o/r/readme", "# FreeVideo\nRuns on 8 GB VRAM")])
    monkeypatch.setattr(sources, "http_get", http_get)
    assert sources.read_source("https://github.com/o/r") == ("github", "# FreeVideo\nRuns on 8 GB VRAM")


def test_read_source_arxiv_uses_the_export_api(monkeypatch):
    http_get, calls = fake_http([("https://export.arxiv.org/api/query?id_list=2512.24157", ARXIV_ATOM)])
    monkeypatch.setattr(sources, "http_get", http_get)
    kind, text = sources.read_source("https://arxiv.org/abs/2512.24157v3")
    assert kind == "arxiv"
    assert text.startswith("Title: Training Report of TeleChat3-MoE")


def test_read_source_x_falls_back_to_fxtwitter(monkeypatch):
    monkeypatch.setenv("TWITTERAPI_IO_KEY", "test-key")
    monkeypatch.setattr(sources, "twitterapi_unavailable", False)
    http_get, calls = fake_http([
        ("https://api.twitterapi.io/", sources.SourceError("HTTP 402 from api.twitterapi.io")),
        ("https://api.fxtwitter.com/status/42", json.dumps({"tweet": {"text": "Launch day", "created_at": "Sat", "author": {"screen_name": "lab"}}})),
    ])
    monkeypatch.setattr(sources, "http_get", http_get)
    kind, text = sources.read_source("https://x.com/lab/status/42")
    assert kind == "x"
    assert "Launch day" in text
    assert calls == ["https://api.twitterapi.io/twitter/tweets?tweet_ids=42", "https://api.fxtwitter.com/status/42"]


def test_read_source_x_skips_twitterapi_after_its_first_failure(monkeypatch):
    monkeypatch.setenv("TWITTERAPI_IO_KEY", "test-key")
    monkeypatch.setattr(sources, "twitterapi_unavailable", False)
    fxtwitter_reply = json.dumps({"tweet": {"text": "Launch day", "created_at": "Sat", "author": {"screen_name": "lab"}}})
    http_get, calls = fake_http([
        ("https://api.twitterapi.io/", sources.SourceError("HTTP 402 from api.twitterapi.io", status=402)),
        ("https://api.fxtwitter.com/status/", fxtwitter_reply),
    ])
    monkeypatch.setattr(sources, "http_get", http_get)
    sources.read_source("https://x.com/lab/status/1")
    sources.read_source("https://x.com/lab/status/2")
    assert calls == [
        "https://api.twitterapi.io/twitter/tweets?tweet_ids=1",
        "https://api.fxtwitter.com/status/1",
        "https://api.fxtwitter.com/status/2",
    ]


def test_read_source_x_keeps_trying_twitterapi_when_it_only_lacks_one_post(monkeypatch):
    monkeypatch.setenv("TWITTERAPI_IO_KEY", "test-key")
    monkeypatch.setattr(sources, "twitterapi_unavailable", False)
    fxtwitter_reply = json.dumps({"tweet": {"text": "Launch day", "created_at": "Sat", "author": {"screen_name": "lab"}}})
    http_get, calls = fake_http([
        ("https://api.twitterapi.io/", json.dumps({"tweets": [], "message": "not found"})),
        ("https://api.fxtwitter.com/status/", fxtwitter_reply),
    ])
    monkeypatch.setattr(sources, "http_get", http_get)
    sources.read_source("https://x.com/lab/status/1")
    sources.read_source("https://x.com/lab/status/2")
    assert calls[2] == "https://api.twitterapi.io/twitter/tweets?tweet_ids=2"


def test_format_source_file_and_source_file_url_round_trip():
    text = sources.format_source_file("https://e.com/a", "Body text")
    assert text == "URL: https://e.com/a\n\nBody text\n"
    assert sources.source_file_url(text) == "https://e.com/a"
    assert sources.source_file_url("no header\nURL: https://e.com/a") is None
    assert sources.source_file_url("") is None


def test_story_source_files_lists_every_txt_except_unconverted_browser_captures(tmp_path):
    (tmp_path / "1-web.txt").write_text(sources.format_source_file("https://e.com/a", "text"), encoding="utf-8")
    (tmp_path / "2-browser.txt").write_text(converted_browser_capture("https://e.com/b"), encoding="utf-8")
    (tmp_path / "3-browser.txt").write_text(raw_browser_capture("https://e.com/c"), encoding="utf-8")
    (tmp_path / "4-webfetch.txt").write_text(sources.format_source_file("https://e.com/d", "text"), encoding="utf-8")
    (tmp_path / "notes.md").write_text("not a source", encoding="utf-8")
    names = []
    for source_path in sources.story_source_files(tmp_path):
        names.append(source_path.name)
    assert names == ["1-web.txt", "2-browser.txt", "4-webfetch.txt"]
    assert sources.story_source_files(tmp_path / "absent") == []


def test_read_source_rejects_page_with_almost_no_text(monkeypatch):
    http_get, calls = fake_http([("https://example.com/app", "<html><body><div id='root'></div><script>app()</script></body></html>")])
    monkeypatch.setattr(sources, "http_get", http_get)
    with pytest.raises(sources.SourceError, match="almost no readable text"):
        sources.read_source("https://example.com/app")


def test_http_get_turns_a_non_ascii_url_into_a_source_error():
    with pytest.raises(sources.SourceError, match="unusable URL"):
        sources.http_get("https://example.invalid/caf\u00e9-news")


def test_read_source_arxiv_falls_back_to_the_abstract_page_when_the_api_fails(monkeypatch):
    abstract_page = (
        "<html><body><h1>Title: Harness Search for LLM Agents</h1>"
        "<blockquote>Abstract: " + "The performance of LLM-based agents depends on their harness. " * 6 + "</blockquote>"
        "</body></html>"
    )
    http_get, calls = fake_http([
        ("https://export.arxiv.org/api/query", sources.SourceError("HTTP 429 from export.arxiv.org")),
        ("https://arxiv.org/abs/2606.09498", abstract_page),
    ])
    monkeypatch.setattr(sources, "http_get", http_get)
    kind, text = sources.read_source("https://arxiv.org/pdf/2606.09498v2")
    assert kind == "arxiv"
    assert "The performance of LLM-based agents" in text
    assert calls == ["https://export.arxiv.org/api/query?id_list=2606.09498", "https://arxiv.org/abs/2606.09498"]


class FakeResponse:
    def __init__(self, body, content_type):
        import email.message
        self.body = body
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type

    def read(self, size=-1):
        if size >= 0:
            return self.body[:size]
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exception_details):
        return False


def test_http_get_retries_through_curl_when_the_certificate_check_fails(monkeypatch):
    import ssl
    import urllib.error

    def failing_urlopen(request, timeout=30):
        raise urllib.error.URLError(ssl.SSLCertVerificationError(1, "unable to get local issuer certificate"))

    curl_calls = []

    def fake_curl_get(url, headers, timeout):
        curl_calls.append((url, headers))
        return "<html>abstract page</html>"

    monkeypatch.setattr(sources.urllib.request, "urlopen", failing_urlopen)
    monkeypatch.setattr(sources, "curl_get", fake_curl_get)
    assert sources.http_get("https://arxiv.org/abs/2511.22677", {"Accept": "text/html"}) == "<html>abstract page</html>"
    assert curl_calls[0][0] == "https://arxiv.org/abs/2511.22677"
    assert curl_calls[0][1]["Accept"] == "text/html"
    assert curl_calls[0][1]["User-Agent"] == sources.USER_AGENT


def test_http_get_reports_a_pdf_instead_of_returning_binary(monkeypatch):
    def pdf_urlopen(request, timeout=30):
        return FakeResponse(b"%PDF-1.7 binary stream", "application/pdf")

    monkeypatch.setattr(sources.urllib.request, "urlopen", pdf_urlopen)
    with pytest.raises(sources.SourceError, match="PDF document"):
        sources.http_get("https://example.org/paper.pdf")


def test_curl_get_sends_headers_on_stdin_and_rejects_pdf(monkeypatch):
    import subprocess

    runs = []

    def fake_run(command, input=None, capture_output=False, timeout=None):
        runs.append((command, input))
        body = b"%PDF-1.4 binary" if command[-1].endswith(".pdf") else b"<html>ok</html>"
        return subprocess.CompletedProcess(command, 0, stdout=body, stderr=b"")

    monkeypatch.setattr(sources.subprocess, "run", fake_run)
    assert sources.curl_get("https://arxiv.org/abs/1", {"X-API-Key": "secret-value"}, 30) == "<html>ok</html>"
    command, stdin_text = runs[0]
    assert "secret-value" not in " ".join(command)
    assert command[command.index("--max-filesize") + 1] == str(sources.MAX_RESPONSE_BYTES)
    assert "X-API-Key: secret-value" in stdin_text.decode("utf-8")
    with pytest.raises(sources.SourceError, match="PDF document"):
        sources.curl_get("https://example.org/paper.pdf", {}, 30)


def test_curl_get_reports_a_failed_request(monkeypatch):
    import subprocess

    def failing_run(command, input=None, capture_output=False, timeout=None):
        return subprocess.CompletedProcess(command, 22, stdout=b"", stderr=b"curl: (22) The requested URL returned error: 404")

    monkeypatch.setattr(sources.subprocess, "run", failing_run)
    with pytest.raises(sources.SourceError, match="404"):
        sources.curl_get("https://arxiv.org/abs/1", {}, 30)


def x_post_after_twitterapi_failure(monkeypatch, failure):
    monkeypatch.setenv("TWITTERAPI_IO_KEY", "test-key")
    monkeypatch.setattr(sources, "twitterapi_unavailable", False)
    fxtwitter_reply = json.dumps({"tweet": {"text": "Launch day", "created_at": "Sat", "author": {"screen_name": "lab"}}})
    http_get, calls = fake_http([
        ("https://api.twitterapi.io/", failure),
        ("https://api.fxtwitter.com/status/", fxtwitter_reply),
    ])
    monkeypatch.setattr(sources, "http_get", http_get)
    sources.read_source("https://x.com/lab/status/1")
    sources.read_source("https://x.com/lab/status/2")
    return calls


def test_read_source_x_stops_using_twitterapi_after_a_402(monkeypatch):
    calls = x_post_after_twitterapi_failure(monkeypatch, sources.SourceError("HTTP 402 from api.twitterapi.io", status=402))
    assert "https://api.twitterapi.io/twitter/tweets?tweet_ids=2" not in calls


def test_read_source_x_keeps_using_twitterapi_after_a_timeout(monkeypatch):
    calls = x_post_after_twitterapi_failure(monkeypatch, sources.SourceError("could not reach api.twitterapi.io: timed out"))
    assert "https://api.twitterapi.io/twitter/tweets?tweet_ids=2" in calls


def test_read_source_x_keeps_using_twitterapi_after_a_429(monkeypatch):
    calls = x_post_after_twitterapi_failure(monkeypatch, sources.SourceError("HTTP 429 from api.twitterapi.io", status=429))
    assert "https://api.twitterapi.io/twitter/tweets?tweet_ids=2" in calls


def test_html_to_text_keeps_text_inside_a_form_wrapping_the_whole_page():
    html_text = (
        "<html><body><form id=aspnetForm><main><h1>Release notes</h1>"
        "<p>Version 4 ships with 2x faster decoding.</p></main></form></body></html>"
    )
    text = sources.html_to_text(html_text)
    assert "Release notes" in text
    assert "Version 4 ships with 2x faster decoding." in text


def test_http_get_reads_at_most_the_size_cap(monkeypatch):
    read_sizes = []

    class CountingResponse(FakeResponse):
        def read(self, size=-1):
            read_sizes.append(size)
            if size >= 0:
                return self.body[:size]
            return self.body

    def big_urlopen(request, timeout=30):
        return CountingResponse(b"a" * (sources.MAX_RESPONSE_BYTES + 1000), "text/html")

    monkeypatch.setattr(sources.urllib.request, "urlopen", big_urlopen)
    text = sources.http_get("https://example.org/huge")
    assert read_sizes == [sources.MAX_RESPONSE_BYTES]
    assert len(text) == sources.MAX_RESPONSE_BYTES
