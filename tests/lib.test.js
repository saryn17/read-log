const test = require('node:test');
const assert = require('node:assert/strict');
const L = require('../site/lib.js');

const CATS = [
  { id: 'cloud', label: 'クラウド' },
  { id: 'infra', label: 'インフラ・運用' },
  { id: 'ai', label: 'AI・LLM' },
];
const mk = (o) => Object.assign({ author: null, published: null, lang: 'ja', summary: '', tags: [] }, o);
const DATA = L.prepare([
  mk({ id: '1', url: 'https://zenn.dev/a', title: 'Kubernetes の Pod が Pending になる理由', site: 'zenn.dev', read: '2025-03-04', category: 'infra', tags: ['kubernetes', 'スケジューラ'], summary: 'スケジューラの挙動を追う' }),
  mk({ id: '2', url: 'https://qiita.com/b', title: 'Azure Functions のコールドスタート', site: 'qiita.com', read: '2025-02-10', published: '2025-01-05', category: 'cloud', tags: ['azure', 'serverless'], summary: '起動時間の内訳' }),
  mk({ id: '3', url: 'https://www.cloudflare.com/c', title: 'Building agents on Workers', site: 'cloudflare.com', read: '2024-12-24', published: '2024-12-01', category: 'ai', tags: ['cloudflare', 'agents', 'ネットワーク'], summary: 'Durable Objects based agents', lang: 'en' }),
], CATS);
const ids = (q, skip) => L.sortItems(L.filterItems(DATA, L.compile(q), skip), 'read-desc').map((x) => x.a.id);

test('operators and plain terms', () => {
  assert.deepEqual(ids(''), ['1', '2', '3']);
  assert.deepEqual(ids('tag:kubernetes'), ['1']);
  assert.deepEqual(ids('cat:cloud,ai'), ['2', '3']);
  assert.deepEqual(ids('cat:クラウド'), ['2']);
  assert.deepEqual(ids('site:cloudflare'), ['3']);
  assert.deepEqual(ids('lang:en'), ['3']);
  assert.deepEqual(ids('read:2025'), ['1', '2']);
  assert.deepEqual(ids('read:2025-02'), ['2']);
  assert.deepEqual(ids('pub:2024'), ['3']);
});

test('tag operators: AND across tokens, OR inside a comma list, negation', () => {
  assert.deepEqual(ids('tag:azure tag:serverless'), ['2']);
  assert.deepEqual(ids('tag:azure tag:kubernetes'), []);
  assert.deepEqual(ids('tag:azure,kubernetes'), ['1', '2']);
  assert.deepEqual(ids('-tag:azure'), ['1', '3']);
});

test('text terms: AND, negation, quotes, kana folding', () => {
  assert.deepEqual(ids('pod pending'), ['1']);
  assert.deepEqual(ids('pod 起動'), []);
  assert.deepEqual(ids('-pod'), ['2', '3']);
  assert.deepEqual(ids('"Pod が Pending"'), ['1']);
  assert.deepEqual(ids('すけじゅーらー'), []);
  assert.deepEqual(ids('ねっとわーく'), ['3']); // tag stored as katakana, query in hiragana
  assert.deepEqual(ids('ネットワーク'), ['3']);
});

const KDATA = L.prepare([
  mk({ id: 'k1', url: 'https://x/1', title: 'Title one', site: 'x', read: '2025-03-01', category: 'ai', tags: [], summary: 'about agents', points: ['uses a boot_id check every 5 minutes', '消えるのは /tmp の中身'] }),
  mk({ id: 'k2', url: 'https://speakerdeck.com/2', title: 'Slide two', site: 'speakerdeck.com', read: '2025-02-01', category: 'ai', tags: [], summary: 'deck', kind: 'slide', points: [] }),
  mk({ id: 'k3', url: 'https://x/3', title: 'Book three', site: 'x', read: '2025-01-01', category: 'ai', tags: [], summary: 'sample', kind: 'book' }),
], CATS);
const kids = (q, skip) => L.sortItems(L.filterItems(KDATA, L.compile(q), skip), 'read-desc').map((x) => x.a.id);

