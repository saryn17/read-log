import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import readlog  # noqa: E402

CATS = json.loads((ROOT / "site/data/categories.json").read_text(encoding="utf-8"))
ALIASES = {"k8s": "kubernetes"}

SAMPLE_HTML = """<!doctype html><html lang="ja"><head>
<title>fallback title</title>
<meta property="og:title" content="Kubernetes の Pod が Pending のままになる理由">
<meta property="og:site_name" content="Zenn">
<meta property="og:description" content="スケジューラの挙動を追う">
<meta name="author" content="https://example.com/profile">
<link rel="canonical" href="/articles/abc">
<script type="application/ld+json">{"@graph":[{"@type":"Article","datePublished":"2025-03-04T10:00:00+09:00","author":{"name":"Taro"}}]}</script>
</head><body><nav>menu menu</nav><article><h1>見出し</h1><p>%s</p></article><footer>foot</footer></body></html>""" % ("本文。" * 100)


def run(argv, stdin=None):
    out = io.StringIO()
    with redirect_stdout(out):
        if stdin is not None:
            with mock.patch("sys.stdin", mock.Mock(buffer=io.BytesIO(stdin.encode("utf-8")))):
                code = readlog.main(argv)
        else:
            code = readlog.main(argv)
    return code, out.getvalue()


class UrlTests(unittest.TestCase):
    def test_same_article_gets_same_key(self):
        variants = [
            "https://example.com/a/b",
            "https://example.com/a/b/",
            "http://www.example.com/a/b?utm_source=x&fbclid=1#top",
            "HTTPS://EXAMPLE.COM:443/a/b",
        ]
        self.assertEqual(len({readlog.dedupe_key(u) for u in variants}), 1)
        self.assertEqual(len({readlog.article_id(u) for u in variants}), 1)

    def test_real_query_params_are_kept(self):
        self.assertNotEqual(readlog.dedupe_key("https://e.com/p?id=1"), readlog.dedupe_key("https://e.com/p?id=2"))
        self.assertEqual(readlog.dedupe_key("https://e.com/p?a=1&b=2"), readlog.dedupe_key("https://e.com/p?b=2&a=1"))

    def test_rejects_non_http(self):
        for bad in ["ftp://e.com/a", "javascript:alert(1)", "example.com/a", ""]:
            with self.assertRaises(ValueError):
                readlog.normalize_url(bad)


class EntryTests(unittest.TestCase):
    def good(self, **kw):
        raw = {"url": "https://zenn.dev/a/b", "title": "  Pod が\n Pending  ", "category": "infra",
               "tags": ["K8s", "#スケジューラ", "kubernetes"], "summary": "原因を調べる", "published": "2025-03-04"}
        raw.update(kw)
        return readlog.build_entry(raw, CATS, ALIASES)

    def test_normalizes(self):
        e, errs = self.good()
        self.assertEqual(errs, [])
        self.assertEqual(e["title"], "Pod が Pending")
        self.assertEqual(e["tags"], ["kubernetes", "スケジューラ"])
        self.assertEqual(e["site"], "zenn.dev")
        self.assertEqual(e["lang"], "ja")
        self.assertEqual(list(e), readlog.FIELD_ORDER)

    def test_rejections(self):
        cases = {
            "category": self.good(category="nope"),
            "tags": self.good(tags=[]),
            "summary": self.good(summary=" "),
            "title": self.good(title=""),
            "published": self.good(published="2025/03/04"),
            "future": self.good(read="2999-01-01"),
        }
        for name, (e, errs) in cases.items():
            self.assertIsNone(e, name)
            self.assertTrue(errs, name)

    def test_too_many_tags(self):
        e, errs = self.good(tags=[f"t{i}" for i in range(9)])
        self.assertIsNone(e)


