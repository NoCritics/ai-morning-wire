"""URL cleanup shared by story discovery, source reading and the duplicate checks."""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMETER = re.compile(r"^(utm_\w+|erid|ysclid)$", re.IGNORECASE)
X_HOSTS = ("x.com", "twitter.com", "mobile.twitter.com")


def host_of(url):
    """The URL's host in lower case, without "www.": the same site however the link was written."""
    return urlsplit(url).netloc.lower().removeprefix("www.")


def without_tracking(url):
    """The URL without utm_*, erid and ysclid parameters (and X's share "s" and "t"), so it matches the same page cited elsewhere."""
    parts = urlsplit(url)
    is_x_post = host_of(url) in X_HOSTS
    kept_parameters = []
    removed_any = False
    for name, value in parse_qsl(parts.query, keep_blank_values=True):
        if TRACKING_PARAMETER.match(name) or (is_x_post and name in ("s", "t")):
            removed_any = True
        else:
            kept_parameters.append((name, value))
    if not removed_any:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept_parameters), parts.fragment))


def normalize_url(url):
    """Compare URLs without scheme, www., letter case of the host, a trailing slash or tracking parameters; twitter.com counts as x.com."""
    clean_url = without_tracking(url.strip())
    parts = urlsplit(clean_url)
    host = host_of(clean_url)
    if host in X_HOSTS:
        host = "x.com"
    path = parts.path.rstrip("/")
    if parts.query:
        return f"{host}{path}?{parts.query}"
    return f"{host}{path}"


def is_specific_page(url):
    """True for a deep link (a model card, post, paper), false for a site's front page."""
    return urlsplit(url).path.strip("/") != ""


def specific_page_keys(url_list):
    """The normalized URLs of the specific pages among url_list; site front pages are left out."""
    keys = set()
    for url in url_list:
        if is_specific_page(url):
            keys.add(normalize_url(url))
    return keys


def slugify(text, max_length):
    """Lower-case letters and digits joined by single hyphens, cut to max_length without a trailing hyphen."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:max_length].strip("-")