test('kind: operator filters by id or label; missing kind counts as article', () => {
  assert.deepEqual(kids('kind:article'), ['k1']);
  assert.deepEqual(kids('kind:slide'), ['k2']);
  assert.deepEqual(kids('kind:スライド'), ['k2']);
  assert.deepEqual(kids('kind:slide,book'), ['k2', 'k3']);
  assert.deepEqual(kids('-kind:slide'), ['k1', 'k3']);
  assert.deepEqual(kids('kind:video'), []);
  assert.deepEqual([L.KIND_LABEL.tool, L.KIND_LABEL.page], ['ツール', 'サイト']);
  assert.deepEqual(kids('kind:slide', 'kind'), ['k1', 'k2', 'k3']); // facet counts ignore their own group
  assert.equal(KDATA[0].kindLabel, '記事');
  assert.deepEqual(KDATA[0].points.length, 2);
  assert.deepEqual(KDATA[2].points, []); // missing points become an empty list
});

test('key points are searched, rank below the summary, and are excluded by negation', () => {
  assert.deepEqual(kids('boot_id'), ['k1']);
  assert.deepEqual(kids('/tmp'), ['k1']);
  assert.deepEqual(kids('-boot_id'), ['k2', 'k3']);
  const data = L.prepare([
    mk({ id: 'p', url: 'https://x/p', title: 'a', site: 'x', read: '2025-05-01', category: 'ai', tags: [], summary: '', points: ['mentions kafka'] }),
    mk({ id: 's', url: 'https://x/s', title: 'b', site: 'x', read: '2025-01-01', category: 'ai', tags: [], summary: 'mentions kafka', points: [] }),
  ], CATS);
  assert.deepEqual(L.sortItems(L.filterItems(data, L.compile('kafka')), 'relevance').map((x) => x.a.id), ['s', 'p']);
});

test('pointMatches returns only points that explain a match hidden from title and summary', () => {
  const a = KDATA[0];
  assert.deepEqual(L.pointMatches(a, L.compile('boot_id').terms), ['uses a boot_id check every 5 minutes']);
  assert.deepEqual(L.pointMatches(a, L.compile('agents').terms), []); // the summary already shows it
  assert.deepEqual(L.pointMatches(a, L.compile('-boot_id').terms), []);
  assert.deepEqual(L.pointMatches(a, L.compile('zzz').terms), []);
  assert.deepEqual(L.pointMatches(a, []), []);
});

test('suggestions include kinds', () => {
  const index = { tags: new Map(), cats: [], sites: new Map(), kinds: [{ id: 'slide', label: 'スライド', n: 2 }, { id: 'article', label: '記事', n: 5 }] };
  assert.deepEqual(L.suggest('kind:sl', index).map((s) => s.v), ['slide']);
  assert.deepEqual(L.suggest('kind:ス', index).map((s) => s.v), ['slide']);
  assert.deepEqual(L.suggest('kind:', index).map((s) => s.v), ['article', 'slide']); // most articles first
});

test('fullwidth and case are normalized', () => {
  assert.deepEqual(ids('ＫＵＢＥＲＮＥＴＥＳ'), ['1']);
  assert.deepEqual(ids('AZURE'), ['2']);
});

test('typo tolerance only for ASCII words of 4+ chars, and not for short words', () => {
  assert.deepEqual(ids('kubernets'), ['1']);
  assert.deepEqual(ids('kubenetes'), ['1']);
  assert.deepEqual(ids('azur'), ['2']); // substring anyway
  assert.deepEqual(ids('zzzz'), []);
  assert.deepEqual(ids('azr'), []); // 3 chars: no fuzzy, and not a substring
});

test('relevance: title hit outranks summary hit', () => {
  const data = L.prepare([
    mk({ id: 'a', url: 'https://x/a', title: 'unrelated', site: 'x', read: '2025-05-01', category: 'ai', tags: [], summary: 'mentions agents once' }),
    mk({ id: 'b', url: 'https://x/b', title: 'Agents in practice', site: 'x', read: '2025-01-01', category: 'ai', tags: [], summary: '' }),
  ], CATS);
  const r = L.sortItems(L.filterItems(data, L.compile('agents')), 'relevance').map((x) => x.a.id);
  assert.deepEqual(r, ['b', 'a']);
});

