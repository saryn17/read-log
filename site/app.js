/* DOM layer. All search / sort / group logic lives in lib.js. */
(function () {
  'use strict';
  const L = window.ReadLogLib;
  const esc = L.esc;
  const PAGE = 48;
  const TAGS_SHOWN = 24;
  const TAGS_PAD = 6; // when fewer tags than this are shared by 2+ articles, the most used ones fill up to it
  const SITES_SHOWN = 8;
  const SORTS = ['relevance', 'read-desc', 'read-asc', 'pub-desc', 'title', 'site'];
  const GROUPS = ['none', 'category', 'month', 'year', 'site'];
  const VIEWS = ['card', 'list'];

  const $ = (s) => document.querySelector(s);
  const pad = (n, w) => String(n).padStart(w, '0');
  const fmtDate = (d) => String(d).replace(/-/g, '.');
  const fmtDateH = (d) => esc(fmtDate(d)); // for HTML templates: dates come from the data file
  const safeUrl = (u) => (/^https?:\/\//i.test(u) ? u : '#');

  const input = $('#q');
  const state = { chips: [], text: '', sort: null, group: 'none', view: 'card', limit: PAGE, tagsOpen: false, sitesOpen: false };
  let data = [];
  let cats = [];
  let suggIndex = { tags: new Map(), cats: [], sites: new Map(), kinds: [] };
  let suggList = [];
  let suggActive = -1;

  // ------------------------------------------------------------ state <-> URL / storage

  function loadPrefs() {
    try {
      const p = JSON.parse(localStorage.getItem('readlog:prefs') || '{}');
      if (GROUPS.includes(p.group)) state.group = p.group;
      if (VIEWS.includes(p.view)) state.view = p.view;
    } catch (e) { /* storage unavailable: defaults are fine */ }
  }
  function savePrefs() {
    try { localStorage.setItem('readlog:prefs', JSON.stringify({ group: state.group, view: state.view })); } catch (e) { /* ignore */ }
  }

  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    const sp = L.splitQuery(p.get('q') || '');
    state.chips = sp.chips;
    state.text = sp.text;
    input.value = sp.text;
    state.sort = SORTS.includes(p.get('sort')) ? p.get('sort') : null;
    if (GROUPS.includes(p.get('group'))) state.group = p.get('group');
    if (VIEWS.includes(p.get('view'))) state.view = p.get('view');
  }
  function writeHash() {
    const p = new URLSearchParams();
    const q = L.joinQuery(state.chips, state.text);
    if (q) p.set('q', q);
    if (state.sort) p.set('sort', state.sort);
    const hash = p.toString();
    history.replaceState(null, '', location.pathname + location.search + (hash ? '#' + hash : ''));
  }

  /** The input text without a half-typed operator such as "tag:kub", which would match nothing. */
  function liveText() {
    const v = input.value;
    if (/\s$/.test(v)) return state.text;
    const toks = L.tokenize(v);
    const last = toks[toks.length - 1];
    if (last && last.kind === 'op') { toks.pop(); return toks.map((t) => t.raw).join(' '); }
    return state.text;
  }
  const chipOn = (token) => state.chips.some((c) => c.toLowerCase() === token.toLowerCase());

  function addChips(list) {
    for (const c of list) if (!chipOn(c)) state.chips.push(c);
  }
  function toggleChip(token) {
    const i = state.chips.findIndex((c) => c.toLowerCase() === token.toLowerCase());
    if (i >= 0) state.chips.splice(i, 1); else state.chips.push(token);
  }

  // ------------------------------------------------------------ render

  function render() {
    const cq = L.compile(L.joinQuery(state.chips, liveText()));
    const hasText = cq.terms.some((t) => !t.neg);
    let sortKey = state.sort;
    if (sortKey === 'relevance' && !hasText) sortKey = 'read-desc';
    if (!sortKey) sortKey = hasText ? 'relevance' : 'read-desc';

    renderChips();
    renderFacets(cq);
    renderControls(hasText, sortKey);
    renderResults(cq, sortKey);
    $('#clear').hidden = !(state.chips.length || input.value);
    writeHash();
  }

  function renderChips() {
    $('#chips').innerHTML = state.chips.map((c, i) => {
      const neg = c.startsWith('-');
      return `<button type="button" class="chip${neg ? ' neg' : ''}" data-chip="${i}" aria-label="${esc(c)} を外す">${esc(c)}<span aria-hidden="true">×</span></button>`;
    }).join('');
  }

  function renderStats() {
    const s = L.stats(data);
    const byCat = L.countBy(data.map((a) => ({ a })), (a) => [a.category]);
    const top = cats.map((c) => ({ c, n: byCat.get(c.id) || 0 })).filter((x) => x.n).sort((x, y) => y.n - x.n);
    const max = Math.max(1, ...top.map((x) => x.n));
    const grid = L.dayGrid(data);
    const cells = grid.cols.map((col) => col.map((c) => {
      if (!c) return '<i class="gd none"></i>';
      const label = `${c.key}: ${c.n}`;
      if (!c.n) return `<i class="gd${c.today ? ' today' : ''}" title="${label}"></i>`;
      const on = chipOn('read:' + c.key);
      return `<button type="button" class="gd l${c.level}${on ? ' on' : ''}${c.today ? ' today' : ''}" data-toggle="read:${c.key}" aria-pressed="${on}" aria-label="${label}" title="${label}"></button>`;
    }).join('')).join('');
    $('#stats').innerHTML =
      `<div class="st-head mono"><span>TOTAL READ</span><span class="dot"></span></div>` +
      `<div class="st-num">${pad(s.total, 3)}</div>` +
      `<div class="st-row"><div><span class="mono">THIS MONTH</span><b>${pad(s.thisMonth, 2)}</b></div>` +
      `<div><span class="mono">THIS YEAR</span><b>${pad(s.thisYear, 2)}</b></div>` +
      `<div><span class="mono">TAGS</span><b>${pad(s.tagCount, 2)}</b></div></div>` +
      (top.length ? `<div class="st-cats">${top.map((x) =>
        `<button type="button" class="brow${chipOn('cat:' + x.c.id) ? ' on' : ''}" data-toggle="cat:${esc(x.c.id)}">` +
        `<span class="mono">${esc(x.c.label)}</span><span class="track"><i style="width:${(x.n / max * 100).toFixed(1)}%"></i></span><span class="mono">${x.n}</span></button>`).join('')}</div>` : '') +
      `<div><div class="st-grass" role="group" aria-label="日ごとの読了数">${cells}</div>` +
      `<div class="st-foot mono"><span>${grid.first.slice(0, 7)}</span><span>${s.currentMonth}</span></div></div>`;
    $('#updated').textContent = s.latest ? 'UPDATED ' + fmtDate(s.latest) : '';
    $('#bar-count').textContent = pad(s.total, 3) + ' ARTICLES';
  }

  function renderFacets(cq) {
    // Each facet is counted with its own group ignored, so a count says what clicking it would show.
    const catN = L.countBy(L.filterItems(data, cq, 'cat'), (a) => [a.category]);
    const tagN = L.countBy(L.filterItems(data, cq, 'tag'), (a) => a.tags);
    const siteN = L.countBy(L.filterItems(data, cq, 'site'), (a) => [a.site]);

    const anyCat = state.chips.some((c) => /^cat:/i.test(c));
    $('#f-cat').innerHTML =
      `<button type="button" class="pbtn${anyCat ? '' : ' on'}" data-clear-facet="cat" aria-pressed="${!anyCat}">ALL</button>` +
      cats.filter((c) => catN.get(c.id) || chipOn('cat:' + c.id)).map((c) => {
        const on = chipOn('cat:' + c.id);
        return `<button type="button" class="pbtn${on ? ' on' : ''}" data-toggle="cat:${esc(c.id)}" aria-pressed="${on}">${esc(c.label)}<span class="n">${catN.get(c.id) || 0}</span></button>`;
      }).join('');

    // FORMAT row appears once the list holds more than one kind of material.
    const kindN = L.countBy(L.filterItems(data, cq, 'kind'), (a) => [a.kind]);
    const anyKind = state.chips.some((c) => /^kind:/i.test(c));
    $('#r-kind').hidden = suggIndex.kinds.length < 2;
    $('#f-kind').innerHTML =
      `<button type="button" class="pbtn${anyKind ? '' : ' on'}" data-clear-facet="kind" aria-pressed="${!anyKind}">ALL</button>` +
      suggIndex.kinds.filter((k) => kindN.get(k.id) || chipOn('kind:' + k.id)).map((k) => {
        const on = chipOn('kind:' + k.id);
        return `<button type="button" class="pbtn${on ? ' on' : ''}" data-toggle="kind:${esc(k.id)}" aria-pressed="${on}">${esc(k.label)}<span class="n">${kindN.get(k.id) || 0}</span></button>`;
      }).join('');

    const tagBtn = ([t, n]) => {
      const on = chipOn('tag:' + t);
      return `<button type="button" class="tg${on ? ' on' : ''}" data-toggle="tag:${esc(t)}" aria-pressed="${on}">${esc(t)}<span class="n">${n}</span></button>`;
    };
    const selTags = state.chips.filter((c) => /^tag:[^,]+$/i.test(c)).map((c) => c.slice(4));
    const tagList = Array.from(tagN).sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0], 'ja'));
    const selected = selTags.map((t) => [t, tagN.get(t) || 0]);
    const rest = tagList.filter(([t]) => !selTags.includes(t));
    // A tag used by one article only narrows to that article, so it stays behind the "+N" button.
    const shared = rest.filter(([t]) => (suggIndex.tags.get(t) || 0) >= 2);
    const firstView = (shared.length >= TAGS_PAD ? shared : rest.slice(0, TAGS_PAD)).slice(0, Math.max(0, TAGS_SHOWN - selected.length));
    const shown = state.tagsOpen ? rest : firstView;
    $('#f-tag').innerHTML = selected.concat(shown).map(tagBtn).join('') +
      (rest.length > shown.length ? `<button type="button" class="expand" data-expand="tags">+${rest.length - shown.length}</button>`
        : state.tagsOpen && rest.length > firstView.length ? `<button type="button" class="expand" data-expand="tags">−</button>` : '');

    const siteList = Array.from(siteN).sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0]));
    const selSites = state.chips.filter((c) => /^site:[^,]+$/i.test(c)).map((c) => c.slice(5).toLowerCase());
    const siteShown = state.sitesOpen ? siteList : siteList.filter(([s], i) => i < SITES_SHOWN || selSites.includes(s.toLowerCase()));
    $('#f-site').innerHTML = siteShown.map(([s, n]) => {
      const on = chipOn('site:' + s);
      return `<button type="button" class="pbtn${on ? ' on' : ''}" data-toggle="site:${esc(s)}" aria-pressed="${on}">${esc(s)}<span class="n">${n}</span></button>`;
    }).join('') + (siteList.length > siteShown.length ? `<button type="button" class="expand" data-expand="sites">+${siteList.length - siteShown.length}</button>`
      : state.sitesOpen && siteList.length > SITES_SHOWN ? `<button type="button" class="expand" data-expand="sites">−</button>` : '');
  }

  function renderControls(hasText, sortKey) {
    document.querySelectorAll('[data-seg]').forEach((b) => {
      const kind = b.dataset.seg;
      const cur = kind === 'sort' ? sortKey : state[kind];
      b.setAttribute('aria-pressed', String(b.dataset.value === cur));
      if (kind === 'sort' && b.dataset.value === 'relevance') b.disabled = !hasText;
    });
  }

  function cardHtml(it, terms) {
    const a = it.a;
    const tags = a.tags.map((t) => `<button type="button" class="tg${chipOn('tag:' + t) ? ' on' : ''}" data-toggle="tag:${esc(t)}">${esc(t)}</button>`).join('');
    const link = `<a class="ttl" href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener noreferrer">${L.highlight(a.title, terms)}</a>`;
    const cat = `<button type="button" class="cat" data-toggle="cat:${esc(a.category)}">${esc(a.catLabel)}</button>`;
    const site = `<button type="button" class="site" data-toggle="site:${esc(a.site)}">${L.highlight(a.site, terms)}</button>`;
    // Key points are shown only when they are the reason the article matched.
    const hits = L.pointMatches(a, terms).slice(0, 2);
    const pts = hits.length ? `<ul class="pts">${hits.map((p) => `<li>${L.highlight(p, terms)}</li>`).join('')}</ul>` : '';
    const kd = a.kind !== 'article' ? `<button type="button" class="kd" data-toggle="kind:${esc(a.kind)}">${esc(a.kind.toUpperCase())}</button>` : '';
    const arc = a.archived && safeUrl(a.url) !== '#'
      ? `<a class="arc" href="${esc('https://web.archive.org/web/' + a.url)}" target="_blank" rel="noopener noreferrer">ARCHIVE</a>` : '';
    if (state.view === 'list') {
      return `<article class="row"><span class="mono d">${fmtDateH(a.read)}</span>` +
        `<div class="main">${link}<span class="sum">${L.highlight(a.summary, terms)}</span>${pts}<div class="tags">${tags}</div></div>` +
        `<span class="mono lm">${site}${kd}${arc}</span>${cat}</article>`;
    }
    return `<article class="card"><div class="top">${cat}<span class="mono">${fmtDateH(a.read)}</span></div>` +
      `${link}<p class="sum">${L.highlight(a.summary, terms)}</p>${pts}<div class="tags">${tags}</div>` +
      `<div class="bot mono">${site}${kd}${a.author ? `<span>${L.highlight(a.author, terms)}</span>` : ''}${a.published ? `<span>PUB ${fmtDateH(a.published)}</span>` : ''}${arc}</div></article>`;
  }

  function renderResults(cq, sortKey) {
    const rhead = $('#rhead');
    const list = $('#list');
    const more = $('#more');
    if (!data.length) {
      rhead.innerHTML = '';
      list.innerHTML = `<div class="empty"><div class="display">No articles yet</div></div>`;
      more.innerHTML = '';
      return;
    }
    const entries = L.filterItems(data, cq);
    const sorted = L.sortItems(entries, sortKey);
    const filtered = state.chips.length || input.value.trim();
    rhead.innerHTML = `<span class="display">${pad(sorted.length, 3)}</span><span class="mono">/ ${pad(data.length, 3)} ARTICLES</span>` +
      (filtered ? `<button type="button" class="btn ghost" data-clear-all>RESET</button>` : '');
    if (!sorted.length) {
      list.innerHTML = `<div class="empty"><div class="display">No match</div><button type="button" class="btn" data-clear-all>RESET</button></div>`;
      more.innerHTML = '';
      return;
    }
    const terms = cq.terms;
    let budget = state.limit;
    const groups = L.groupItems(sorted, state.group, cats, sortKey);
    let html = '';
    for (const g of groups) {
      if (budget <= 0) break;
      const items = g.items.slice(0, budget);
      budget -= items.length;
      if (state.group !== 'none') {
        html += `<div class="gh"><h2${/[^\x00-\x7f]/.test(g.label) ? ' class="jp"' : ''}>${esc(g.label)}</h2><span class="mono">${pad(g.items.length, 2)}</span></div>`;
      }
      const body = items.map((it) => cardHtml(it, terms)).join('');
      html += state.view === 'list' ? `<div class="rows">${body}</div>` : `<div class="grid">${body}</div>`;
    }
    list.innerHTML = html;
    more.innerHTML = sorted.length > state.limit ? `<button type="button" class="btn" data-more>MORE +${Math.min(PAGE, sorted.length - state.limit)}</button>` : '';
  }

  // ------------------------------------------------------------ suggestions

  function buildIndex() {
    const tags = new Map();
    const sites = new Map();
    const catN = new Map();
    const kindN = new Map();
    for (const a of data) {
      a.tags.forEach((t) => tags.set(t, (tags.get(t) || 0) + 1));
      sites.set(a.site, (sites.get(a.site) || 0) + 1);
      catN.set(a.category, (catN.get(a.category) || 0) + 1);
      kindN.set(a.kind, (kindN.get(a.kind) || 0) + 1);
    }
    suggIndex = {
      tags, sites,
      cats: cats.map((c) => ({ id: c.id, label: c.label, n: catN.get(c.id) || 0 })).filter((c) => c.n),
      kinds: Object.keys(L.KIND_LABEL).map((id) => ({ id, label: L.KIND_LABEL[id], n: kindN.get(id) || 0 })).filter((k) => k.n),
    };
  }

  const KIND_LABEL = { tag: 'TAG', cat: 'CAT', site: 'SITE', kind: 'FORMAT' };

  function updateSuggest() {
    const v = input.value;
    const token = /\s$/.test(v) ? '' : v.split(/\s+/).pop();
    suggList = token ? L.suggest(token, suggIndex) : [];
    suggActive = -1;
    paintSuggest();
  }
  function paintSuggest() {
    const ul = $('#sugg');
    ul.hidden = !suggList.length;
    input.setAttribute('aria-expanded', String(!ul.hidden));
    input.removeAttribute('aria-activedescendant');
    ul.innerHTML = suggList.map((s, i) =>
      `<li role="option" id="sg-${i}" data-sugg="${i}" class="${i === suggActive ? 'active' : ''}" aria-selected="${i === suggActive}">` +
      `<span class="mono">${s.neg ? '−' : ''}${KIND_LABEL[s.kind]}</span><span>${esc(s.alt && (s.kind === 'cat' || s.kind === 'kind') ? s.alt : s.v)}</span><span class="mono">${s.n}</span></li>`).join('');
    if (suggActive >= 0) input.setAttribute('aria-activedescendant', 'sg-' + suggActive);
  }
  function acceptSuggest(i) {
    const s = suggList[i];
    if (!s) return;
    addChips([(s.neg ? '-' : '') + s.kind + ':' + s.v]);
    const rest = input.value.replace(/\S*$/, '');
    input.value = rest;
    state.text = rest.trim();
    state.limit = PAGE;
    suggList = [];
    paintSuggest();
    render();
    input.focus();
  }
  function commitInput() {
    const sp = L.splitQuery(input.value);
    addChips(sp.chips);
    input.value = sp.text;
    state.text = sp.text;
    suggList = [];
    paintSuggest();
    render();
  }

  input.addEventListener('input', () => {
    let v = input.value;
    if (/\s$/.test(v)) {
      const sp = L.splitQuery(v);
      if (sp.chips.length) {
        addChips(sp.chips);
        v = sp.text ? sp.text + ' ' : '';
        input.value = v;
      }
    }
    state.text = v.trim();
    state.limit = PAGE;
    updateSuggest();
    render();
  });
  input.addEventListener('keydown', (e) => {
    const open = suggList.length > 0;
    if (e.key === 'ArrowDown' && open) { e.preventDefault(); suggActive = (suggActive + 1) % suggList.length; paintSuggest(); }
    else if (e.key === 'ArrowUp' && open) { e.preventDefault(); suggActive = (suggActive - 1 + suggList.length) % suggList.length; paintSuggest(); }
    else if ((e.key === 'Enter' || e.key === 'Tab') && open && (suggActive >= 0 || e.key === 'Tab')) { e.preventDefault(); acceptSuggest(Math.max(suggActive, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); commitInput(); }
    else if (e.key === 'Escape') {
      if (open) { suggList = []; paintSuggest(); }
      else if (input.value) { input.value = ''; state.text = ''; render(); }
      else input.blur();
    } else if (e.key === 'Backspace' && !input.value && state.chips.length) { state.chips.pop(); render(); }
  });
  input.addEventListener('blur', () => setTimeout(() => { suggList = []; paintSuggest(); }, 120));
  $('#sugg').addEventListener('mousedown', (e) => {
    const li = e.target.closest('[data-sugg]');
    if (li) { e.preventDefault(); acceptSuggest(Number(li.dataset.sugg)); }
  });

  // ------------------------------------------------------------ clicks / keys

  function reducedMotion() { return window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches; }

  document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-toggle],[data-chip],[data-seg],[data-more],[data-clear-all],[data-clear-facet],[data-scroll],[data-expand]');
    if (!el) return;
    const d = el.dataset;
    if (d.toggle !== undefined) { toggleChip(d.toggle); state.limit = PAGE; renderStats(); render(); }
    else if (d.chip !== undefined) { state.chips.splice(Number(d.chip), 1); state.limit = PAGE; renderStats(); render(); }
    else if (d.seg) {
      if (d.seg === 'sort') state.sort = d.value;
      else state[d.seg] = d.value;
      state.limit = PAGE;
      savePrefs();
      render();
    } else if (d.more !== undefined) { state.limit += PAGE; render(); }
    else if (d.clearAll !== undefined) { state.chips = []; state.text = ''; input.value = ''; state.limit = PAGE; renderStats(); render(); }
    else if (d.clearFacet) { state.chips = state.chips.filter((c) => !new RegExp('^-?' + d.clearFacet + ':', 'i').test(c)); renderStats(); render(); }
    else if (d.expand === 'tags') { state.tagsOpen = !state.tagsOpen; render(); }
    else if (d.expand === 'sites') { state.sitesOpen = !state.sitesOpen; render(); }
    else if (d.scroll) {
      const target = d.scroll === 'top' ? document.body : document.getElementById(d.scroll);
      if (target) target.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'start' });
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
      e.preventDefault();
      input.focus();
      input.select();
    }
  });
  window.addEventListener('hashchange', () => { readHash(); renderStats(); render(); });

  // ------------------------------------------------------------ boot

  function fail() {
    $('#list').innerHTML = `<div class="empty"><div class="display">No data</div><span class="mono">data/articles.json</span></div>`;
    document.body.classList.add('is-empty');
  }

  async function boot() {
    loadPrefs();
    let articles;
    try {
      const [a, c] = await Promise.all([
        fetch('data/articles.json', { cache: 'no-cache' }).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }),
        fetch('data/categories.json', { cache: 'no-cache' }).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }),
      ]);
      articles = a;
      cats = c;
    } catch (err) {
      fail();
      return;
    }
    data = L.prepare(articles, cats);
    document.body.classList.toggle('is-empty', data.length === 0);
    buildIndex();
    readHash();
    renderStats();
    render();
  }
  boot();
})();
