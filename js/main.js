/* =========================================================
   trace-repro — interactions
   ========================================================= */
(function () {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  /* ---------- Mobile menu ---------- */
  const toggle = $('#menu-toggle');
  const nav = $('#primary-nav');
  function setMenu(open) {
    nav.classList.toggle('is-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
  }
  toggle.addEventListener('click', () => setMenu(!nav.classList.contains('is-open')));
  $$('a', nav).forEach((a) => a.addEventListener('click', () => setMenu(false)));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setMenu(false); });

  /* ---------- Hero incident animation ---------- */
  const HERO = [
    { cap: 'A customer\u2019s checkout just failed. The error log blames checkout.', on: { 'hv-n3': 'red' }, paths: {}, step: 0, live: 'Live incident', ok: false, ent: 'status: CANCELLED', entCls: 'is-bad' },
    { cap: 'trace-repro looks at what the checkout touched: Order #8812.', on: { 'hv-n3': 'red', 'hv-entity': 'on' }, paths: { 'hv-p3': 'red' }, scan: true, step: 1, live: 'Investigating', ok: false, ent: 'scanning history\u2026', entCls: 'is-on' },
    { cap: 'Two background events changed that same order milliseconds earlier.', on: { 'hv-n3': 'red', 'hv-entity': 'on', 'hv-n1': 'on', 'hv-n2': 'on' }, paths: { 'hv-p1': 'on', 'hv-p2': 'on', 'hv-p3': 'red' }, step: 2, live: 'Culprits found', ok: false, ent: '3 events \u00b7 38ms apart', entCls: 'is-on' },
    { cap: 'A test replays all three at the exact timing. It fails \u2014 bug proven.', on: { 'hv-n3': 'red', 'hv-entity': 'on', 'hv-n1': 'on', 'hv-n2': 'on' }, paths: { 'hv-p1': 'red', 'hv-p2': 'red', 'hv-p3': 'red' }, step: 3, live: 'Test: red', ok: false, ent: 'reproduced \u2715', entCls: 'is-bad' },
    { cap: 'The real cause is fixed. Test passes. Shipped to production.', on: { 'hv-n3': 'ok', 'hv-entity': 'ok', 'hv-n1': 'ok', 'hv-n2': 'ok' }, paths: { 'hv-p1': 'on', 'hv-p2': 'on', 'hv-p3': 'on' }, step: 4, live: 'Healed', ok: true, ent: 'status: PAID \u2713', entCls: 'is-ok', n3: 'paid \u00b7 200 OK' }
  ];
  const hvNodes = ['hv-n1', 'hv-n2', 'hv-n3', 'hv-entity'].map((id) => document.getElementById(id));
  const hvPaths = ['hv-p1', 'hv-p2', 'hv-p3'].map((id) => document.getElementById(id));
  const hvSteps = $$('#hv-steps li');
  const hvCap = $('#hv-caption');
  const hvLive = $('.viz-live');
  let hi = 0;

  function heroFrame(i) {
    const f = HERO[i];
    hvNodes.forEach((n) => n.classList.remove('is-on', 'is-red', 'is-ok', 'is-bad'));
    Object.entries(f.on).forEach(([id, st]) => document.getElementById(id).classList.add('is-' + st));
    if (f.entCls) document.getElementById('hv-entity').classList.add(f.entCls);
    hvPaths.forEach((p) => p.classList.remove('is-on', 'is-red'));
    Object.entries(f.paths).forEach(([id, st]) => {
      const p = document.getElementById(id);
      p.classList.add('is-on');
      if (st === 'red') p.classList.add('is-red');
    });
    $('#hv-scan').classList.toggle('is-on', !!f.scan);
    $('#hv-entity-sub').textContent = f.ent;
    $('#hv-n3-sub').textContent = f.n3 || 'failed \u00b7 error 500';
    hvSteps.forEach((li, k) => {
      li.classList.toggle('is-done', k < f.step);
      li.classList.toggle('is-cur', k === f.step);
    });
    hvLive.lastChild.textContent = f.live;
    hvLive.classList.toggle('is-ok', f.ok);
    hvCap.textContent = f.cap;
    hvCap.classList.remove('fade'); void hvCap.offsetWidth; hvCap.classList.add('fade');
  }
  heroFrame(0);
  if (reduce) { heroFrame(HERO.length - 1); }
  else {
    setInterval(() => { hi = (hi + 1) % HERO.length; heroFrame(hi); }, 2800);
  }

  /* ---------- Walkthrough (case study) ---------- */
  const CASE = [
    {
      t: 'Something breaks in production',
      short: 'Crash',
      d: 'A customer tries to pay for Order #8812. Instead of a receipt, they get an error. Your monitoring tool fires an alert and points at the checkout code.',
      status: ['Incident open', 'is-red'], red: true,
      file: 'Cloud Run · checkout-svc',
      code: '<span class="c-red">500 InvalidStateError</span>\n<span class="c-ink">Order #8812 is CANCELLED, cannot capture \u20b94,500</span>\n\n<span class="c-dim">endpoint</span>   POST /checkout/capture\n<span class="c-dim">trace</span>      C3\n<span class="c-dim">blames</span>     checkout/service.py:142  <span class="c-dim">\u2190 wrong place</span>'
    },
    {
      t: 'It looks beyond the error',
      short: 'Investigate',
      d: 'Instead of reading just the failing request, trace-repro asks a better question: what else touched Order #8812 in the moments before it broke?',
      status: ['Investigating', ''],
      file: 'FalkorDB · graph:incident_8812',
      code: '<span class="c-dim">// follow the crash into the record it touched</span>\nMATCH (crash:Span {id: $crash_span_id})\n      -[:READ_ENTITY]->(order:StateEntity)\n\n<span class="c-ok">\u2192 order:8812</span>\n<span class="c-dim">  scanning 50,214 events from the last 60s\u2026</span>'
    },
    {
      t: 'It finds the real culprits',
      short: 'Find',
      d: 'Two completely separate background events changed the same order 38 and 26 milliseconds earlier \u2014 with no safeguard in place. Everything else is ruled out.',
      status: ['2 culprits found', 'is-red'], red: true,
      file: 'FalkorDB · query result',
      code: '<span class="c-dim">event</span>               <span class="c-dim">when</span>     <span class="c-dim">protected?</span>\nPayment retry       \u221238ms    <span class="c-red">no</span>\nCart cleanup        \u221226ms    <span class="c-red">no</span>\nCheckout (victim)     0ms    \u2014\n\n<span class="c-ok">50,211 unrelated events pruned</span>\n<span class="c-dim">root cause \u2192</span> <span class="c-ink">payments/webhooks.py \u00b7 apply_webhook()</span>'
    },
    {
      t: 'It proves the bug with a test',
      short: 'Prove',
      d: 'Before changing any code, it writes a test that fires those exact three events, in order, milliseconds apart. The test fails with the same error customers saw. Bug proven.',
      status: ['Pipeline #1 · red', 'is-red'], red: true,
      file: 'tests/repro/test_incident_8812.py',
      code: '<span class="c-dim">$ pytest tests/repro/test_incident_8812.py</span>\n\n<span class="c-red">FAILED</span> test_incident_8812_cross_trace_race\n  assert 500 == 200\n  <span class="c-red">InvalidStateError: Order #8812 is CANCELLED</span>\n\n<span class="c-red">\u2715 Pipeline #1 failed</span> <span class="c-dim">\u2014 exactly as intended</span>'
    },
    {
      t: 'It fixes the cause, not the symptom',
      short: 'Fix',
      d: 'It goes straight to the code that started the chain reaction and adds the missing safeguard: lock the order while it\u2019s being changed, and ignore duplicate messages.',
      status: ['Pipeline #2 · green', 'is-ok'],
      file: 'payments/webhooks.py · MR !217',
      code: '<span class="c-red">-  order = db.query(Order).get(id)</span>\n<span class="c-ok">+  order = (db.query(Order).filter_by(id=id)</span>\n<span class="c-ok">+             .with_for_update().one())   # lock it</span>\n<span class="c-ok">+  if order.last_event_id == event.id:   # skip duplicates</span>\n<span class="c-ok">+      return order</span>\n\n<span class="c-ok">\u2713 Pipeline #2 passed</span> <span class="c-dim">\u2014 1 passed in 0.41s</span>'
    },
    {
      t: 'It ships the fix and shows its work',
      short: 'Ship',
      d: 'The fix is security-scanned and rolled out to Google Cloud Run. A before/after report is attached, the incident closes itself, and the test stays forever as a guard.',
      status: ['Resolved', 'is-ok'],
      file: 'GitLab MR !217 · report',
      code: '<span class="c-dim">before \u2192 after</span>\n  order locked          <span class="c-red">no</span>  \u2192 <span class="c-ok">yes</span>\n  bug reproduced        <span class="c-ok">yes</span>\n  bug resolved          <span class="c-red">no</span>  \u2192 <span class="c-ok">yes</span>\n\n<span class="c-ok">\u2713 Deployed</span>  checkout-svc rev 42 \u00b7 100% traffic\n<span class="c-ok">\u2713 Closed</span>    Incident #8812'
    }
  ];

  const list = $('#case-steps');
  CASE.forEach((s, i) => {
    const li = document.createElement('li');
    const b = document.createElement('button');
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-label', 'Step ' + (i + 1) + ': ' + s.t);
    b.innerHTML = '<span class="step-n">' + String(i + 1).padStart(2, '0') + '</span><span class="step-t">' + esc(s.short) + '</span>';
    b.addEventListener('click', () => { goCase(i); restartAuto(); });
    li.appendChild(b);
    list.appendChild(li);
  });
  const caseBtns = $$('button', list);
  const prev = $('#case-prev');
  const next = $('#case-next');
  const play = $('#case-play');
  const bar = $('#case-bar');
  let ci = 0;
  let playing = !reduce;
  let inView = false;
  let raf = null;
  let t0 = 0;
  const DUR = 6500;

  function goCase(i) {
    ci = (i + CASE.length) % CASE.length;
    const s = CASE[ci];
    $('#case-count').textContent = String(ci + 1).padStart(2, '0') + ' / ' + String(CASE.length).padStart(2, '0');
    const st = $('#case-status');
    st.textContent = s.status[0];
    st.className = 'case-status ' + s.status[1];
    $('#case-title').textContent = s.t;
    $('#case-desc').textContent = s.d;
    $('#case-file').textContent = s.file;
    $('#case-pre').innerHTML = s.code;
    ['#case-title', '#case-desc', '#case-pre'].forEach((sel) => {
      const el = $(sel); el.classList.remove('fade'); void el.offsetWidth; el.classList.add('fade');
    });
    caseBtns.forEach((b, k) => {
      b.classList.toggle('is-cur', k === ci);
      b.classList.toggle('is-done', k < ci);
      b.classList.toggle('is-red', k === ci && !!CASE[k].red);
      b.setAttribute('aria-selected', String(k === ci));
    });
    prev.disabled = ci === 0;
    next.textContent = ci === CASE.length - 1 ? 'Restart \u21ba' : 'Next \u2192';
    t0 = performance.now();
  }

  function tick(now) {
    if (playing && inView) {
      const p = Math.min((now - t0) / DUR, 1);
      bar.style.width = (p * 100) + '%';
      if (p >= 1) goCase(ci + 1);
    } else if (!playing) {
      bar.style.width = '0%';
    } else {
      t0 = now - (parseFloat(bar.style.width || '0') / 100) * DUR;
    }
    raf = requestAnimationFrame(tick);
  }
  function setPlaying(v) {
    playing = v;
    play.textContent = v ? 'Pause' : 'Play';
    play.setAttribute('aria-label', v ? 'Pause autoplay' : 'Start autoplay');
    t0 = performance.now();
  }
  function restartAuto() { t0 = performance.now(); bar.style.width = '0%'; }

  prev.addEventListener('click', () => { goCase(ci - 1); setPlaying(false); });
  next.addEventListener('click', () => { goCase(ci + 1); setPlaying(false); });
  play.addEventListener('click', () => setPlaying(!playing));
  document.addEventListener('keydown', (e) => {
    if (!inView || e.target.closest('input, textarea')) return;
    if (e.key === 'ArrowRight') { goCase(ci + 1); setPlaying(false); }
    if (e.key === 'ArrowLeft') { goCase(ci - 1); setPlaying(false); }
  });
  if ('IntersectionObserver' in window) {
    new IntersectionObserver((en) => { inView = en[0].isIntersecting; }, { threshold: 0.35 }).observe($('#walkthrough .case'));
  } else { inView = true; }
  goCase(0);
  setPlaying(playing);
  raf = requestAnimationFrame(tick);

  /* ---------- Syntax highlighting ---------- */
  const RULES = {
    cypher: [
      ['tk-com', /\/\/[^\n]*/],
      ['tk-str', /"[^"\n]*"/],
      ['tk-par', /\$[a-zA-Z_]+/],
      ['tk-rel', /\[[a-z_]*:[A-Z_|]+[^\]]*\]/],
      ['tk-lbl', /:[A-Z][a-zA-Z]+/],
      ['tk-kw', /\b(?:MATCH|OPTIONAL|WHERE|AND|OR|RETURN|AS|ORDER BY|ASC|DESC|UNWIND|WITH|LIMIT)\b/],
      ['tk-fn', /\b(?:shortestPath|nodes)\b(?=\()/],
      ['tk-num', /\b\d+(?:\.\d+)?\b/]
    ],
    python: [
      ['tk-com', /#[^\n]*/],
      ['tk-str', /"[^"\n]*"|'[^'\n]*'/],
      ['tk-lbl', /@[a-zA-Z_.]+/],
      ['tk-kw', /\b(?:import|from|async|await|def|return|assert|as|if|else|for|in|with|None|True|False)\b/],
      ['tk-fn', /\b[a-z_][a-z0-9_]*(?=\()/],
      ['tk-num', /\b\d+(?:\.\d+)?\b/]
    ]
  };
  function highlight(code, lang) {
    if (lang === 'diff') {
      return code.split('\n').map((l) => {
        if (l.startsWith('+')) return '<span class="tk-add">' + esc(l) + '</span>';
        if (l.startsWith('-')) return '<span class="tk-del">' + esc(l) + '</span>';
        if (/^\s*#/.test(l)) return '<span class="tk-com">' + esc(l) + '</span>';
        return esc(l);
      }).join('\n');
    }
    const rules = RULES[lang];
    if (!rules) return esc(code);
    const master = new RegExp(rules.map(([, re]) => '(' + re.source + ')').join('|'), 'g');
    let out = '', last = 0, m;
    while ((m = master.exec(code)) !== null) {
      const idx = m.slice(1).findIndex((g) => g !== undefined);
      out += esc(code.slice(last, m.index)) + '<span class="' + rules[idx][0] + '">' + esc(m[0]) + '</span>';
      last = m.index + m[0].length;
    }
    return out + esc(code.slice(last));
  }
  $$('code[data-lang]').forEach((el) => { el.innerHTML = highlight(el.textContent, el.dataset.lang); });

  /* ---------- Code deck tabs + copy ---------- */
  const tabs = $$('.deck-tab');
  tabs.forEach((tab) => tab.addEventListener('click', () => {
    tabs.forEach((t) => { t.classList.remove('is-active'); t.setAttribute('aria-selected', 'false'); });
    $$('.deck-panel').forEach((p) => p.classList.remove('is-active'));
    tab.classList.add('is-active');
    tab.setAttribute('aria-selected', 'true');
    document.getElementById(tab.dataset.panel).classList.add('is-active');
  }));
  const copyBtn = $('#deck-copy');
  copyBtn.addEventListener('click', () => {
    const text = $('.deck-panel.is-active').textContent;
    const done = () => {
      copyBtn.textContent = 'Copied';
      copyBtn.classList.add('is-done');
      setTimeout(() => { copyBtn.textContent = 'Copy'; copyBtn.classList.remove('is-done'); }, 1500);
    };
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, done); else done();
  });

  /* ---------- FAQ: one open at a time ---------- */
  const faqs = $$('.faq');
  faqs.forEach((d) => d.addEventListener('toggle', () => {
    if (d.open) faqs.forEach((o) => { if (o !== d) o.open = false; });
  }));

  /* ---------- Reveal on scroll ---------- */
  const targets = $$('.section-label, .statement, .section-lede, .story, .versus-col, .pillar, .case, .lens, .graph-callout, .table-scroll, .flow-step, .code-deck, .reel-cut, .faq-list, .cta-inner, .outcome');
  if (reduce || !('IntersectionObserver' in window)) {
    targets.forEach((t) => t.classList.add('is-visible'));
    return;
  }
  targets.forEach((t) => t.classList.add('reveal'));
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      const sibs = Array.from(e.target.parentElement.children).filter((c) => c.classList.contains('reveal'));
      e.target.style.transitionDelay = Math.min(sibs.indexOf(e.target), 5) * 70 + 'ms';
      e.target.classList.add('is-visible');
      io.unobserve(e.target);
    });
  }, { threshold: 0.12, rootMargin: '0px 0px -60px 0px' });
  targets.forEach((t) => io.observe(t));
})();