test('facet counts skip their own group', () => {
  const cq = L.compile('cat:cloud');
  assert.equal(L.filterItems(DATA, cq).length, 1);
  assert.equal(L.filterItems(DATA, cq, 'cat').length, 3);
  const tags = L.countBy(L.filterItems(DATA, L.compile('tag:azure'), 'tag'), (a) => a.tags);
  assert.equal(tags.get('kubernetes'), 1);
});

test('sorting: published puts nulls last, title and site sort, read-asc reverses', () => {
  const all = L.filterItems(DATA, L.compile(''));
  assert.deepEqual(L.sortItems(all, 'pub-desc').map((x) => x.a.id), ['2', '3', '1']);
  assert.deepEqual(L.sortItems(all, 'read-asc').map((x) => x.a.id), ['3', '2', '1']);
  assert.deepEqual(L.sortItems(all, 'site').map((x) => x.a.id), ['3', '2', '1']); // cloudflare < qiita < zenn
});

test('grouping keeps taxonomy order for categories and newest-first for months', () => {
  const sorted = L.sortItems(L.filterItems(DATA, L.compile('')), 'read-desc');
  assert.deepEqual(L.groupItems(sorted, 'category', CATS).map((g) => g.key), ['cloud', 'infra', 'ai']);
  assert.deepEqual(L.groupItems(sorted, 'month', CATS).map((g) => g.key), ['2025-03', '2025-02', '2024-12']);
  assert.deepEqual(L.groupItems(sorted, 'month', CATS, 'read-asc').map((g) => g.key), ['2024-12', '2025-02', '2025-03']);
  assert.equal(L.groupItems(sorted, 'none', CATS).length, 1);
});

test('splitQuery separates chips from text and joinQuery round-trips', () => {
  const s = L.splitQuery('tag:k8s -cat:ai pod "a b" -x');
  assert.deepEqual(s.chips, ['tag:k8s', '-cat:ai']);
  assert.equal(s.text, 'pod "a b" -x');
  assert.equal(L.joinQuery(s.chips, s.text), 'tag:k8s -cat:ai pod "a b" -x');
});

test('suggestions', () => {
  const index = {
    tags: new Map([['kubernetes', 4], ['kafka', 2], ['azure', 5]]),
    cats: [{ id: 'cloud', label: 'クラウド', n: 3 }],
    sites: new Map([['zenn.dev', 6]]),
  };
  assert.deepEqual(L.suggest('tag:k', index).map((s) => s.v), ['kubernetes', 'kafka']);
  assert.deepEqual(L.suggest('cat:クラ', index).map((s) => s.v), ['cloud']);
  assert.deepEqual(L.suggest('zen', index).map((s) => [s.kind, s.v]), [['site', 'zenn.dev']]);
  assert.deepEqual(L.suggest('', index), []);
});

test('highlight escapes HTML and wraps every match', () => {
  // the term "<b>" is searched literally: it matches "<b>" but not "</b>", and the output is escaped
  const terms = L.compile('pod <b>').terms;
  assert.equal(L.highlight('Pod & <b>x</b> pod', terms), '<mark>Pod</mark> &amp; <mark>&lt;b&gt;</mark>x&lt;/b&gt; <mark>pod</mark>');
  assert.equal(L.highlight('<script>', []), '&lt;script&gt;');
  // a string whose NFKC length differs is returned escaped, never mis-indexed
  assert.equal(L.highlight('㌔ <i>', L.compile('i').terms), '㌔ &lt;i&gt;');
});

test('stats counts this month and fills 12 month buckets', () => {
  const s = L.stats(DATA, new Date(2025, 2, 15));
  assert.equal(s.total, 3);
  assert.equal(s.thisMonth, 1);
  assert.equal(s.thisYear, 2);
  assert.equal(s.months.length, 12);
  assert.equal(s.months[11].key, '2025-03');
  assert.equal(s.months[11].n, 1);
  assert.equal(s.months.find((m) => m.key === '2024-12').n, 1);
  assert.equal(s.tagCount, 7);
});
