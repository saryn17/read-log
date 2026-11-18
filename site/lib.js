/* Pure search / sort / group logic. No DOM access, so it runs in the browser and in Node tests. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ReadLogLib = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const OPS = ['tag', 'cat', 'site', 'lang', 'read', 'pub'];
  const OP_RE = new RegExp('^(' + OPS.join('|') + '):(.+)$', 'i');

  /** NFKC, lowercase, katakana folded to hiragana so ネットワーク and ねっとわーく meet. */
  function norm(s) {
    return String(s == null ? '' : s)
      .normalize('NFKC')
      .toLowerCase()
      .replace(/[ァ-ヶ]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0x60));
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // ------------------------------------------------------------ query

  /** Split a query string into tokens. Handles -negation, "quoted phrases" and key:value operators. */
  function tokenize(str) {
    const out = [];
    const re = /(-?)(?:"([^"]*)"|(\S+))/g;
    let m;
    while ((m = re.exec(String(str || '')))) {
      const neg = m[1] === '-';
      const quoted = m[2] !== undefined;
      const value = quoted ? m[2] : m[3];
      if (!value) continue;
      const op = !quoted && OP_RE.exec(value);
      out.push(op
        ? { kind: 'op', key: op[1].toLowerCase(), value: op[2], neg, raw: (neg ? '-' : '') + op[1].toLowerCase() + ':' + op[2] }
        : { kind: 'term', value, neg, quoted, raw: (neg ? '-' : '') + (quoted ? '"' + value + '"' : value) });
    }
    return out;
  }

  /** chips = operator tokens (shown as pills), text = everything else. */
  function splitQuery(str) {
    const toks = tokenize(str);
    return {
      chips: toks.filter((t) => t.kind === 'op').map((t) => t.raw),
      text: toks.filter((t) => t.kind === 'term').map((t) => t.raw).join(' '),
    };
  }

  function joinQuery(chips, text) {
    return chips.concat(String(text || '').trim() ? [String(text).trim()] : []).join(' ');
  }

  function compile(str) {
    const toks = tokenize(str);
    const cq = { tagGroups: [], notTags: [], cats: [], notCats: [], sites: [], notSites: [], langs: [], reads: [], pubs: [], terms: [] };
    for (const t of toks) {
      if (t.kind === 'term') {
        cq.terms.push({ t: norm(t.value), neg: t.neg, raw: t.value });
        continue;
      }
      const vals = t.value.split(',').map(norm).filter(Boolean);
      if (!vals.length) continue;
      if (t.key === 'tag') (t.neg ? cq.notTags.push(...vals) : cq.tagGroups.push(vals));
      else if (t.key === 'cat') (t.neg ? cq.notCats : cq.cats).push(...vals);
      else if (t.key === 'site') (t.neg ? cq.notSites : cq.sites).push(...vals);
      else if (t.key === 'lang') cq.langs.push(...vals);
      else if (t.key === 'read') cq.reads.push(...vals);
      else if (t.key === 'pub') cq.pubs.push(...vals);
    }
    return cq;
  }

  // ------------------------------------------------------------ data

  function prepare(articles, categories) {
    const labelOf = {};
    for (const c of categories) labelOf[c.id] = c.label;
    return articles.map((a) => {
      const label = labelOf[a.category] || a.category;
      return Object.assign({}, a, {
        tags: a.tags || [],
        _t: norm(a.title),
        _s: norm(a.summary),
        _tags: (a.tags || []).map(norm),
        _site: norm(a.site),
        _auth: norm(a.author || ''),
        _cat: norm(a.category + ' ' + label),
        _catId: norm(a.category),
        _catLabel: norm(label),
        _url: norm(a.url),
        catLabel: label,
      });
    });
  }

  // ------------------------------------------------------------ matching

  function editDistanceWithin(a, b, max) {
    if (Math.abs(a.length - b.length) > max) return false;
    let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
    for (let i = 1; i <= a.length; i++) {
      const cur = [i];
      let best = i;
      for (let j = 1; j <= b.length; j++) {
        cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
        if (cur[j] < best) best = cur[j];
      }
      if (best > max) return false;
      prev = cur;
    }
    return prev[b.length] <= max;
  }

  function fuzzyWord(a, term) {
    if (term.length < 4 || !/^[\x20-\x7e]+$/.test(term)) return false;
    const max = term.length >= 7 ? 2 : 1;
    const words = (a._t + ' ' + a._tags.join(' ') + ' ' + a._site).split(/[^a-z0-9]+/).filter((w) => w.length >= 3);
    return words.some((w) => editDistanceWithin(term, w, max));
  }

  function has(a, term) {
    return a._t.includes(term) || a._s.includes(term) || a._tags.some((g) => g.includes(term)) ||
      a._site.includes(term) || a._auth.includes(term) || a._cat.includes(term) || a._url.includes(term);
  }

  function termScore(a, term) {
    let s = 0;
    const i = a._t.indexOf(term);
    if (i >= 0) s += 10 + (i === 0 || /[^a-z0-9぀-鿿]/.test(a._t[i - 1]) ? 5 : 0);
    for (const g of a._tags) {
      if (g === term) { s += 8; break; }
      if (g.includes(term)) { s += 5; break; }
    }
    if (a._s.includes(term)) s += 3;
    if (a._site.includes(term) || a._auth.includes(term) || a._cat.includes(term)) s += 3;
    if (s === 0 && a._url.includes(term)) s = 1;
    if (s === 0 && fuzzyWord(a, term)) s = 1;
    return s;
  }

  /** Returns -1 when the article does not match, otherwise a relevance score (0 without text terms).
   *  skip = 'tag' | 'cat' | 'site' ignores that group, used to count what a facet would add. */
  function matchArticle(a, cq, skip) {
    if (skip !== 'tag') {
      if (!cq.tagGroups.every((g) => g.some((v) => a._tags.includes(v)))) return -1;
      if (cq.notTags.some((v) => a._tags.includes(v))) return -1;
    }
    if (skip !== 'cat') {
      if (cq.cats.length && !cq.cats.some((v) => a._catId === v || a._catLabel === v)) return -1;
      if (cq.notCats.some((v) => a._catId === v || a._catLabel === v)) return -1;
    }
    if (skip !== 'site') {
      if (cq.sites.length && !cq.sites.some((v) => a._site.includes(v))) return -1;
      if (cq.notSites.some((v) => a._site.includes(v))) return -1;
    }
    if (cq.langs.length && !cq.langs.includes(norm(a.lang))) return -1;
    if (cq.reads.length && !cq.reads.some((v) => a.read.startsWith(v))) return -1;
    if (cq.pubs.length && !cq.pubs.some((v) => (a.published || '').startsWith(v))) return -1;
    let score = 0;
    for (const t of cq.terms) {
      if (t.neg) {
        if (has(a, t.t)) return -1;
      } else {
        const s = termScore(a, t.t);
        if (!s) return -1;
        score += s;
      }
    }
    return score;
  }

  function filterItems(items, cq, skip) {
    const out = [];
    for (const a of items) {
      const score = matchArticle(a, cq, skip);
      if (score >= 0) out.push({ a, score });
    }
    return out;
  }

  // ------------------------------------------------------------ sort / group

  const cmpStr = (x, y) => String(x).localeCompare(String(y), 'ja');
  const byReadDesc = (x, y) => (x.a.read < y.a.read ? 1 : x.a.read > y.a.read ? -1 : 0);

  function sortItems(list, key) {
    const out = list.slice();
    const cmp = {
      relevance: (x, y) => y.score - x.score || byReadDesc(x, y),
      'read-desc': byReadDesc,
      'read-asc': (x, y) => -byReadDesc(x, y),
      'pub-desc': (x, y) => {
        const px = x.a.published || '', py = y.a.published || '';
        if (!px !== !py) return px ? -1 : 1;
        return px < py ? 1 : px > py ? -1 : byReadDesc(x, y);
      },
      title: (x, y) => cmpStr(x.a.title, y.a.title),
      site: (x, y) => cmpStr(x.a.site, y.a.site) || byReadDesc(x, y),
    }[key] || byReadDesc;
    return out.sort(cmp); // Array.prototype.sort is stable
  }

  function groupItems(list, mode, categories, sortKey) {
    if (mode === 'none') return [{ key: '', label: '', items: list }];
    const groups = new Map();
    const keyOf = {
      month: (a) => a.read.slice(0, 7),
      year: (a) => a.read.slice(0, 4),
      category: (a) => a.category,
      site: (a) => a.site,
    }[mode];
    for (const it of list) {
      const k = keyOf(it.a);
      if (!groups.has(k)) groups.set(k, { key: k, label: mode === 'category' ? it.a.catLabel : k, items: [] });
      groups.get(k).items.push(it);
    }
    const arr = Array.from(groups.values());
    if (mode === 'month' || mode === 'year') arr.sort((x, y) => (sortKey === 'read-asc' ? 1 : -1) * cmpStr(x.key, y.key));
    else if (mode === 'category') {
      const order = categories.map((c) => c.id);
      arr.sort((x, y) => order.indexOf(x.key) - order.indexOf(y.key));
    } else arr.sort((x, y) => y.items.length - x.items.length || cmpStr(x.key, y.key));
    return arr;
  }

  // ------------------------------------------------------------ facets / stats / suggestions

  function countBy(entries, pick) {
    const m = new Map();
    for (const { a } of entries) for (const k of pick(a)) m.set(k, (m.get(k) || 0) + 1);
    return m;
  }

  function stats(items, now) {
    const d = now || new Date();
    const ym = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
    const months = [];
    for (let i = 11; i >= 0; i--) {
      const t = new Date(d.getFullYear(), d.getMonth() - i, 1);
      months.push({ key: t.getFullYear() + '-' + String(t.getMonth() + 1).padStart(2, '0'), n: 0 });
    }
    for (const a of items) {
      const m = months.find((x) => x.key === a.read.slice(0, 7));
      if (m) m.n++;
    }
    const tags = new Set();
    items.forEach((a) => a.tags.forEach((t) => tags.add(t)));
    return {
      total: items.length,
      thisMonth: items.filter((a) => a.read.startsWith(ym)).length,
      thisYear: items.filter((a) => a.read.startsWith(String(d.getFullYear()))).length,
      tagCount: tags.size,
      months,
      currentMonth: ym,
      latest: items.reduce((m, a) => (a.read > m ? a.read : m), ''),
    };
  }

  /** index = { tags: Map(tag -> n), cats: [{id,label,n}], sites: Map(site -> n) } */
  function suggest(token, index, limit) {
    limit = limit || 8;
    const m = /^(-?)(tag|cat|site|lang):(.*)$/i.exec(token);
    const byCount = (x, y) => y.n - x.n || cmpStr(x.v, y.v);
    const rank = (list, q) => {
      const starts = list.filter((c) => norm(c.v).startsWith(q) || (c.alt && norm(c.alt).startsWith(q)));
      const rest = list.filter((c) => !starts.includes(c) && (norm(c.v).includes(q) || (c.alt && norm(c.alt).includes(q))));
      return starts.sort(byCount).concat(rest.sort(byCount));
    };
    const all = () => [
      ...Array.from(index.tags, ([v, n]) => ({ kind: 'tag', v, n })),
      ...index.cats.map((c) => ({ kind: 'cat', v: c.id, alt: c.label, n: c.n })),
      ...Array.from(index.sites, ([v, n]) => ({ kind: 'site', v, n })),
    ];
    let list;
    if (m) {
      const kind = m[2].toLowerCase();
      list = rank(all().filter((c) => c.kind === kind), norm(m[3]));
      return list.slice(0, limit).map((c) => Object.assign({ neg: m[1] === '-' }, c));
    }
    const q = norm(token.replace(/^-/, ''));
    if (!q) return [];
    return rank(all(), q).slice(0, limit).map((c) => Object.assign({ neg: token.startsWith('-') }, c));
  }

  /** HTML-escape text and wrap matches of any positive term in <mark>. Falls back to plain text when
   *  normalization changes the string length (indices would no longer line up). */
  function highlight(text, terms) {
    text = String(text == null ? '' : text);
    const n = norm(text);
    const needles = terms.filter((t) => !t.neg && t.t).map((t) => t.t);
    if (n.length !== text.length || !needles.length) return esc(text);
    const ranges = [];
    for (const needle of needles) {
      let i = 0;
      while ((i = n.indexOf(needle, i)) >= 0) { ranges.push([i, i + needle.length]); i += needle.length; }
    }
    if (!ranges.length) return esc(text);
    ranges.sort((x, y) => x[0] - y[0]);
    const merged = [ranges[0].slice()];
    for (const r of ranges.slice(1)) {
      const last = merged[merged.length - 1];
      if (r[0] <= last[1]) last[1] = Math.max(last[1], r[1]); else merged.push(r.slice());
    }
    let out = '', pos = 0;
    for (const [s, e] of merged) {
      out += esc(text.slice(pos, s)) + '<mark>' + esc(text.slice(s, e)) + '</mark>';
      pos = e;
    }
    return out + esc(text.slice(pos));
  }

  return { OPS, norm, esc, tokenize, splitQuery, joinQuery, compile, prepare, matchArticle, filterItems, sortItems, groupItems, countBy, stats, suggest, highlight, editDistanceWithin };
});
