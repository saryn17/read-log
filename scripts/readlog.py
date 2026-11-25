#!/usr/bin/env python3
"""read-log CLI. Standard library only.

  fetch URL [--chars N]  Fetch a page and print its metadata and a text excerpt as JSON
  add [--file F]         Add articles (JSON object or array from stdin or F)
  edit                   Change fields of stored articles (JSON patch(es) with id or url, from stdin)
  tags [--json]          List categories and tags in use, with counts
  categories             List the category ids with their hints
  remove URL_OR_ID       Remove an article
  archive [URL_OR_ID..]  Ask the Wayback Machine to save articles (default: those not saved yet)
  check-links            Report articles whose URL answers 404/410 (used by the monthly workflow)
  normalize              Rewrite articles.json in canonical form (after the schema changes)
  validate               Check site/data/articles.json (used by CI)

The site reads site/data/articles.json directly, so every write keeps that file
sorted, normalized and free of duplicates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "site" / "data"

FIELD_ORDER = ["id", "url", "title", "site", "author", "published", "read", "category", "kind", "tags",
               "summary", "points", "lang", "archived"]
KINDS = ["article", "slide", "video", "paper", "book", "tool", "page"]
# Format guessed from the domain when `kind` is not given. Anything else is an article.
# "book" (books and PDF handbooks), "tool" (apps) and "page" (reference sites, collections such as an
# advent calendar) are never guessed: give them explicitly.
KIND_HOSTS = {
    "slide": ("speakerdeck.com", "docswell.com", "slideshare.net"),
    "video": ("youtube.com", "youtu.be", "nicovideo.jp", "vimeo.com"),
    "paper": ("arxiv.org",),
}
MAX_TAGS = 8
MAX_TITLE = 200
MAX_SUMMARY = 160
MAX_TAG_LEN = 30
MAX_POINTS = 3
MAX_POINT = 80
ARCHIVE_SAVE = "https://web.archive.org/save/"
TRACKING_PARAMS = {"fbclid", "gclid", "yclid", "igshid", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "ref_src", "ref_url"}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 read-log/1.0"
)


def today() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------- paths / io

def articles_path() -> Path:
    return DATA_DIR / "articles.json"


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_bytes().decode("utf-8-sig"))


def write_json(path: Path, value) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_articles() -> list[dict]:
    return load_json(articles_path(), [])


def load_categories() -> list[dict]:
    return load_json(DATA_DIR / "categories.json", [])


def load_aliases() -> dict[str, str]:
    return load_json(DATA_DIR / "tag_aliases.json", {})


# ---------------------------------------------------------------- normalization

def normalize_url(url: str) -> str:
    """Canonical form stored in the data: https/http kept, fragment and tracking params dropped."""
    url = url.strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"not an http(s) URL: {url!r}")
    host = parts.hostname.lower() if parts.hostname else ""
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    query = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urllib.parse.urlunsplit((parts.scheme, netloc, path, urllib.parse.urlencode(query), ""))


def dedupe_key(url: str) -> str:
    """Same article reached through http/https, www or a trailing slash gets the same key."""
    parts = urllib.parse.urlsplit(normalize_url(url))
    host = parts.netloc[4:] if parts.netloc.startswith("www.") else parts.netloc
    query = urllib.parse.urlencode(sorted(urllib.parse.parse_qsl(parts.query, keep_blank_values=True)))
    return f"{host}{parts.path}" + (f"?{query}" if query else "")


def article_id(url: str) -> str:
    return hashlib.sha1(dedupe_key(url).encode("utf-8")).hexdigest()[:8]


def site_of(url: str) -> str:
    host = urllib.parse.urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def normalize_tag(tag: str, aliases: dict[str, str]) -> str:
    tag = unicodedata.normalize("NFKC", str(tag)).strip().lstrip("#").strip().lower()
    tag = re.sub(r"\s+", "-", tag)
    return aliases.get(tag, tag)


def normalize_tags(tags, aliases: dict[str, str]) -> list[str]:
    out: list[str] = []
    for t in tags:
        n = normalize_tag(t, aliases)
        if n and n not in out:
            out.append(n)
    return out


def parse_date(value, label: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValueError(f"{label} must be YYYY-MM-DD: {value!r}")


def guess_lang(text: str) -> str:
    return "ja" if re.search(r"[぀-ヿ一-鿿]", text) else "en"


def guess_kind(url: str) -> str:
    host = site_of(url)
    for kind, hosts in KIND_HOSTS.items():
        if any(host == h or host.endswith("." + h) for h in hosts):
            return kind
    return "article"


# ---------------------------------------------------------------- entries

def build_entry(raw: dict, categories: list[dict], aliases: dict[str, str]) -> tuple[dict | None, list[str]]:
    """Validate raw input and return the normalized entry, or the list of problems."""
    errors: list[str] = []
    try:
        url = normalize_url(raw.get("url", ""))
    except ValueError as e:
        return None, [str(e)]

    title = re.sub(r"\s+", " ", str(raw.get("title", ""))).strip()
    if not title:
        errors.append("title is empty")
    elif len(title) > MAX_TITLE:
        errors.append(f"title is longer than {MAX_TITLE} chars")

    category = str(raw.get("category", "")).strip()
    ids = [c["id"] for c in categories]
    if category not in ids:
        errors.append(f"category {category!r} is not one of {ids}")

    tags = normalize_tags(raw.get("tags") or [], aliases)
    if not 1 <= len(tags) <= MAX_TAGS:
        errors.append(f"tags must have 1 to {MAX_TAGS} entries after normalization (got {len(tags)})")
    for t in tags:
        if len(t) > MAX_TAG_LEN or re.search(r"[,#]", t):
            errors.append(f"tag {t!r} is too long or contains , or #")

    summary = re.sub(r"\s+", " ", str(raw.get("summary", ""))).strip()
    if not summary:
        errors.append("summary is empty")
    elif len(summary) > MAX_SUMMARY:
        errors.append(f"summary is longer than {MAX_SUMMARY} chars")

    kind = str(raw.get("kind") or "").strip().lower() or guess_kind(url)
    if kind not in KINDS:
        errors.append(f"kind {kind!r} is not one of {KINDS}")

    points_raw = raw.get("points")
    if points_raw is None:
        points_raw = []
    if not isinstance(points_raw, list):
        errors.append("points must be a list of strings")
        points_raw = []
    points = [re.sub(r"\s+", " ", str(p)).strip() for p in points_raw]
    points = [p for p in points if p]
    if len(points) > MAX_POINTS:
        errors.append(f"points has more than {MAX_POINTS} entries")
    for p in points:
        if len(p) > MAX_POINT:
            errors.append(f"point {p[:20]!r}... is longer than {MAX_POINT} chars")

    published = read = archived = None
    try:
        published = parse_date(raw.get("published"), "published")
        read = parse_date(raw.get("read"), "read") or today()
        archived = parse_date(raw.get("archived"), "archived")
    except ValueError as e:
        errors.append(str(e))
    limit = (date.fromisoformat(today()) + timedelta(days=1)).isoformat()
    for label, v in (("published", published), ("read", read)):
        if v and v > limit:
            errors.append(f"{label} {v} is in the future")

    if errors:
        return None, errors

    entry = {
        "id": article_id(url),
        "url": url,
        "title": title,
        "site": str(raw.get("site") or "").strip() or site_of(url),
        "author": (str(raw["author"]).strip() or None) if raw.get("author") else None,
        "published": published,
        "read": read,
        "category": category,
        "kind": kind,
        "tags": tags,
        "summary": summary,
        "points": points,
        "lang": str(raw.get("lang") or "").strip().lower() or guess_lang(title),
        "archived": archived,
    }
    return {k: entry[k] for k in FIELD_ORDER}, []


def sort_articles(items: list[dict]) -> list[dict]:
    # sorted() is stable, so entries read on the same day keep their relative order.
    return sorted(items, key=lambda a: a["read"], reverse=True)


def check_stored(items: list[dict], categories: list[dict], aliases: dict[str, str]) -> list[str]:
    problems: list[str] = []
    seen: dict[str, str] = {}
    for i, a in enumerate(items):
        where = f"[{i}] {a.get('url', '?')}"
        entry, errs = build_entry(a, categories, aliases)
        if errs:
            problems += [f"{where}: {e}" for e in errs]
            continue
        if list(a.keys()) != FIELD_ORDER:
            problems.append(f"{where}: fields are not in the canonical order")
        if a["id"] != entry["id"]:
            problems.append(f"{where}: id does not match the url")
        if a["url"] != entry["url"]:
            problems.append(f"{where}: url is not normalized (expected {entry['url']})")
        if a["tags"] != entry["tags"]:
            problems.append(f"{where}: tags are not normalized (expected {entry['tags']})")
        if a.get("points") != entry["points"]:
            problems.append(f"{where}: points are not normalized (expected {entry['points']})")
        key = dedupe_key(a["url"])
        if key in seen:
            problems.append(f"{where}: duplicates {seen[key]}")
        seen[key] = where
    if items != sort_articles(items):
        problems.append("articles are not sorted by read date (newest first)")
    return problems


# ---------------------------------------------------------------- fetch / parse

SKIP_TAGS = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "template"}
PUBLISHED_METAS = [
    "article:published_time", "datepublished", "date", "dc.date", "dc.date.issued", "dcterms.created",
    "publish-date", "publishdate", "pubdate", "sailthru.date", "parsely-pub-date", "og:published_time",
]


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.canonical = None
        self.lang = None
        self.title = ""
        self.h1 = ""
        self.ld: list = []
        self.time_dt = None
        self._in = {"title": False, "h1": False, "ld": False}
        self._skip = 0
        self._article = 0
        self._main = 0
        self._buf = {"article": [], "main": [], "body": []}
        self._ld_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html" and a.get("lang"):
            self.lang = a["lang"]
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = a["content"].strip()
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split() and a.get("href"):
            self.canonical = self.canonical or a["href"]
        elif tag == "title":
            self._in["title"] = True
        elif tag == "h1" and not self.h1:
            self._in["h1"] = True
        elif tag == "time" and a.get("datetime") and not self.time_dt:
            self.time_dt = a["datetime"]
        elif tag == "script" and "ld+json" in a.get("type", "").lower():
            self._in["ld"] = True
            self._ld_buf = []
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "article":
            self._article += 1
        elif tag == "main":
            self._main += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in["title"] = False
        elif tag == "h1":
            self._in["h1"] = False
        elif tag == "script" and self._in["ld"]:
            self._in["ld"] = False
            try:
                self.ld.append(json.loads("".join(self._ld_buf)))
            except ValueError:
                pass
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "article" and self._article:
            self._article -= 1
        elif tag == "main" and self._main:
            self._main -= 1

    def handle_data(self, data):
        if self._in["ld"]:
            self._ld_buf.append(data)
            return
        if self._in["title"]:
            self.title += data
        if self._in["h1"]:
            self.h1 += data
        if self._skip:
            return
        text = data.strip()
        if not text:
            return
        self._buf["body"].append(text)
        if self._main:
            self._buf["main"].append(text)
        if self._article:
            self._buf["article"].append(text)

    def excerpt(self, limit: int = 1200) -> str:  # limit is set by fetch --chars
        for name in ("article", "main", "body"):
            text = re.sub(r"\s+", " ", " ".join(self._buf[name])).strip()
            if len(text) >= 200 or name == "body":
                return text[:limit]
        return ""


def _walk_ld(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk_ld(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_ld(v)


def _author_names(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [value["name"]] if isinstance(value.get("name"), str) else []
    if isinstance(value, list):
        return [n for v in value for n in _author_names(v)]
    return []


def _iso_day(value) -> str | None:
    m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError:
        return None


def extract_meta(html: str, url: str, excerpt_chars: int = 1200) -> dict:
    p = PageParser()
    p.feed(html)
    m = p.meta
    ld_nodes = [n for doc in p.ld for n in _walk_ld(doc)]

    published = next((d for k in PUBLISHED_METAS if (d := _iso_day(m.get(k)))), None)
    published = published or next((d for n in ld_nodes if (d := _iso_day(n.get("datePublished")))), None)
    published = published or _iso_day(p.time_dt)

    authors: list[str] = []
    if m.get("author"):
        authors.append(m["author"])
    authors += [n for node in ld_nodes for n in _author_names(node.get("author"))]
    authors = [a for a in dict.fromkeys(authors) if not a.startswith("http")]

    ld_headline = next((n["headline"] for n in ld_nodes if isinstance(n.get("headline"), str)), "")
    title = m.get("og:title") or m.get("twitter:title") or ld_headline or p.title or p.h1
    site = m.get("og:site_name") or next(
        (n["publisher"]["name"] for n in ld_nodes
         if isinstance(n.get("publisher"), dict) and isinstance(n["publisher"].get("name"), str)), "")
    desc = m.get("og:description") or m.get("description") or m.get("twitter:description") or ""
    canonical = p.canonical or m.get("og:url")
    if canonical:
        canonical = urllib.parse.urljoin(url, canonical)

    return {
        "url": url,
        "canonical": canonical,
        "title": re.sub(r"\s+", " ", title).strip(),
        "site_name": site.strip() or site_of(url),
        "author": authors[0] if authors else None,
        "published": published,
        "description": re.sub(r"\s+", " ", desc).strip(),
        "lang": (p.lang or m.get("og:locale") or "").split("-")[0].split("_")[0].lower() or None,
        "excerpt": p.excerpt(excerpt_chars),
    }


def decode_body(raw: bytes, content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I) or re.search(rb"charset=[\"']?([\w-]+)", raw[:4096], re.I)
    enc = m.group(1) if m and isinstance(m.group(1), str) else (m.group(1).decode("ascii") if m else "utf-8")
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def fetch_page(url: str, timeout: int = 20, max_bytes: int = 3_000_000, excerpt_chars: int = 1200) -> dict:
    url = normalize_url(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ja,en;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes)
            final = resp.geturl()
            ctype = resp.headers.get("Content-Type", "")
            status = resp.status
    except urllib.error.HTTPError as e:
        return {"url": url, "error": f"HTTP {e.code}", "status": e.code}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"url": url, "error": f"{type(e).__name__}: {e}"}
    if "html" not in ctype.lower():
        return {"url": final, "error": f"not an HTML page ({ctype or 'unknown type'})", "status": status}
    info = extract_meta(decode_body(raw, ctype), normalize_url(final), excerpt_chars)
    info["status"] = status
    if not info["title"] or len(info["excerpt"]) < 200:
        info["note"] = "thin page (likely client-rendered or blocked): read the article with a web fetch tool instead"
    return info


# ---------------------------------------------------------------- commands

def read_input(path: str | None):
    raw = Path(path).read_bytes() if path else sys.stdin.buffer.read()
    return json.loads(raw.decode("utf-8-sig"))


def cmd_fetch(args) -> int:
    info = fetch_page(args.url, excerpt_chars=max(200, min(args.chars, 20000)))
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 1 if "error" in info else 0


def http_get(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def find_candidates(query: str) -> list[dict]:
    """Look a title up on the sites' own search: Hatena Bookmark (any site), note, Qiita."""
    import html as htmllib
    q = urllib.parse.quote(query)
    found: list[dict] = []

    def hatena():
        text = http_get(f"https://b.hatena.ne.jp/search/text?mode=rss&sort=popular&q={q}")
        for item in re.findall(r"<item[^>]*>(.*?)</item>", text, re.S):
            title, link = re.search(r"<title>(.*?)</title>", item, re.S), re.search(r"<link>(.*?)</link>", item, re.S)
            if title and link:
                yield htmllib.unescape(title.group(1)), link.group(1)

    def note():
        data = json.loads(http_get(f"https://note.com/api/v3/searches?context=note&q={q}&size=5"))
        for n in data["data"]["notes"]["contents"]:
            yield n.get("name") or "", f"https://note.com/{n['user']['urlname']}/n/{n['key']}"

    def qiita():
        for i in json.loads(http_get(f"https://qiita.com/api/v2/items?per_page=5&query={q}")):
            yield i["title"], i["url"]

    for source, fn in (("hatena", hatena), ("note", note), ("qiita", qiita)):
        try:
            found += [{"source": source, "title": t, "url": u} for t, u in list(fn())[:5]]
        except Exception as e:  # one source being down must not hide the others
            found.append({"source": source, "error": f"{type(e).__name__}: {e}"})
    return found


