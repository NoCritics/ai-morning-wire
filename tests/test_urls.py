import pytest

from ainews import urls


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://e.com/p?utm_source=tg&id=7&erid=2Vtz", "https://e.com/p?id=7"),
        ("https://e.com/p?ysclid=abc", "https://e.com/p"),
        ("https://x.com/karpathy/status/1?s=46", "https://x.com/karpathy/status/1"),
        ("https://e.com/p?s=46", "https://e.com/p?s=46"),
        ("https://e.com/a,b?q=1,2", "https://e.com/a,b?q=1,2"),
        ("https://mobile.twitter.com/karpathy/status/1?s=46&t=abc", "https://mobile.twitter.com/karpathy/status/1"),
    ],
)
def test_without_tracking(url, expected):
    assert urls.without_tracking(url) == expected


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.Example.com/post", "example.com"),
        ("http://t.me/s/channel", "t.me"),
        ("https://mobile.twitter.com/a", "mobile.twitter.com"),
    ],
)
def test_host_of(url, expected):
    assert urls.host_of(url) == expected


@pytest.mark.parametrize(
    "first, second",
    [
        ("https://www.Example.com/post/", "https://example.com/post"),
        ("http://example.com/a?x=1", "https://example.com/a?x=1"),
        ("https://twitter.com/karpathy/status/1", "https://x.com/karpathy/status/1"),
        ("https://mobile.twitter.com/karpathy/status/1", "https://x.com/karpathy/status/1"),
        ("https://x.com/a/status/1?s=20", "https://x.com/a/status/1"),
        ("https://e.com/a?utm_source=tg&id=7&erid=2Vtz", "https://e.com/a?id=7"),
    ],
)
def test_normalize_url_ignores_scheme_www_case_and_trailing_slash(first, second):
    assert urls.normalize_url(first) == urls.normalize_url(second)


def test_normalize_url_keeps_query_and_path_differences():
    assert urls.normalize_url("https://x.com/a/status/1") != urls.normalize_url("https://x.com/a/status/2")
    assert urls.normalize_url("https://e.com/a?x=1") != urls.normalize_url("https://e.com/a?x=2")


def test_specific_page_keys_keeps_normalized_deep_links_only():
    keys = urls.specific_page_keys([
        "https://www.openai.com/index/a/",
        "https://openai.com/index/a?utm_source=tg",
        "https://openai.com/",
        "https://twitter.com/lab/status/1",
    ])
    assert keys == {"openai.com/index/a", "x.com/lab/status/1"}


def test_specific_page_keys_of_no_urls_is_empty():
    assert urls.specific_page_keys([]) == set()


@pytest.mark.parametrize(
    "text, max_length, slug",
    [
        ("Qwen3.8-27B: 8.8 GB, 176 tok/s!", 60, "qwen3-8-27b-8-8-gb-176-tok-s"),
        ("!!!", 60, ""),
        ("Kolibri-1: open German reasoning model", 10, "kolibri-1"),
        ("Kolibri-1: open German reasoning model", 50, "kolibri-1-open-german-reasoning-model"),
    ],
)
def test_slugify_cuts_to_the_limit_without_a_trailing_hyphen(text, max_length, slug):
    assert urls.slugify(text, max_length) == slug
