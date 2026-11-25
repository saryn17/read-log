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

    def test_kind_is_guessed_from_the_domain_and_can_be_overridden(self):
        cases = {
            "https://speakerdeck.com/a/b": "slide",
            "https://www.youtube.com/watch?v=1": "video",
            "https://arxiv.org/abs/2401.00001": "paper",
            "https://zenn.dev/a/b": "article",
            "https://notspeakerdeck.com/a": "article",  # a longer domain that merely ends the same way
        }
        for url, kind in cases.items():
            e, errs = self.good(url=url)
            self.assertEqual(errs, [], url)
            self.assertEqual(e["kind"], kind, url)
        e, _ = self.good(url="https://speakerdeck.com/a/b", kind="article")
        self.assertEqual(e["kind"], "article")
        e, errs = self.good(kind="podcast")
        self.assertIsNone(e)
        self.assertTrue(any("kind" in m for m in errs))

    def test_tool_and_page_are_accepted_only_when_given(self):
        for kind in ("tool", "page"):
            e, errs = self.good(url="https://protocolexplorer.org/", kind=kind)
            self.assertEqual(errs, [])
            self.assertEqual(e["kind"], kind)
        e, _ = self.good(url="https://protocolexplorer.org/")
        self.assertEqual(e["kind"], "article")

    def test_points_are_trimmed_and_limited(self):
        e, errs = self.good(points=["  一つ目\n です ", "", "二つ目"])
        self.assertEqual(errs, [])
        self.assertEqual(e["points"], ["一つ目 です", "二つ目"])
        self.assertEqual(self.good()[0]["points"], [])
        for bad in (["a", "b", "c", "d"], ["あ" * 81], "not a list"):
            e, errs = self.good(points=bad)
            self.assertIsNone(e, bad)
            self.assertTrue(errs)

    def test_archived_must_be_a_date(self):
        self.assertEqual(self.good(archived="2026-11-20")[0]["archived"], "2026-11-20")
        self.assertIsNone(self.good()[0]["archived"])
        e, errs = self.good(archived="yesterday")
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

    def test_excerpt_length_follows_chars(self):
        full = readlog.extract_meta(SAMPLE_HTML, "https://zenn.dev/a")["excerpt"]
        self.assertGreater(len(full), 250)  # the sample body is about 300 chars, under the 1200 default
        self.assertEqual(len(readlog.extract_meta(SAMPLE_HTML, "https://zenn.dev/a", 250)["excerpt"]), 250)


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

    def test_update_keeps_points_and_archived_when_input_omits_them(self):
        self.add("https://a.com/1", "2025-01-01", points=["要点"])
        run(["edit"], json.dumps({"url": "https://a.com/1", "archived": "2026-01-02"}))
        raw = {"url": "https://a.com/1", "title": "new", "category": "cloud", "tags": ["x"], "summary": "s"}
        run(["add", "--update"], json.dumps(raw))
        self.assertEqual(self.stored()[0]["points"], ["要点"])
        self.assertEqual(self.stored()[0]["archived"], "2026-01-02")
        run(["add", "--update"], json.dumps(dict(raw, points=[])))
        self.assertEqual(self.stored()[0]["points"], [])  # an explicit empty list replaces them

    def test_edit_merges_fields_by_id_or_url(self):
        self.add("https://a.com/1", "2025-01-01")
        self.add("https://a.com/2", "2025-02-01")
        first = self.stored()[1]
        code, out = run(["edit"], json.dumps([
            {"id": first["id"], "tags": ["Rust", "#wasm"], "points": ["a"]},
            {"url": "https://www.a.com/2/?utm_source=x", "kind": "slide"},
        ]))
        self.assertEqual(code, 0, out)
        by_url = {a["url"]: a for a in self.stored()}
        self.assertEqual(by_url["https://a.com/1"]["tags"], ["rust", "wasm"])
        self.assertEqual(by_url["https://a.com/1"]["title"], "t https://a.com/1")  # untouched fields stay
        self.assertEqual(by_url["https://a.com/2"]["kind"], "slide")
        self.assertEqual(by_url["https://a.com/2"]["read"], "2025-02-01")
        self.assertEqual(run(["validate"])[0], 0)

    def test_edit_with_one_bad_patch_writes_nothing(self):
        self.add("https://a.com/1", "2025-01-01")
        before = (self.tmp / "articles.json").read_bytes()
        code, out = run(["edit"], json.dumps([
            {"url": "https://a.com/1", "tags": ["ok"]},
            {"url": "https://a.com/1", "category": "nope"},
            {"url": "https://a.com/missing", "tags": ["x"]},
        ]))
        self.assertEqual(code, 1)
        self.assertEqual(len(json.loads(out)["errors"]), 2)
        self.assertEqual((self.tmp / "articles.json").read_bytes(), before)

    def test_normalize_migrates_the_old_shape(self):
        old = {"id": "00000000", "url": "https://speakerdeck.com/a/b", "title": "t", "site": "speakerdeck.com", "author": None,
               "published": None, "read": "2025-01-01", "category": "cloud", "tags": ["x"], "summary": "s", "lang": "ja"}
        old["id"] = readlog.article_id(old["url"])
        (self.tmp / "articles.json").write_text(json.dumps([old]), encoding="utf-8")
        self.assertEqual(run(["validate"])[0], 1)
        code, out = run(["normalize"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["rewritten"])
        a = self.stored()[0]
        self.assertEqual(list(a), readlog.FIELD_ORDER)
        self.assertEqual((a["kind"], a["points"], a["archived"]), ("slide", [], None))
        self.assertEqual(run(["validate"])[0], 0)
        self.assertFalse(json.loads(run(["normalize"])[1])["rewritten"])  # second run changes nothing

    def test_archive_records_only_the_successes(self):
        self.add("https://a.com/1", "2025-01-01")
        self.add("https://a.com/2", "2025-02-01")
        answers = {"https://a.com/1": (True, "HTTP 200"), "https://a.com/2": (False, "HTTP 429")}
        with mock.patch.object(readlog, "request_archive", side_effect=lambda u, t: answers[u]) as req:
            code, out = run(["archive", "--delay", "0"])
        self.assertEqual(code, 0)
        res = json.loads(out)
        self.assertEqual(res["archived"], ["https://a.com/1"])
        self.assertEqual(res["failed"], [{"url": "https://a.com/2", "reason": "HTTP 429"}])
        by_url = {a["url"]: a for a in self.stored()}
        self.assertEqual(by_url["https://a.com/1"]["archived"], readlog.today())
        self.assertIsNone(by_url["https://a.com/2"]["archived"])
        # the next default run asks only for the one that is still missing
        with mock.patch.object(readlog, "request_archive", return_value=(True, "HTTP 200")) as req:
            run(["archive", "--delay", "0"])
        self.assertEqual([c.args[0] for c in req.call_args_list], ["https://a.com/2"])

    def test_archive_keeps_articles_added_while_it_was_running(self):
        self.add("https://a.com/1", "2025-01-01")

        def slow_request(url, timeout):
            self.add("https://a.com/2", "2025-02-01")  # added by another command during the wait
            return True, "HTTP 200"
        with mock.patch.object(readlog, "request_archive", side_effect=slow_request):
            run(["archive", "--delay", "0"])
        by_url = {a["url"]: a for a in self.stored()}
        self.assertEqual(sorted(by_url), ["https://a.com/1", "https://a.com/2"])
        self.assertEqual(by_url["https://a.com/1"]["archived"], readlog.today())
        self.assertIsNone(by_url["https://a.com/2"]["archived"])

    def test_archive_unknown_target_is_an_error(self):
        self.add("https://a.com/1", "2025-01-01")
        with mock.patch.object(readlog, "request_archive") as req:
            code, _ = run(["archive", "https://a.com/nope"])
        self.assertEqual(code, 1)
        req.assert_not_called()

    def test_check_links_fails_only_on_404_and_410(self):
        for n in range(1, 6):
            self.add(f"https://a.com/{n}", f"2025-01-0{n}")
        status = {"https://a.com/1": 200, "https://a.com/2": 404, "https://a.com/3": 403,
                  "https://a.com/4": "TimeoutError", "https://a.com/5": 410}
        with mock.patch.object(readlog, "probe", side_effect=lambda u, t: status[u]):
            code, out = run(["check-links"])
        res = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(sorted(d["url"] for d in res["dead"]), ["https://a.com/2", "https://a.com/5"])
        self.assertEqual(sorted(w["url"] for w in res["warn"]), ["https://a.com/3", "https://a.com/4"])
        with mock.patch.object(readlog, "probe", return_value=200):
            self.assertEqual(run(["check-links"])[0], 0)

    def test_find_reads_each_source_and_survives_one_failing(self):
        def fake(url, timeout=25):
            if "b.hatena.ne.jp" in url:
                return "<rss><item><title>A &amp; B</title><link>https://h.example/1</link></item></rss>"
            if "note.com" in url:
                raise OSError("down")
            return json.dumps([{"title": "Q", "url": "https://qiita.com/u/items/1"}])
        with mock.patch.object(readlog, "http_get", side_effect=fake):
            code, out = run(["find", "タイトル"])
        res = json.loads(out)
        self.assertEqual(code, 0)
        self.assertIn({"source": "hatena", "title": "A & B", "url": "https://h.example/1"}, res)
        self.assertIn({"source": "qiita", "title": "Q", "url": "https://qiita.com/u/items/1"}, res)
        self.assertTrue(any(r["source"] == "note" and "OSError" in r["error"] for r in res))

    def test_file_is_utf8_lf(self):
        self.add("https://a.com/1", "2025-01-01", title="日本語タイトル")
        raw = (self.tmp / "articles.json").read_bytes()
        self.assertIn("日本語タイトル".encode("utf-8"), raw)
        self.assertNotIn(b"\r\n", raw)


if __name__ == "__main__":
    unittest.main()