class ParseTests(unittest.TestCase):
    def test_extract(self):
        m = readlog.extract_meta(SAMPLE_HTML, "https://zenn.dev/articles/abc")
        self.assertEqual(m["title"], "Kubernetes の Pod が Pending のままになる理由")
        self.assertEqual(m["site_name"], "Zenn")
        self.assertEqual(m["published"], "2025-03-04")
        self.assertEqual(m["author"], "Taro")  # the profile URL in <meta name=author> is skipped
        self.assertEqual(m["canonical"], "https://zenn.dev/articles/abc")
        self.assertEqual(m["lang"], "ja")
        self.assertTrue(m["excerpt"].startswith("見出し"))
        self.assertNotIn("menu", m["excerpt"])
        self.assertNotIn("foot", m["excerpt"])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(ROOT / "site/data/categories.json", self.tmp)
        shutil.copy(ROOT / "site/data/tag_aliases.json", self.tmp)
        (self.tmp / "articles.json").write_text("[]\n", encoding="utf-8")
        p = mock.patch.object(readlog, "DATA_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def add(self, url, read, **kw):
        raw = {"url": url, "title": "t " + url, "category": "cloud", "tags": ["azure"], "summary": "s", "read": read}
        raw.update(kw)
        return run(["add"], json.dumps(raw))

    def stored(self):
        return json.loads((self.tmp / "articles.json").read_text(encoding="utf-8"))

    def test_add_sorts_newest_first_and_validates(self):
        self.add("https://a.com/1", "2025-01-01")
        self.add("https://a.com/3", "2025-03-01")
        self.add("https://a.com/2", "2025-02-01")
        self.assertEqual([a["read"] for a in self.stored()], ["2025-03-01", "2025-02-01", "2025-01-01"])
        code, _ = run(["validate"])
        self.assertEqual(code, 0)

    def test_duplicate_is_not_added(self):
        self.add("https://a.com/1", "2025-01-01")
        code, out = self.add("https://www.a.com/1/?utm_source=x", "2025-02-01")
        res = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(len(res["duplicates"]), 1)
        self.assertEqual(len(self.stored()), 1)
        self.assertEqual(self.stored()[0]["read"], "2025-01-01")

    def test_update_keeps_original_read_date(self):
        self.add("https://a.com/1", "2025-01-01")
        raw = {"url": "https://a.com/1", "title": "new", "category": "cloud", "tags": ["x"], "summary": "s"}
        code, out = run(["add", "--update"], json.dumps(raw))
        self.assertEqual(len(json.loads(out)["updated"]), 1)
        self.assertEqual(self.stored()[0]["title"], "new")
        self.assertEqual(self.stored()[0]["read"], "2025-01-01")

    def test_batch_with_one_error_writes_nothing(self):
        good = {"url": "https://a.com/1", "title": "t", "category": "cloud", "tags": ["x"], "summary": "s"}
        bad = dict(good, url="https://a.com/2", category="nope")
        code, out = run(["add"], json.dumps([good, bad]))
        self.assertEqual(code, 1)
        self.assertEqual(self.stored(), [])

    def test_validate_catches_unsorted_and_duplicates(self):
        self.add("https://a.com/1", "2025-01-01")
        self.add("https://a.com/2", "2025-02-01")
        items = self.stored()
        (self.tmp / "articles.json").write_text(json.dumps(items[::-1]), encoding="utf-8")
        code, out = run(["validate"])
        self.assertEqual(code, 1)
        self.assertIn("not sorted", out)
        (self.tmp / "articles.json").write_text(json.dumps([items[0], items[0]]), encoding="utf-8")
        code, out = run(["validate"])
        self.assertEqual(code, 1)
        self.assertIn("duplicates", out)

    def test_remove_by_id_and_url(self):
        self.add("https://a.com/1", "2025-01-01")
        self.add("https://a.com/2", "2025-02-01")
        first = self.stored()[1]
        code, _ = run(["remove", first["id"]])
        self.assertEqual(code, 0)
        code, _ = run(["remove", "https://a.com/2/"])
        self.assertEqual(code, 0)
        self.assertEqual(self.stored(), [])
        code, _ = run(["remove", "https://a.com/none"])
        self.assertEqual(code, 1)

    def test_file_is_utf8_lf(self):
        self.add("https://a.com/1", "2025-01-01", title="日本語タイトル")
        raw = (self.tmp / "articles.json").read_bytes()
        self.assertIn("日本語タイトル".encode("utf-8"), raw)
        self.assertNotIn(b"\r\n", raw)


if __name__ == "__main__":
    unittest.main()