def cmd_find(args) -> int:
    print(json.dumps(find_candidates(args.query), ensure_ascii=False, indent=2))
    return 0


def cmd_add(args) -> int:
    raw = read_input(args.file)
    raws = raw if isinstance(raw, list) else [raw]
    categories, aliases = load_categories(), load_aliases()
    items = load_articles()
    by_key = {dedupe_key(a["url"]): i for i, a in enumerate(items)}

    added, updated, duplicates, errors = [], [], [], []
    staged: list[tuple[dict, dict]] = []
    for r in raws:
        entry, errs = build_entry(r, categories, aliases)
        if errs:
            errors.append({"url": r.get("url"), "errors": errs})
        else:
            staged.append((entry, r))
    if errors:
        print(json.dumps({"added": [], "updated": [], "duplicates": [], "errors": errors}, ensure_ascii=False, indent=2))
        return 1

    for entry, r in staged:
        key = dedupe_key(entry["url"])
        if key in by_key:
            old = items[by_key[key]]
            if args.update:
                # Fields the input leaves out keep their stored value.
                if not r.get("read"):
                    entry["read"] = old["read"]
                if "points" not in r:
                    entry["points"] = old.get("points", [])
                if not r.get("archived"):
                    entry["archived"] = old.get("archived")
                items[by_key[key]] = entry
                updated.append(entry)
            else:
                duplicates.append(old)
        else:
            items.insert(0, entry)
            by_key = {dedupe_key(a["url"]): i for i, a in enumerate(items)}
            added.append(entry)

    if added or updated:
        write_json(articles_path(), sort_articles(items))
    print(json.dumps({"added": added, "updated": updated, "duplicates": duplicates, "errors": [], "total": len(items)},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_tags(args) -> int:
    items = load_articles()
    cats = load_categories()
    cat_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    for a in items:
        cat_counts[a["category"]] = cat_counts.get(a["category"], 0) + 1
        for t in a["tags"]:
            tag_counts[t] = tag_counts.get(t, 0) + 1
    tags = sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    if args.json:
        print(json.dumps({
            "total": len(items),
            "categories": {c["id"]: cat_counts.get(c["id"], 0) for c in cats},
            "tags": dict(tags),
        }, ensure_ascii=False, indent=2))
        return 0
    print(f"articles: {len(items)}")
    print("categories:", ", ".join(f"{c['id']}({cat_counts.get(c['id'], 0)})" for c in cats))
    print("tags:", ", ".join(f"{t}({n})" for t, n in tags) or "(none yet)")
    return 0


def cmd_categories(_args) -> int:
    for c in load_categories():
        print(f"{c['id']:<13} {c['label']}  {c.get('hint', '')}")
    return 0


def cmd_remove(args) -> int:
    items = load_articles()
    target = args.target.strip()
    try:
        key = dedupe_key(target)
    except ValueError:
        key = None
    keep = [a for a in items if a["id"] != target and (key is None or dedupe_key(a["url"]) != key)]
    if len(keep) == len(items):
        print(json.dumps({"removed": None, "error": f"no article matches {target!r}"}, ensure_ascii=False))
        return 1
    removed = [a for a in items if a not in keep]
    write_json(articles_path(), keep)
    print(json.dumps({"removed": removed, "total": len(keep)}, ensure_ascii=False, indent=2))
    return 0


def find_article(items: list[dict], target: str) -> int | None:
    target = target.strip()
    for i, a in enumerate(items):
        if a["id"] == target:
            return i
    try:
        key = dedupe_key(target)
    except ValueError:
        return None
    for i, a in enumerate(items):
        if dedupe_key(a["url"]) == key:
            return i
    return None


def cmd_edit(args) -> int:
    """Merge each patch (an object with id or url, plus the fields to change) into the stored article."""
    raw = read_input(args.file)
    patches = raw if isinstance(raw, list) else [raw]
    categories, aliases = load_categories(), load_aliases()
    items = load_articles()
    updated, errors = [], []
    for patch in patches:
        target = patch.get("id") or patch.get("url") or ""
        i = find_article(items, target)
        if i is None:
            errors.append({"target": target, "errors": ["no article matches"]})
            continue
        merged = dict(items[i])
        merged.update({k: v for k, v in patch.items() if k not in ("id", "url")})
        entry, errs = build_entry(merged, categories, aliases)
        if errs:
            errors.append({"target": target, "errors": errs})
        else:
            items[i] = entry
            updated.append(entry)
    if errors:
        print(json.dumps({"updated": [], "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    write_json(articles_path(), sort_articles(items))
    print(json.dumps({"updated": [{"id": e["id"], "title": e["title"]} for e in updated], "errors": []},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_normalize(_args) -> int:
    categories, aliases = load_categories(), load_aliases()
    items = load_articles()
    out, errors = [], []
    for a in items:
        entry, errs = build_entry(a, categories, aliases)
        if errs:
            errors.append({"url": a.get("url"), "errors": errs})
        else:
            out.append(entry)
    if errors:
        print(json.dumps({"errors": errors}, ensure_ascii=False, indent=2))
        return 1
    out = sort_articles(out)
    changed = out != items or any(list(a.keys()) != FIELD_ORDER for a in items)
    if changed:
        write_json(articles_path(), out)
    print(json.dumps({"rewritten": changed, "total": len(out)}, ensure_ascii=False))
    return 0


def request_archive(url: str, timeout: int = 120) -> tuple[bool, str]:
    """Ask the Wayback Machine (Save Page Now) to capture the URL."""
    req = urllib.request.Request(ARCHIVE_SAVE + url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return False, type(e).__name__


def cmd_archive(args) -> int:
    items = load_articles()
    if args.targets:
        idx = []
        for t in args.targets:
            i = find_article(items, t)
            if i is None:
                print(json.dumps({"error": f"no article matches {t!r}"}, ensure_ascii=False))
                return 1
            idx.append(i)
    else:
        idx = [i for i, a in enumerate(items) if not a.get("archived")]
    archived, failed = [], []
    for n, i in enumerate(idx):
        if n:
            time.sleep(args.delay)
        ok, reason = request_archive(items[i]["url"], args.timeout)
        if ok:
            items[i]["archived"] = today()
            archived.append(items[i]["url"])
        else:
            failed.append({"url": items[i]["url"], "reason": reason})
    if archived:
        # Archiving takes minutes: reload so articles added meanwhile are not overwritten.
        fresh = load_articles()
        done = set(archived)
        for a in fresh:
            if a["url"] in done:
                a["archived"] = today()
        write_json(articles_path(), fresh)
    print(json.dumps({"archived": archived, "failed": failed}, ensure_ascii=False, indent=2))
    return 0


DEAD_STATUSES = {404, 410}


def probe(url: str, timeout: int) -> str | int:
    """HTTP status of the URL, or the error name when no answer came."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return type(e).__name__


def cmd_check_links(args) -> int:
    from concurrent.futures import ThreadPoolExecutor
    items = load_articles()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda a: probe(a["url"], args.timeout), items))
    dead = [{"url": a["url"], "status": r} for a, r in zip(items, results) if r in DEAD_STATUSES]
    # 403, 429, 5xx and timeouts are often bot blocking, so they are listed but do not fail the run.
    warn = [{"url": a["url"], "status": r} for a, r in zip(items, results)
            if r not in DEAD_STATUSES and not (isinstance(r, int) and 200 <= r < 400)]
    print(json.dumps({"checked": len(items), "dead": dead, "warn": warn}, ensure_ascii=False, indent=2))
    return 1 if dead else 0


def cmd_validate(_args) -> int:
    try:
        items = load_articles()
        cats, aliases = load_categories(), load_aliases()
    except ValueError as e:
        print(f"invalid JSON: {e}")
        return 1
    problems = check_stored(items, cats, aliases)
    ids = [c["id"] for c in cats]
    if len(ids) != len(set(ids)):
        problems.append("categories.json has duplicate ids")
    for p in problems:
        print(p)
    print(f"{len(items)} articles, {len(problems)} problems")
    return 1 if problems else 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("fetch"); s.add_argument("url"); s.add_argument("--chars", type=int, default=1200); s.set_defaults(fn=cmd_fetch)
    s = sub.add_parser("find"); s.add_argument("query"); s.set_defaults(fn=cmd_find)
    s = sub.add_parser("add"); s.add_argument("--file"); s.add_argument("--update", action="store_true"); s.set_defaults(fn=cmd_add)
    s = sub.add_parser("edit"); s.add_argument("--file"); s.set_defaults(fn=cmd_edit)
    s = sub.add_parser("archive"); s.add_argument("targets", nargs="*")
    s.add_argument("--delay", type=float, default=5.0); s.add_argument("--timeout", type=int, default=120); s.set_defaults(fn=cmd_archive)
    s = sub.add_parser("check-links"); s.add_argument("--timeout", type=int, default=20); s.set_defaults(fn=cmd_check_links)
    s = sub.add_parser("normalize"); s.set_defaults(fn=cmd_normalize)
    s = sub.add_parser("tags"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_tags)
    s = sub.add_parser("categories"); s.set_defaults(fn=cmd_categories)
    s = sub.add_parser("remove"); s.add_argument("target"); s.set_defaults(fn=cmd_remove)
    s = sub.add_parser("validate"); s.set_defaults(fn=cmd_validate)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
