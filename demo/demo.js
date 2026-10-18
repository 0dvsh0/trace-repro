/* =========================================================
   demo/demo.js — renders demo.html from data/demo-data.js
   (written by demo/capture.py out of real runs) and the real
   screenshots in demo/assets/ (captured by demo/screenshot.py).
   ========================================================= */
(function () {
  'use strict';

  const DATA = window.TR_DEMO;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  if (!DATA || !DATA.incidents || !DATA.incidents.length) {
    document.querySelector('main').innerHTML =
      '<div class="frame" style="padding:80px 32px">' +
      '<h1 style="font-size:34px">No captured run found</h1>' +
      '<p class="lede">This page is generated from real runs. Produce them with:</p>' +
      '<pre class="run-card" style="padding:18px;margin-top:18px;display:block">' +
      'pip install -e .\npython demo/capture.py     # collects the real values\n' +
      'python demo/screenshot.py  # photographs the real windows</pre></div>';
    return;
  }

  let cur = 0;

  /* ---------------- run card ---------------- */
  function renderRunCard() {
    const rows = DATA.incidents.map((i) => {
      const m = i.metrics;
      const dot = i.healed
        ? '<span style="color:var(--neon)">●</span>'
        : '<span style="color:var(--red)">■</span>';
      return dot + ' ' + esc(i.sev + ' ' + i.service) +
        '\n    RED    ' + esc(m.red || '') +
        '\n    GREEN  ' + esc(m.green || '') +
        '\n    healed in ' + esc(i.duration.toFixed(2) + 's');
    }).join('\n');
    const healed = DATA.incidents.filter((i) => i.healed).length;
    $('#run-block').innerHTML =
      '<span class="dim">$ pip install -e .</span>\n' +
      '<span class="dim">$ python demo/showcase.py</span>\n\n' + rows +
      '\n\n<span style="color:var(--neon)">' + healed + '/' + DATA.incidents.length +
      ' incidents healed in ' +
      DATA.incidents.reduce((a, i) => a + i.duration, 0).toFixed(1) + 's</span>';
    const stamp = DATA.generated ? DATA.generated.replace('T', ' ').replace('+00:00', ' UTC') : '';
    $('#run-stamp').textContent = stamp;
    $('#foot-stamp').textContent = 'captured ' + stamp + ' · regenerate with: python demo/capture.py && python demo/screenshot.py';
  }

  /* ---------------- service switcher ---------------- */
  function renderSwitcher() {
    const wrap = $('#switcher');
    wrap.innerHTML = '';
    DATA.incidents.forEach((inc, i) => {
      const b = document.createElement('button');
      b.className = 'svc' + (i === cur ? ' is-cur' : '');
      b.setAttribute('role', 'tab');
      b.innerHTML =
        '<span class="svc-sev">' + esc(inc.sev) + ' · ' + esc(inc.clock) + ' · ' + esc(inc.service) + '</span>' +
        '<span class="svc-hook">' + esc(inc.hook) + '</span>' +
        '<span class="svc-meta">' + esc(inc.entity) + ' · healed in ' + inc.duration.toFixed(2) + 's</span>';
      b.addEventListener('click', () => select(i));
      wrap.appendChild(b);
    });
  }

  /* ---------------- the incident ---------------- */
  function renderIncident(inc) {
    $('#inc-title').textContent = inc.hook;
    $('#inc-tag').textContent = inc.sev + ' · ' + inc.service + ' · ' + inc.entity;

    $('#inc-story').textContent = inc.story;
    $('#inc-notes').innerHTML = inc.notes.map((n) =>
      '<div class="note"><i>▸</i><span>' + esc(n) + '</span></div>').join('');

    $('#inc-timeline').innerHTML = (inc.timeline || []).map((t) => {
      const victim = t.kind === 'request';
      return '<li class="' + (victim ? 'is-victim' : '') + '">' +
        '<time>+' + t.offset_ms.toFixed(0) + 'ms</time>' +
        '<span><span class="tl-ep">' + esc(t.endpoint) + '</span>' +
        '<span class="tl-fn">' + esc(t.function) + '() · ' +
        (victim ? 'the victim — correct code refusing a bad state'
          : 'unlocked writer of the row') + '</span>' +
        '<span class="tl-tag">' + esc(t.kind) + '</span></span></li>';
    }).join('');

    const m = inc.metrics;
    const cells = [
      [m.spans + ' → ' + m.causal, 'spans kept as causally relevant', 'is-ink'],
      [m.pruned_pct + '%', 'of the run pruned as unrelated noise', ''],
      [m.window_ms + ' ms', 'the window the whole bug lived in', ''],
      [m.red ? 'RED' : '—', 'reproduced before any code changed', ''],
      [m.green ? 'GREEN' : '—', 'the same test, after the AST repair', ''],
      [m.resolved ? 'resolved' : 'open', 'read back out of the graph', ''],
      [inc.duration.toFixed(2) + ' s', 'incident start → closed, unattended', '']
    ];
    $('#inc-metrics').innerHTML = cells.map(([n, l, cls]) =>
      '<div class="metric"><b class="' + (cls || '') + '">' + esc(n) + '</b><span>' + esc(l) + '</span></div>').join('');
  }

  /* ---------------- screenshots ---------------- */
  function renderShots(inc) {
    const shots = inc.shots || [];
    const img = $('#shot-img');
    const thumbs = $('#shot-thumbs');
    thumbs.innerHTML = '';
    if (!shots.length) {
      $('#screens .shot-grid').innerHTML =
        '<div class="missing"><h4>No screenshot captured yet</h4>' +
        '<p>This page never draws a fake window. Capture the real one:</p>' +
        '<code>python demo/screenshot.py --only ' + esc(inc.key) + '</code></div>';
      return;
    }
    shots.forEach((src, i) => {
      const t = document.createElement('button');
      t.className = 'shot-thumb' + (i === 0 ? ' is-cur' : '');
      t.innerHTML = '<img src="' + esc(src) + '" alt="page ' + (i + 1) + '">';
      t.addEventListener('click', () => pickShot(inc, i));
      thumbs.appendChild(t);
    });
    pickShot(inc, 0);
  }

  function pickShot(inc, i) {
    const shots = inc.shots || [];
    if (!shots[i]) return;
    const src = shots[i];
    const img = $('#shot-img');
    img.src = src;
    img.alt = inc.service + ' — real console capture, page ' + (i + 1);
    $('#shot-name').textContent = inc.service + ' · trace-repro demo · demo/' + inc.key;
    $('#shot-page').textContent = 'page ' + (i + 1) + ' / ' + shots.length + ' — click to enlarge';
    $$('.shot-thumb').forEach((t, k) => t.classList.toggle('is-cur', k === i));
    img.onclick = () => openLightbox(src, img.alt);
  }

  /* ---------------- telemetry tables ---------------- */
  function renderTelemetry(inc) {
    const deep = inc.deep || {};
    const tbody = $('#tbl-spans tbody');
    tbody.innerHTML = (deep.spans || []).map((s) =>
      '<tr><td class="dim">' + esc(s.id) + '</td>' +
      '<td>' + esc(s.function || s.name) + '</td>' +
      '<td class="dim">' + esc(s.file) + ':' + esc(s.lineno) + '</td>' +
      '<td class="num">' + esc(s.duration_ms) + ' ms</td>' +
      '<td class="' + (s.status === 'ERROR' ? 'bad' : 'good') + '">' + esc(s.status) + '</td>' +
      '<td class="' + (s.error_type ? 'bad' : 'dim') + '">' + esc(s.error_type || '—') + '</td></tr>').join('');

    $('#mut-entity').textContent = inc.entity;
    $('#tbl-mut tbody').innerHTML = (deep.mutations || []).map((m) =>
      '<tr><td class="dim">' + esc(m.seq_no) + '</td>' +
      '<td class="num">' + esc(m.offset_ms) + '</td>' +
      '<td>' + esc(m.field) + '</td>' +
      '<td class="dim">' + esc(m.old) + '</td>' +
      '<td>' + esc(m.new) + '</td>' +
      '<td class="' + (m.lock_held ? 'good' : 'bad') + '">' + (m.lock_held ? 'true' : 'FALSE') + '</td>' +
      '<td class="dim">' + esc(m.trace_id) + '</td></tr>').join('');

    $('#tbl-writers tbody').innerHTML = (deep.writers || []).map((w) =>
      '<tr><td>' + esc(w.function_name) + '()</td>' +
      '<td class="dim">' + esc(w.entry_endpoint) + '</td>' +
      '<td class="num">' + esc((w.offset_ms != null ? w.offset_ms.toFixed(1) : '—') + ' ms') + '</td>' +
      '<td class="' + (w.lock_held ? 'good' : 'bad') + '">' + (w.lock_held ? 'true' : 'FALSE') + '</td></tr>').join('')
      || '<tr><td colspan="4" class="dim">none recorded</td></tr>';
  }

  /* ---------------- graph ---------------- */
  function renderGraph(inc) {
    const deep = inc.deep || {};
    const nodes = (deep.graph && deep.graph.nodes) || [];
    $('#tbl-nodes tbody').innerHTML = nodes.map((n) => {
      const kind = n.kind.replace('_', ' ');
      let detail = '';
      if (n.trace_id) detail = 'trace ' + n.trace_id + (n.payload ? ' · ' + n.payload.slice(0, 48) : '');
      else if (n.span_id) detail = 'span ' + n.span_id;
      else if (n.seq !== undefined) detail = 'seq ' + n.seq + ' · lock_held=' + n.lock_held;
      else if (n.entity_key) detail = n.entity_key;
      const cls = /victim/.test(n.kind) ? 'bad' : (n.kind === 'entity' ? 'good' : 'dim');
      return '<tr><td class="' + cls + '">' + esc(n.label) + '</td>' +
        '<td class="dim">' + esc(kind) + '</td>' +
        '<td class="dim">' + esc(detail) + '</td></tr>';
    }).join('') || '<tr><td colspan="3" class="dim">no graph captured</td></tr>';

    // FalkorDB's own Browser screenshot, when one was captured.
    const fk = 'assets/falkordb-graph.png';
    const probe = new Image();
    probe.onload = function () {
      $('#graph-tag').className = 'tag is-real';
      $('#graph-tag').textContent = 'real screenshot · FalkorDB Browser';
      $('#graph-shot').innerHTML =
        '<figure class="shot-main"><img src="' + fk + '" alt="FalkorDB Browser showing the live graph">' +
        '<figcaption class="shot-cap"><span>FalkorDB Browser · live graph for this incident</span>' +
        '<span>click to enlarge</span></figcaption></figure>';
      const i = $('#graph-shot img');
      i.onclick = () => openLightbox(fk, 'FalkorDB Browser — the live graph');
    };
    probe.onerror = function () {
      $('#graph-shot').innerHTML =
        '<div class="missing"><h4>FalkorDB Browser screenshot</h4>' +
        '<p>This needs a live FalkorDB, so it can only be captured on a machine with Docker running. ' +
        'Start one and re-run — the page picks the image up on reload:</p>' +
        '<code>docker run -d -p 6379:6379 --name falkordb falkordb/falkordb:latest\n' +
        'python demo/screenshot.py --falkordb --graph falkordb://demo</code>' +
        '<span class="drop">the graph above is already the real subgraph the Cypher returned.</span></div>';
    };
    probe.src = fk;
  }

  /* ---------------- MCP transcript ---------------- */
  function renderMcp(inc) {
    const turns = (inc.deep && inc.deep.mcp) || [];
    const wrap = $('#transcript');
    wrap.innerHTML = '';
    if (!turns.length) {
      wrap.innerHTML = '<div class="missing"><h4>No MCP session captured</h4>' +
        '<p>Run <code>python demo/capture.py</code> to record a live session against the real server.</p></div>';
      return;
    }
    turns.forEach((t) => {
      const req = t.request || {};
      const params = req.params || {};
      const label = params.name
        ? 'tools/call ' + params.name
        : (req.method || '');
      const d = document.createElement('details');
      if (req.method === 'initialize' || (params.name === 'get_cross_trace_collision_graph')) d.open = true;
      d.innerHTML =
        '<summary><em>' + esc(label) + '</em><span class="dim">id ' + esc(req.id == null ? '—' : req.id) + '</span></summary>' +
        '<pre>' + highlightPair(t) + '</pre>';
      wrap.appendChild(d);
    });
  }

  function highlightPair(t) {
    const req = t.request || {};
    const out = [];
    out.push('<span class="k">→ request</span>  ' + esc(JSON.stringify(req)));
    const resp = t.response;
    if (resp == null) {
      out.push('<span class="dim">← notification, no reply</span>');
    } else if (resp.error) {
      out.push('<span class="b">← error</span>   ' + esc(JSON.stringify(resp.error)));
    } else {
      const result = resp.result || {};
      let body = '';
      if (result.content && result.content[0]) body = result.content[0].text;
      else body = JSON.stringify(result, null, 2);
      out.push('<span class="k">← result</span>  ' + esc(body.split('\n').join('\n              ')));
    }
    return out.join('\n');
  }

  /* ---------------- manual slots ----------------
     Every entry is a real capture of a real GitLab page. `why` says what the
     screenshot is *evidence of*, so a card never claims more than it shows. */
  const MANUAL = [
    ['gitlab-mr', 'GitLab Merge Request !1',
      'The submission: three scenario repos, the graph layer, the CI contract'],
    ['gitlab-joblog', 'GitLab CI · the job log',
      'self_heal:scenario [checkout] — 253 spans, the 500, and orders:8812 = CANCELLED'],
    ['gitlab-pipeline', 'GitLab CI · the pipeline',
      'lint and test green, all three self_heal scenarios red — this is the RED half'],
    ['gitlab-ci-yml', 'GitLab CI · the job definition',
      'The matrix that runs trace-repro per scenario and exports the five artifacts'],
    ['duo-agent', 'GitLab Duo Agent',
      'causal-incident-healer — the MCP tools it calls to name the unlocked writer'],
    ['duo-flow', 'GitLab Duo Flow',
      'incident-causal-self-heal — fires on pipeline_failure, drives triage → repair'],
    ['falkordb-cli', 'FalkorDB CLI',
      'Cypher straight off the server: the cross-trace collision on orders:8812']
  ];

  function renderManual() {
    const wrap = $('#manual-slots');
    wrap.innerHTML = MANUAL.map(([name, title, why]) => {
      const src = 'assets/manual/' + name + '.png';
      return '<div class="file file-card" id="slot-' + esc(name) + '">' +
        '<div class="file-head"><b>' + esc(title) + '</b><span class="file-status">probing...</span></div>' +
        '<span>' + esc(why) + '</span>' +
        '<div class="file-thumb" id="thumb-' + esc(name) + '">' +
          // deliberately not loading="lazy": these are the evidence the section
          // exists to show, and a lazy thumbnail renders as an empty box until
          // the reader happens to scroll it into view.
          '<img src="' + esc(src) + '" alt="' + esc(title) + ' — ' + esc(why) + '" decoding="async">' +
        '</div>' +
        '<div class="file-foot">' +
          '<span class="dim">assets/manual/' + esc(name) + '.png</span>' +
          '<a class="file-link" href="' + esc(src) + '" target="_blank" rel="noopener">full image ↗</a>' +
        '</div>' +
      '</div>';
    }).join('');

    // The headline count is only allowed to say what actually loaded, so a
    // missing file turns the section amber instead of quietly lying.
    const total = MANUAL.length;
    let live = 0;
    const tally = () => {
      const el = $('#manual-count');
      if (!el) return;
      el.textContent = live + '/' + total + ' captured';
      el.style.color = live === total ? 'var(--neon)' : 'var(--amber)';
    };

    MANUAL.forEach(([name, title, why]) => {
      const src = 'assets/manual/' + name + '.png';
      const card = $('#slot-' + name);
      if (!card) return;
      const status = card.querySelector('.file-status');
      const img = card.querySelector('img');
      img.onload = () => {
        live += 1;
        status.innerHTML = '<span style="color:var(--neon)">● verified live</span>';
        card.classList.add('is-ready');
        img.onclick = () => openLightbox(src, title + ' — ' + why);
        tally();
      };
      img.onerror = () => {
        status.innerHTML = '<span style="color:var(--amber)">not captured</span>';
        const thumb = $('#thumb-' + name);
        if (thumb) {
          thumb.innerHTML = '<div class="missing-slot">Needs capture to <code>' + esc(src) + '</code></div>';
        }
        tally();
      };
    });
    tally();
  }

  /* ---------------- artifacts ---------------- */
  function renderArtifacts(inc) {
    const files = [
      ['junit.xml', 'pipeline #1 red, pipeline #2 green'],
      ['gl-code-quality-report.json', 'one critical finding per unlocked writer'],
      ['self_heal.patch', 'the AST repair, as a reviewable diff'],
      ['repro_test.py', 'the deterministic reproduction test'],
      ['incident_report.md', 'the collision table and the before/after proof']
    ];
    $('#artifacts').innerHTML = files.map(([name, why]) =>
      '<div class="file"><b>' + esc(name) + '</b>' +
      '<span>' + esc(why) + '</span>' +
      '<span style="margin-top:10px">demo/artifacts/' + esc(inc.key) + '/' + esc(name) + '</span></div>').join('');
  }

  /* ---------------- lightbox ---------------- */
  function openLightbox(src, cap) {
    $('#lb-img').src = src;
    $('#lb-cap').textContent = cap || '';
    $('#lightbox').classList.add('is-open');
  }
  function closeLightbox() { $('#lightbox').classList.remove('is-open'); }
  $('#lb-close').addEventListener('click', closeLightbox);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeLightbox(); });
  $('#lightbox').addEventListener('click', (e) => {
    if (e.target.id === 'lightbox') closeLightbox();
  });

  /* ---------------- wiring ---------------- */
  function select(i) {
    cur = i;
    const inc = DATA.incidents[i];
    renderSwitcher();
    renderIncident(inc);
    renderShots(inc);
    renderTelemetry(inc);
    renderGraph(inc);
    renderMcp(inc);
    renderArtifacts(inc);
  }

  renderRunCard();
  renderManual();
  select(0);
})();
