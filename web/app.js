/* 智选A股 —— 前端应用 */
(() => {
  'use strict';

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const UP = '#ff5d5d';
  const DOWN = '#22c58b';
  const GOLD = '#e9b95f';

  const state = {
    boot: null,
    fields: [],
    fieldMap: {},
    ops: [],
    presets: [],
    conditions: [],
    screenRows: [],
    sortKey: 'score',
    sortOrder: 'desc',
    charts: {},
    syncTimer: null,
    limitupKind: 'up',
  };

  /* ---------------- 工具 ---------------- */
  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* ignore */ }
    if (!res.ok) throw new Error((data && data.error) || ('请求失败 ' + res.status));
    if (data && data.ok === false) throw new Error(data.error || '操作失败');
    return data;
  }

  let toastTimer = null;
  function toast(msg, isErr = false) {
    const el = $('#toast');
    el.textContent = msg;
    el.className = 'toast show' + (isErr ? ' err' : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.className = 'toast'), 2800);
  }

  const isNum = (v) => typeof v === 'number' && isFinite(v);
  function fmt(v, d = 2) { return isNum(v) ? v.toFixed(d) : '—'; }
  function fmtPct(v, d = 2) {
    if (!isNum(v)) return '—';
    return (v > 0 ? '+' : '') + v.toFixed(d) + '%';
  }
  function cls(v) { return !isNum(v) ? 'flat' : v > 0 ? 'up' : v < 0 ? 'down' : 'flat'; }
  function money(v) {
    if (!isNum(v)) return '—';
    if (Math.abs(v) >= 10000) return (v / 10000).toFixed(2) + '万亿';
    return v.toFixed(1) + '亿';
  }
  function price(v) { return isNum(v) ? v.toFixed(v >= 1000 ? 1 : 2) : '—'; }
  function volHand(v) {
    if (!isNum(v)) return '—';
    return v >= 10000 ? (v / 10000).toFixed(2) + '万手' : v.toFixed(0) + '手';
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function debounce(fn, ms) {
    let t;
    return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }
  function fmtDate(d) {
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }
  function dayOffset(dateStr, days) {
    const d = new Date(dateStr + 'T00:00:00');
    d.setDate(d.getDate() + days);
    return fmtDate(d);
  }
  function today() { return fmtDate(new Date()); }

  function sparkline(values, up) {
    if (!values || values.length < 2) return '';
    const w = 120, h = 34;
    const min = Math.min(...values), max = Math.max(...values);
    const span = max - min || 1;
    const pts = values.map((v, i) => {
      const x = (i / (values.length - 1)) * w;
      const y = h - 3 - ((v - min) / span) * (h - 8);
      return x.toFixed(1) + ',' + y.toFixed(1);
    }).join(' ');
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="${up ? UP : DOWN}" stroke-width="1.4" opacity=".8"/></svg>`;
  }

  /* ---------------- 图表 ---------------- */
  function chart(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    if (state.charts[id]) { state.charts[id].dispose(); }
    const c = echarts.init(el, null, { renderer: 'canvas' });
    state.charts[id] = c;
    return c;
  }
  const AXIS = { axisLine: { lineStyle: { color: '#1f2b40' } }, axisTick: { show: false }, axisLabel: { color: '#5c6b82', fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(31,43,64,.5)' } } };

  /* ---------------- 启动 ---------------- */
  async function boot() {
    try {
      state.boot = await api('/api/bootstrap');
      state.fields = state.boot.fields || [];
      state.ops = state.boot.ops || [];
      state.presets = state.boot.presets || [];
      state.fields.forEach((f) => (state.fieldMap[f.key] = f));

      renderBoardChecks();
      renderPresets();
      bindNav();
      bindSearch();
      bindScreener();
      bindPicks();
      bindBacktest();
      bindData();
      bindDrawer();

      const stats = state.boot.stats || {};
      $('#bt-end').value = stats.bar_max_date || today();
      $('#bt-start').value = dayOffset(stats.bar_max_date || today(), -365);
      $('#bt-preset').innerHTML = state.presets.map((p) => `<option value="${p.id}">${esc(p.icon)} ${esc(p.name)}</option>`).join('') +
        '<option value="custom">自定义（使用当前选股条件）</option>';
      updateBtDesc();
      if (state.presets.length) applyPreset(state.presets[0], false);
      else addCondition();

      route();
      if (state.boot.sync && state.boot.sync.running) { pollSync(); }
      else updateConn(true);
      if (!(state.boot.stats && state.boot.stats.stocks)) {
        setTimeout(() => {
          location.hash = '#/data';
          toast('首次使用：请点击「全量重建」初始化行情数据');
        }, 400);
      }

      // 深链接：?stock=600000 直接打开个股；#/screener?run=1 自动选股
      const qs = new URLSearchParams(location.search);
      if (qs.get('stock')) setTimeout(() => openStock(qs.get('stock')), 500);
      if (location.hash.includes('run=1')) {
        setTimeout(() => {
          if (location.hash.includes('backtest')) runBacktest();
          else runScreen();
        }, 700);
      }
    } catch (e) {
      updateConn(false);
      toast('无法连接后端：' + e.message, true);
      console.error(e);
    }
  }

  function updateConn(ok, text) {
    const dot = $('#conn-dot');
    if (!dot) return;
    dot.className = 'dot' + (ok ? '' : ' off');
    $('#conn-text').textContent = text || (ok ? '已连接' : '连接失败');
  }

  function bindNav() {
    $$('.nav-item').forEach((btn) => btn.addEventListener('click', () => {
      location.hash = '#/' + btn.dataset.view;
    }));
    window.addEventListener('hashchange', route);
  }

  function route() {
    const name = (location.hash.replace('#/', '') || 'overview').split('?')[0];
    const valid = ['overview', 'picks', 'screener', 'limitup', 'backtest', 'watchlist', 'data'];
    const view = valid.includes(name) ? name : 'overview';
    if (view !== 'watchlist') { clearInterval(watchTimer); watchTimer = null; }
    $$('.nav-item').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
    $$('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-' + view));
    if (view === 'overview') loadOverview();
    if (view === 'picks') loadPicks();
    if (view === 'limitup') loadLimitUp(state.limitupKind);
    if (view === 'watchlist') loadWatchlist();
    if (view === 'data') loadDataStatus();
    if (view === 'backtest' && state.boot) updateBtDesc();
    if (view === 'screener') {
      const pending = sessionStorage.getItem('pendingPreset');
      if (pending) {
        sessionStorage.removeItem('pendingPreset');
        const p = state.presets.find((x) => x.id === pending);
        if (p) { applyPreset(p, false); setTimeout(runScreen, 60); }
      }
    }
    if (!$('#index-strip').childElementCount) loadIndexStrip();
  }

  async function loadIndexStrip() {
    try {
      const data = await api('/api/index');
      if (data.indices) renderIndexStrip(data.indices);
    } catch (e) { /* ignore */ }
  }

  /* ---------------- 行情条 ---------------- */
  async function loadOverview() {
    try {
      const data = await api('/api/market/overview');
      if (!data.ok) {
        $('#ov-sub').textContent = data.error || '暂无行情数据';
        return;
      }
      renderIndexStrip(data.indices);
      $('#ov-sub').textContent = `全市场 ${data.breadth.total} 只 · 成交额 ${money(data.breadth.amount)} · 平均涨跌 ${fmtPct(data.breadth.avg_pct)}`;
      $('#ov-date').textContent = data.trade_date ? '交易日 ' + data.trade_date : '';
      $('#ov-breadth').textContent = `涨 ${data.breadth.up} · 跌 ${data.breadth.down} · 平 ${data.breadth.flat} · 涨停 ${data.breadth.limit_up} · 跌停 ${data.breadth.limit_down}`;
      renderIndexCards(data.indices);
      renderDist(data.dist);
      renderIndustry(data.industries);
      renderMini('tbl-gainers', data.gainers);
      renderMini('tbl-losers', data.losers);
      renderMini('tbl-actives', data.actives, true);
    } catch (e) {
      $('#ov-sub').textContent = e.message;
    }
  }

  function renderIndexStrip(indices) {
    const el = $('#index-strip');
    el.innerHTML = (indices || []).slice(0, 6).map((i) => `
      <div class="idx">
        <b>${esc(i.name)}</b>
        <div class="v"><span class="p ${cls(i.pct)}">${isNum(i.price) ? i.price.toFixed(2) : '—'}</span>
        <span class="c ${cls(i.pct)}">${fmtPct(i.pct)}</span></div>
      </div>`).join('');
  }

  function renderIndexCards(indices) {
    const el = $('#ov-indices');
    el.innerHTML = (indices || []).map((i) => `
      <div class="idx-card">
        <div class="name"><span>${esc(i.name)}</span><span class="${cls(i.pct)}">${fmtPct(i.pct)}</span></div>
        <div class="price ${cls(i.pct)}">${isNum(i.price) ? i.price.toFixed(2) : '—'}</div>
        <div class="pct muted">${i.date || ''}</div>
        ${sparkline(i.spark, (i.pct || 0) >= 0)}
      </div>`).join('');
  }

  function renderDist(dist) {
    const c = chart('chart-dist');
    if (!c) return;
    const data = (dist || []).filter((d) => d.value > 0);
    c.setOption({
      backgroundColor: 'transparent',
      grid: { left: 62, right: 48, top: 10, bottom: 12 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: '#0f1520', borderColor: '#1f2b40', textStyle: { color: '#e9eef7', fontSize: 11 } },
      xAxis: { type: 'value', ...AXIS, splitLine: { show: false }, axisLabel: { show: false } },
      yAxis: { type: 'category', data: data.map((d) => d.name).reverse(), ...AXIS, axisLabel: { color: '#8593a8', fontSize: 10.5 } },
      series: [{
        type: 'bar', barWidth: 12,
        data: data.map((d) => ({
          value: d.value,
          itemStyle: { color: d.name.includes('涨') ? UP : d.name.includes('跌') ? DOWN : '#5c6b82', borderRadius: [0, 4, 4, 0] },
        })).reverse(),
        label: { show: true, position: 'right', color: '#8593a8', fontSize: 10 },
      }],
    }, true);
  }

  function renderIndustry(list) {
    const c = chart('chart-industry');
    if (!c) return;
    const data = (list || []).slice(0, 10).reverse();
    c.setOption({
      backgroundColor: 'transparent',
      grid: { left: 78, right: 48, top: 8, bottom: 10 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: '#0f1520', borderColor: '#1f2b40', textStyle: { color: '#e9eef7', fontSize: 11 } },
      xAxis: { type: 'value', ...AXIS, splitLine: { show: false }, axisLabel: { show: false } },
      yAxis: { type: 'category', data: data.map((d) => d.name), ...AXIS, axisLabel: { color: '#8593a8', fontSize: 10 } },
      series: [{
        type: 'bar', barWidth: 11,
        data: data.map((d) => ({ value: d.pct, itemStyle: { color: d.pct >= 0 ? UP : DOWN, borderRadius: d.pct >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4] } })),
        label: { show: true, position: 'right', formatter: (p) => fmtPct(p.value), color: '#8593a8', fontSize: 10 },
      }],
    }, true);
  }

  function renderMini(id, rows, byAmount) {
    const tb = document.getElementById(id);
    if (!tb) return;
    tb.innerHTML = (rows || []).map((r) => `
      <tr data-code="${esc(r.code)}">
        <td><span class="nm">${esc(r.name)}</span></td>
        <td class="num">${price(r.price)}</td>
        <td class="num ${cls(r.pct)}">${byAmount ? money(r.amount) : fmtPct(r.pct)}</td>
      </tr>`).join('');
    $$('tr', tb).forEach((tr) => tr.addEventListener('click', () => openStock(tr.dataset.code)));
  }

  /* ---------------- 搜索 ---------------- */
  function bindSearch() {
    const input = $('#global-search');
    const drop = $('#search-drop');
    const run = debounce(async () => {
      const q = input.value.trim();
      if (!q) { drop.classList.add('hidden'); return; }
      try {
        const res = await api('/api/search?q=' + encodeURIComponent(q));
        const rows = res.rows || [];
        if (!rows.length) { drop.classList.add('hidden'); return; }
        drop.innerHTML = rows.map((r) => `
          <div class="item" data-code="${esc(r.code)}">
            <span>${esc(r.name)}</span>
            <span class="code">${esc(r.code)} <b class="${cls(r.pct)}">${fmtPct(r.pct)}</b></span>
          </div>`).join('');
        drop.classList.remove('hidden');
        $$('.item', drop).forEach((it) => it.addEventListener('click', () => {
          drop.classList.add('hidden');
          input.value = '';
          openStock(it.dataset.code);
        }));
      } catch (e) { /* ignore */ }
    }, 180);
    input.addEventListener('input', run);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        const first = $('.item', drop);
        if (first) first.click();
      }
    });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.search-box')) drop.classList.add('hidden');
    });
  }

  /* ---------------- 选股 ---------------- */
  function renderPresets() {
    const el = $('#preset-row');
    el.innerHTML = state.presets.map((p) => `
      <div class="preset" data-id="${p.id}" title="${esc(p.desc)}">
        <span class="ico">${p.icon}</span>
        <div><b>${esc(p.name)}</b><span>${esc(p.desc)}</span></div>
      </div>`).join('');
    $$('.preset', el).forEach((b) => b.addEventListener('click', () => {
      const p = state.presets.find((x) => x.id === b.dataset.id);
      if (p) { applyPreset(p, true); }
    }));
  }

  function renderBoardChecks() {
    $('#board-checks').innerHTML = (state.boot.boards || []).map((b, i) => `
      <label><input type="checkbox" value="${esc(b)}" ${i < 4 ? 'checked' : ''}>${esc(b)}</label>`).join('');
  }

  function applyPreset(preset, run) {
    state.conditions = JSON.parse(JSON.stringify(preset.conds || []));
    $$('.preset').forEach((b) => b.classList.toggle('active', b.dataset.id === preset.id));
    renderConditions();
    if (run) runScreen();
  }

  function addCondition(cond) {
    state.conditions.push(cond || { field: 'pct', op: 'gte', value: 3 });
    renderConditions();
  }

  function renderConditions() {
    const list = $('#cond-list');
    list.innerHTML = '';
    state.conditions.forEach((cond, idx) => list.appendChild(conditionRow(cond, idx)));
    $('#cond-count').textContent = state.conditions.length + ' 条';
    if (!state.conditions.length) {
      list.innerHTML = '<p class="muted" style="font-size:11.5px;margin:4px 0 2px">暂无附加条件，将扫描全部股票池</p>';
    }
  }

  function conditionRow(cond, idx) {
    const div = document.createElement('div');
    const field = state.fieldMap[cond.field] || state.fields[0];
    const isBool = field.type === 'bool';
    const isRange = cond.op === 'between';
    div.className = 'cond-row' + (isRange ? ' has-range' : '');

    const groups = {};
    state.fields.forEach((f) => { (groups[f.group] = groups[f.group] || []).push(f); });
    const fieldOpts = Object.keys(groups).map((g) => `<optgroup label="${esc(g)}">` +
      groups[g].map((f) => `<option value="${f.key}" ${f.key === cond.field ? 'selected' : ''}>${esc(f.label)}${f.unit ? '（' + esc(f.unit) + '）' : ''}</option>`).join('') +
      '</optgroup>').join('');

    const opOpts = isBool
      ? `<option value="eq" selected>等于</option>`
      : state.ops.map((o) => `<option value="${o.key}" ${o.key === cond.op ? 'selected' : ''}>${o.label}</option>`).join('');

    const valInput = isBool
      ? `<select class="cond-val"><option value="true" ${cond.value ? 'selected' : ''}>是</option><option value="false" ${cond.value === false ? 'selected' : ''}>否</option></select>`
      : `<input class="cond-val" type="number" step="${field.step || 1}" value="${cond.value != null ? cond.value : ''}">`;

    const val2 = isRange && !isBool
      ? `<input class="cond-val2" type="number" step="${field.step || 1}" value="${cond.value2 != null ? cond.value2 : ''}">`
      : (isRange ? '' : '');

    div.innerHTML = `
      <select class="cond-field">${fieldOpts}</select>
      <select class="cond-op">${opOpts}</select>
      ${valInput}
      ${val2}
      <button class="remove" title="删除">✕</button>`;

    $('.cond-field', div).addEventListener('change', (e) => {
      const f = state.fieldMap[e.target.value];
      cond.field = f.key;
      cond.op = f.type === 'bool' ? 'eq' : 'gte';
      cond.value = f.type === 'bool' ? true : '';
      delete cond.value2;
      renderConditions();
    });
    $('.cond-op', div).addEventListener('change', (e) => {
      cond.op = e.target.value;
      renderConditions();
    });
    $('.cond-val', div).addEventListener('input', (e) => {
      if (isBool) cond.value = e.target.value === 'true';
      else cond.value = e.target.value === '' ? null : Number(e.target.value);
    });
    const v2 = $('.cond-val2', div);
    if (v2) v2.addEventListener('input', (e) => { cond.value2 = e.target.value === '' ? null : Number(e.target.value); });
    $('.remove', div).addEventListener('click', () => {
      state.conditions.splice(idx, 1);
      $$('.preset').forEach((b) => b.classList.remove('active'));
      renderConditions();
    });
    return div;
  }

  function universeFromUI() {
    const boards = $$('#board-checks input:checked').map((i) => i.value);
    return {
      boards: boards.length && boards.length < (state.boot.boards || []).length ? boards : [],
      exclude_st: $('#excl-st').checked,
      exclude_new: $('#excl-new').checked,
      exclude_suspended: $('#excl-sus').checked,
    };
  }

  function bindScreener() {
    $('#btn-add-cond').addEventListener('click', () => addCondition());
    $('#btn-run-screen').addEventListener('click', () => runScreen());
    $('#btn-reset').addEventListener('click', () => {
      state.conditions = [];
      $$('.preset').forEach((b) => b.classList.remove('active'));
      renderConditions();
      state.screenRows = [];
      $('#tbl-screen tbody').innerHTML = '';
      $('#res-count').textContent = '—';
      $('#res-meta').textContent = '';
      $('#screen-empty').classList.remove('hidden');
    });
    $('#btn-export').addEventListener('click', exportCsv);
    $$('#tbl-screen thead th[data-key]').forEach((th) => th.addEventListener('click', () => {
      const key = th.dataset.key;
      if (state.sortKey === key) state.sortOrder = state.sortOrder === 'desc' ? 'asc' : 'desc';
      else { state.sortKey = key; state.sortOrder = 'desc'; }
      sortAndRender();
    }));
  }

  async function runScreen() {
    const btn = $('#btn-run-screen');
    btn.disabled = true;
    btn.textContent = '筛选中…';
    try {
      const sortMap = { score: 'score', pct: 'pct', turnover: 'turnover', amount: 'amount', pct_60d: 'pct_60d', mktcap: 'mktcap', pe: 'pe' };
      const data = await api('/api/screen', {
        method: 'POST',
        body: {
          conditions: state.conditions.filter((c) => c.field && c.value !== undefined),
          universe: universeFromUI(),
          limit: Number($('#result-limit').value),
          sort_by: sortMap[$('#sort-by').value] || 'score',
          order: 'desc',
        },
      });
      state.screenRows = data.rows || [];
      state.sortKey = 'score';
      state.sortOrder = 'desc';
      sortAndRender();
      $('#res-count').textContent = data.total;
      $('#res-meta').textContent = `只符合条件 · 扫描 ${data.scanned} 只 · 耗时 ${data.elapsed}s`;
      $('#res-time').textContent = data.spot_ts ? '行情 ' + data.spot_ts : '';
      if (data.warnings && data.warnings.length) toast(data.warnings[0], true);
      $('#screen-empty').classList.add('hidden');
    } catch (e) {
      toast(e.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = '开始选股';
    }
  }

  function sortAndRender() {
    const rows = state.screenRows.slice();
    const key = state.sortKey;
    const dir = state.sortOrder === 'asc' ? 1 : -1;
    rows.sort((a, b) => {
      const av = a[key], bv = b[key];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === 'string') return av.localeCompare(bv) * dir;
      return (av - bv) * dir;
    });
    renderScreenRows(rows);
  }

  function signalTags(r) {
    const tags = [];
    if (r.ma_bull) tags.push('<span class="tag good">多头</span>');
    if (r.macd_cross != null && r.macd_cross <= 3) tags.push('<span class="tag gold">MACD金叉</span>');
    if (r.breakout60) tags.push('<span class="tag warn">60日新高</span>');
    if (r.vol_ratio5 && r.vol_ratio5 >= 1.5) tags.push('<span class="tag">放量</span>');
    if (r.bias20 != null && r.bias20 < -5) tags.push('<span class="tag">超跌</span>');
    return tags.slice(0, 2).join('') || '<span class="muted">—</span>';
  }

  function renderScreenRows(rows) {
    const tb = $('#tbl-screen tbody');
    tb.innerHTML = rows.map((r) => `
      <tr data-code="${esc(r.code)}">
        <td><span class="score">${fmt(r.score, 1)}</span></td>
        <td><div class="stock-cell"><span class="code">${esc(r.code)}</span><b>${esc(r.name)}${r.is_st ? '<span class="tag warn">ST</span>' : ''}</b></div></td>
        <td class="num">${price(r.price)}</td>
        <td class="num ${cls(r.pct)}">${fmtPct(r.pct)}</td>
        <td class="num">${fmt(r.turnover)}%</td>
        <td class="num">${fmt(r.volume_ratio)}</td>
        <td class="num">${money(r.amount)}</td>
        <td class="num">${money(r.mktcap)}</td>
        <td class="num">${fmt(r.pe)}</td>
        <td class="num ${cls(r.pct_60d)}">${fmtPct(r.pct_60d, 1)}</td>
        <td>${signalTags(r)}</td>
        <td class="num"><div class="row-actions">
          <button class="star ${r.watch ? 'on' : ''}" data-star="${esc(r.code)}" title="自选">${r.watch ? '★' : '☆'}</button>
        </div></td>
      </tr>`).join('');
    $$('tr', tb).forEach((tr) => {
      tr.addEventListener('click', (e) => {
        if (e.target.closest('[data-star]')) return;
        openStock(tr.dataset.code);
      });
    });
    $$('[data-star]', tb).forEach((b) => b.addEventListener('click', async (e) => {
      e.stopPropagation();
      const code = b.dataset.star;
      try {
        if (b.classList.contains('on')) await api('/api/watchlist/' + code, { method: 'DELETE' });
        else await api('/api/watchlist', { method: 'POST', body: { code } });
        b.classList.toggle('on');
        b.textContent = b.classList.contains('on') ? '★' : '☆';
        toast(b.classList.contains('on') ? '已加入自选' : '已移出自选');
      } catch (err) { toast(err.message, true); }
    }));
  }

  function exportCsv() {
    if (!state.screenRows.length) { toast('暂无可导出的结果', true); return; }
    const head = ['代码', '名称', '评分', '最新价', '涨跌幅%', '换手率%', '量比', '成交额(亿)', '总市值(亿)', 'PE', 'PB', '60日涨幅%', '行业'];
    const lines = [head.join(',')];
    state.screenRows.forEach((r) => {
      lines.push([r.code, `"${(r.name || '').replace(/"/g, '""')}"`, r.score, r.price, r.pct, r.turnover, r.volume_ratio, r.amount, r.mktcap, r.pe, r.pb, r.pct_60d, `"${r.industry || ''}"`].join(','));
    });
    const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = '选股结果_' + today() + '.csv';
    a.click();
    URL.revokeObjectURL(a.href);
    toast('已导出 CSV');
  }

  /* ---------------- 个股详情 ---------------- */
  function bindDrawer() {
    $('#d-close').addEventListener('click', closeDrawer);
    $('#drawer-mask').addEventListener('click', closeDrawer);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });
    $('#d-watch').addEventListener('click', async () => {
      const code = $('#drawer').dataset.code;
      if (!code) return;
      const btn = $('#d-watch');
      try {
        if (btn.dataset.on === '1') { await api('/api/watchlist/' + code, { method: 'DELETE' }); btn.dataset.on = ''; btn.textContent = '☆ 加入自选'; toast('已移出自选'); }
        else { await api('/api/watchlist', { method: 'POST', body: { code } }); btn.dataset.on = '1'; btn.textContent = '★ 已自选'; toast('已加入自选'); }
        if (location.hash.includes('watchlist')) loadWatchlist();
      } catch (e) { toast(e.message, true); }
    });
    $('#d-backtest').addEventListener('click', () => {
      closeDrawer();
      location.hash = '#/backtest';
    });
    window.addEventListener('resize', debounce(() => {
      Object.values(state.charts).forEach((c) => c && c.resize());
    }, 150));
  }

  async function openStock(code) {
    const drawer = $('#drawer');
    drawer.dataset.code = code;
    drawer.classList.add('open');
    $('#drawer-mask').classList.remove('hidden');
    requestAnimationFrame(() => $('#drawer-mask').classList.add('show'));
    $('#d-name').textContent = '载入中…';
    $('#d-code').textContent = code;
    $('#d-board').textContent = '';
    try {
      const data = await api('/api/stock/' + code);
      const s = data.stock || {};
      $('#d-name').textContent = s.name || code;
      $('#d-code').textContent = s.code || code;
      $('#d-board').textContent = s.board || '';
      const up = (s.pct || 0) >= 0;
      $('#d-price').innerHTML = `<span class="big ${cls(s.pct)}">${price(s.price)}</span>
        <span class="chg ${cls(s.pct)}">${fmtPct(s.pct)}</span>
        <span class="muted" style="font-size:11.5px">${isNum(s.change) ? (s.change > 0 ? '+' : '') + s.change.toFixed(2) : ''}</span>`;
      const items = [
        ['今开', price(s.open)], ['昨收', price(s.prev_close)], ['最高', price(s.high)], ['最低', price(s.low)],
        ['换手', fmt(s.turnover) + '%'], ['量比', fmt(s.volume_ratio)], ['成交额', money(s.amount)], ['总市值', money(s.mktcap)],
        ['PE(动)', fmt(s.pe)], ['PB', fmt(s.pb)],
        ['MA5', price(s.ma5)], ['MA20', price(s.ma20)], ['RSI6', fmt(s.rsi6, 1)], ['60日涨幅', fmtPct(s.pct_60d, 1)],
        ['距60日高', fmtPct(s.dist_high60, 1)],
      ];
      $('#d-stats').innerHTML = items.map(([k, v]) => `<div class="item"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
      $('#d-watch').dataset.on = s.watch ? '1' : '';
      $('#d-watch').textContent = s.watch ? '★ 已自选' : '☆ 加入自选';
      $('#d-concepts').textContent = s.concepts ? '概念：' + s.concepts : '';
      renderKline(data.series);
      renderMacd(data.series);
    } catch (e) {
      toast(e.message, true);
    }
  }

  function closeDrawer() {
    const drawer = $('#drawer');
    if (!drawer.classList.contains('open')) return;
    drawer.classList.remove('open');
    $('#drawer-mask').classList.remove('show');
    setTimeout(() => $('#drawer-mask').classList.add('hidden'), 250);
  }

  function renderKline(series) {
    const c = chart('d-chart');
    if (!c || !series) return;
    const dates = series.dates || [];
    const cUp = UP, cDown = DOWN;
    const gridTop = 26, gridH = '58%';
    c.setOption({
      backgroundColor: 'transparent',
      animation: false,
      legend: { top: 0, left: 6, textStyle: { color: '#8593a8', fontSize: 10 }, itemWidth: 14, itemHeight: 7 },
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'cross', lineStyle: { color: '#3b4a63' } },
        backgroundColor: 'rgba(15,21,32,.96)', borderColor: '#1f2b40', textStyle: { color: '#e9eef7', fontSize: 11 },
        formatter: (ps) => {
          const i = ps[0].dataIndex;
          const k = series.kline[i] || [];
          return `<b>${dates[i]}</b><br/>开 ${k[0]}　收 ${k[1]}<br/>低 ${k[2]}　高 ${k[3]}<br/>涨跌 <span style="color:${(series.pct[i] || 0) >= 0 ? cUp : cDown}">${fmtPct(series.pct[i])}</span><br/>量 ${volHand(series.volume[i])}`;
        },
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [
        { left: 52, right: 14, top: gridTop, height: gridH },
        { left: 52, right: 14, top: '76%', height: '12%' },
      ],
      xAxis: [
        { type: 'category', data: dates, boundaryGap: true, axisLine: { lineStyle: { color: '#1f2b40' } }, axisLabel: { show: false }, axisTick: { show: false }, splitLine: { show: false } },
        { type: 'category', gridIndex: 1, data: dates, axisLine: { lineStyle: { color: '#1f2b40' } }, axisLabel: { color: '#5c6b82', fontSize: 9 }, axisTick: { show: false }, splitLine: { show: false } },
      ],
      yAxis: [
        { scale: true, ...AXIS, axisLabel: { color: '#5c6b82', fontSize: 9.5 } },
        { gridIndex: 1, scale: true, ...AXIS, axisLabel: { color: '#5c6b82', fontSize: 9, formatter: (v) => (v >= 10000 ? (v / 10000).toFixed(0) + '万' : v) }, splitNumber: 2 },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: 55, end: 100 },
        { type: 'slider', xAxisIndex: [0, 1], bottom: 2, height: 13, borderColor: '#1f2b40', backgroundColor: '#0d1420', fillerColor: 'rgba(91,141,239,.12)', handleStyle: { color: '#3b4a63' }, textStyle: { color: '#5c6b82', fontSize: 9 }, dataBackground: { lineStyle: { color: '#243147' }, areaStyle: { color: '#151e2f' } } },
      ],
      series: [
        { name: 'K线', type: 'candlestick', data: series.kline, itemStyle: { color: cUp, color0: cDown, borderColor: cUp, borderColor0: cDown } },
        { name: 'MA5', type: 'line', data: series.ma5, symbol: 'none', lineStyle: { width: 1, color: '#f0d27a' }, smooth: true },
        { name: 'MA10', type: 'line', data: series.ma10, symbol: 'none', lineStyle: { width: 1, color: '#6ea8ff' }, smooth: true },
        { name: 'MA20', type: 'line', data: series.ma20, symbol: 'none', lineStyle: { width: 1, color: '#c084fc' }, smooth: true },
        { name: 'MA60', type: 'line', data: series.ma60, symbol: 'none', lineStyle: { width: 1, color: '#4ade80' }, smooth: true },
        { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, barWidth: '62%', data: (series.volume || []).map((v, i) => ({ value: v, itemStyle: { color: (series.pct[i] || 0) >= 0 ? cUp : cDown, opacity: 0.85 } })) },
      ],
    }, true);
  }

  function renderMacd(series) {
    const c = chart('d-chart2');
    if (!c || !series) return;
    c.setOption({
      backgroundColor: 'transparent',
      animation: false,
      legend: { top: 0, left: 4, textStyle: { color: '#8593a8', fontSize: 10 }, itemWidth: 12, itemHeight: 6 },
      tooltip: { trigger: 'axis', backgroundColor: 'rgba(15,21,32,.96)', borderColor: '#1f2b40', textStyle: { color: '#e9eef7', fontSize: 11 }, axisPointer: { type: 'line', lineStyle: { color: '#3b4a63' } } },
      grid: { left: 46, right: 12, top: 24, bottom: 8 },
      xAxis: { type: 'category', data: series.dates, axisLine: { lineStyle: { color: '#1f2b40' } }, axisLabel: { color: '#5c6b82', fontSize: 9 }, axisTick: { show: false } },
      yAxis: { scale: true, ...AXIS, axisLabel: { color: '#5c6b82', fontSize: 9 } },
      series: [
        { name: 'MACD', type: 'bar', data: (series.hist || []).map((v) => ({ value: v, itemStyle: { color: v >= 0 ? UP : DOWN, opacity: 0.8 } })), barWidth: '55%' },
        { name: 'DIF', type: 'line', data: series.dif, symbol: 'none', lineStyle: { width: 1.2, color: '#f0d27a' } },
        { name: 'DEA', type: 'line', data: series.dea, symbol: 'none', lineStyle: { width: 1.2, color: '#6ea8ff' } },
      ],
    }, true);
  }

  /* ---------------- 回测 ---------------- */
  function bindBacktest() {
    $('#bt-preset').addEventListener('change', updateBtDesc);
    $('#btn-run-bt').addEventListener('click', runBacktest);
  }

  function updateBtDesc() {
    const id = $('#bt-preset').value;
    const p = state.presets.find((x) => x.id === id);
    $('#bt-desc').textContent = p ? p.desc : '将使用「智能选股」页当前的条件进行回测。';
  }

  async function runBacktest() {
    const btn = $('#btn-run-bt');
    btn.disabled = true;
    btn.textContent = '回测中…';
    const presetId = $('#bt-preset').value;
    const payload = {
      start: $('#bt-start').value,
      end: $('#bt-end').value,
      hold_days: Number($('#bt-hold').value),
      max_positions: Number($('#bt-slots').value),
      capital: Number($('#bt-capital').value) || 1000000,
    };
    if (presetId === 'custom') {
      payload.conditions = state.conditions;
      payload.universe = universeFromUI();
    } else {
      payload.preset_id = presetId;
    }
    try {
      const data = await api('/api/backtest', { method: 'POST', body: payload });
      if (!data.ok) throw new Error(data.error || '回测失败');
      renderBacktest(data);
    } catch (e) {
      toast(e.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = '运行回测';
    }
  }

  function renderBacktest(data) {
    $('#bt-empty').classList.add('hidden');
    $('#bt-body').classList.remove('hidden');
    const st = data.stats;
    const cells = [
      ['总收益', fmtPct(st.total_return, 1), cls(st.total_return)],
      ['年化收益', fmtPct(st.annual_return, 1), cls(st.annual_return)],
      ['最大回撤', fmtPct(st.max_drawdown, 1), 'down'],
      ['夏普比率', fmt(st.sharpe, 2), ''],
      ['胜率', fmt(st.win_rate, 1) + '%', ''],
    ];
    $('#bt-stats').innerHTML = cells.map(([k, v, c]) => `<div class="stat"><div class="k">${k}</div><div class="v ${c}">${v}</div></div>`).join('');
    $('#bt-range').textContent = st.start + ' ~ ' + st.end;
    $('#bt-dd-max').textContent = '最大 ' + fmtPct(st.max_drawdown, 1);

    const curve = data.curve || [];
    const bench = data.benchmark || [];
    const dates = curve.map((p) => p.date);
    const benchMap = {};
    bench.forEach((p) => (benchMap[p.date] = p.equity));
    const eq = curve.map((p) => p.equity);
    const benchSeries = dates.map((d) => benchMap[d] != null ? benchMap[d] : null);

    const c = chart('chart-equity');
    if (c) {
      c.setOption({
        backgroundColor: 'transparent', animation: false,
        legend: { top: 0, right: 8, textStyle: { color: '#8593a8', fontSize: 11 }, itemWidth: 14, itemHeight: 8 },
        tooltip: { trigger: 'axis', backgroundColor: 'rgba(15,21,32,.96)', borderColor: '#1f2b40', textStyle: { color: '#e9eef7', fontSize: 11 }, axisPointer: { type: 'cross' } },
        grid: { left: 62, right: 18, top: 32, bottom: 34 },
        xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: '#1f2b40' } }, axisLabel: { color: '#5c6b82', fontSize: 9.5 }, axisTick: { show: false } },
        yAxis: { scale: true, ...AXIS, axisLabel: { color: '#5c6b82', fontSize: 9.5, formatter: (v) => (v / 10000).toFixed(0) + '万' } },
        dataZoom: [{ type: 'inside' }, { type: 'slider', height: 12, bottom: 4, borderColor: '#1f2b40', fillerColor: 'rgba(91,141,239,.12)', textStyle: { color: '#5c6b82', fontSize: 9 } }],
        series: [
          { name: '策略', type: 'line', data: eq, symbol: 'none', lineStyle: { width: 1.8, color: GOLD }, areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(233,185,95,.28)' }, { offset: 1, color: 'rgba(233,185,95,0)' }]) } },
          { name: '沪深300', type: 'line', data: benchSeries, symbol: 'none', lineStyle: { width: 1.1, color: '#6ea8ff', type: 'dashed' } },
        ],
      }, true);
    }

    const dd = data.drawdown || [];
    const cd = chart('chart-dd');
    if (cd) {
      cd.setOption({
        backgroundColor: 'transparent', animation: false,
        tooltip: { trigger: 'axis', backgroundColor: 'rgba(15,21,32,.96)', borderColor: '#1f2b40', textStyle: { color: '#e9eef7', fontSize: 11 } },
        grid: { left: 52, right: 14, top: 12, bottom: 6 },
        xAxis: { type: 'category', data: dd.map((p) => p.date), axisLine: { lineStyle: { color: '#1f2b40' } }, axisLabel: { show: false }, axisTick: { show: false } },
        yAxis: { ...AXIS, axisLabel: { color: '#5c6b82', fontSize: 9, formatter: '{value}%' } },
        series: [{ type: 'line', data: dd.map((p) => p.dd), symbol: 'none', lineStyle: { width: 1, color: DOWN }, areaStyle: { color: 'rgba(34,197,139,.18)' } }],
      }, true);
    }

    const warn = $('#bt-warn');
    if (data.warnings && data.warnings.length) {
      warn.classList.remove('hidden');
      warn.innerHTML = data.warnings.map((w) => '· ' + esc(w)).join('<br>');
    } else warn.classList.add('hidden');

    const summary = [
      ['回测区间', st.start + ' ~ ' + st.end],
      ['交易日数', st.days + ' 天'],
      ['初始资金', '¥' + Number($('#bt-capital').value || 1000000).toLocaleString()],
      ['期末权益', '¥' + Math.round(st.final_equity).toLocaleString()],
      ['交易笔数', st.trades + ' 笔'],
      ['单笔平均', fmtPct(st.avg_trade, 2)],
      ['盈亏比', st.profit_factor != null ? st.profit_factor : '—'],
      ['平均持仓', fmt(st.avg_positions, 1) + ' 只'],
      ['产生信号', st.signals + ' 个'],
      ['沪深300', st.benchmark_return != null ? fmtPct(st.benchmark_return, 1) : '—'],
      ['超额收益', st.benchmark_return != null ? fmtPct(st.total_return - st.benchmark_return, 1) : '—'],
    ];
    $('#bt-summary').innerHTML = summary.map(([k, v]) => `<div class="kv"><span>${k}</span><b>${v}</b></div>`).join('');

    const trades = data.trades || [];
    $('#bt-trade-note').textContent = trades.length ? '最近 ' + trades.length + ' 笔' : '暂无成交';
    $('#tbl-trades tbody').innerHTML = trades.map((t) => `
      <tr>
        <td><div class="stock-cell"><span class="code">${esc(t.code)}</span><b>${esc(t.name || '')}</b></div></td>
        <td>${t.buy_date}</td>
        <td>${t.sell_date}</td>
        <td class="num">${fmt(t.buy_price, 2)}</td>
        <td class="num">${fmt(t.sell_price, 2)}</td>
        <td class="num ${cls(t.return_pct)}">${fmtPct(t.return_pct)}</td>
        <td class="num">${fmt(t.score, 1)}</td>
      </tr>`).join('');
  }

  /* ---------------- 自选 ---------------- */
  let watchTimer = null;
  let watchCodes = [];

  function watchStamp(dateStr, timeStr) {
    if (!timeStr) return { text: '—', live: false };
    if (dateStr && dateStr !== fmtDate(new Date())) {
      return { text: '收盘 ' + dateStr.slice(5) + ' ' + timeStr.slice(0, 5), live: false };
    }
    return { text: '实时 ' + timeStr, live: true };
  }

  function watchStampHTML(dateStr, timeStr) {
    const st = watchStamp(dateStr, timeStr);
    return (st.live ? '<span class="live-dot"></span>' : '') + st.text;
  }

  function applyQuotes(quotes) {
    Object.keys(quotes || {}).forEach((code) => {
      const tr = document.querySelector('#tbl-watch tr[data-code="' + code + '"]');
      if (!tr) return;
      const q = quotes[code];
      const p = tr.querySelector('[data-f="price"]');
      if (p && isNum(q.price)) p.textContent = price(q.price);
      const c = tr.querySelector('[data-f="pct"]');
      if (c && isNum(q.pct)) {
        c.textContent = fmtPct(q.pct);
        c.className = 'num ' + cls(q.pct);
      }
      const a = tr.querySelector('[data-f="amount"]');
      if (a && isNum(q.amount)) a.textContent = money(q.amount / 1e8);
    });
  }

  function pollInterval() {
    const now = new Date();
    const day = now.getDay();
    const hm = now.getHours() * 100 + now.getMinutes();
    const trading = day >= 1 && day <= 5 && hm >= 915 && hm <= 1505;
    return trading ? 15000 : 60000;
  }

  function startWatchPoll() {
    clearInterval(watchTimer);
    const tick = async () => {
      if (document.hidden || !watchCodes.length) return;
      try {
        const d = await api('/api/quote?codes=' + watchCodes.join(','));
        applyQuotes(d.quotes);
        if (d.quote_time) $('#wl-ts').innerHTML = watchStampHTML(d.quote_date, d.quote_time);
      } catch (e) { /* 静默失败，下一轮重试 */ }
    };
    tick();
    watchTimer = setInterval(tick, pollInterval());
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && watchCodes.length && location.hash.includes('watchlist')) {
      startWatchPoll();
    }
  });

  async function loadWatchlist() {
    clearInterval(watchTimer);
    try {
      const data = await api('/api/watchlist');
      const rows = data.rows || [];
      watchCodes = rows.map((r) => r.code);
      $('#wl-ts').innerHTML = data.live && data.quote_time
        ? watchStampHTML(data.quote_date, data.quote_time)
        : (data.spot_ts ? '快照 ' + data.spot_ts : '—');
      const tb = $('#tbl-watch tbody');
      tb.innerHTML = rows.map((r) => `
        <tr data-code="${esc(r.code)}">
          <td><div class="stock-cell"><span class="code">${esc(r.code)}</span><b>${esc(r.name)}</b></div></td>
          <td class="num" data-f="price">${price(r.price)}</td>
          <td class="num ${cls(r.pct)}" data-f="pct">${fmtPct(r.pct)}</td>
          <td class="num">${fmt(r.turnover)}%</td>
          <td class="num" data-f="amount">${money(r.amount)}</td>
          <td class="num">${money(r.mktcap)}</td>
          <td class="num">${fmt(r.pe)}</td>
          <td class="num ${cls(r.pct_60d)}">${fmtPct(r.pct_60d, 1)}</td>
          <td class="num"><button class="star on" data-del="${esc(r.code)}">★</button></td>
        </tr>`).join('');
      $('#wl-empty').classList.toggle('hidden', rows.length > 0);
      $$('tr', tb).forEach((tr) => tr.addEventListener('click', (e) => {
        if (e.target.closest('[data-del]')) return;
        openStock(tr.dataset.code);
      }));
      $$('[data-del]', tb).forEach((b) => b.addEventListener('click', async (e) => {
        e.stopPropagation();
        await api('/api/watchlist/' + b.dataset.del, { method: 'DELETE' });
        loadWatchlist();
        toast('已移出自选');
      }));
      if (rows.length) startWatchPoll();
    } catch (e) { toast(e.message, true); }
  }

  /* ---------------- 今日推荐 ---------------- */
  async function loadPicks() {
    try {
      const d = await api('/api/recommend?limit=12');
      renderPicks(d);
    } catch (e) {
      toast(e.message, true);
    }
  }

  function renderPicks(d) {
    const r = d.regime || {};
    const b = r.breadth || {};
    $('#picks-date').textContent = d.trade_date ? '交易日 ' + d.trade_date : '';
    const badge = $('#regime-badge');
    const regimeClass = { '进攻': 'attack', '防守': 'defend', '震荡': 'neutral' };
    badge.className = 'regime-badge ' + (regimeClass[r.label] || 'neutral');
    badge.textContent = r.label || '—';
    $('#regime-label').textContent = r.label ? r.label + '市' : '—';
    $('#regime-advice').textContent = r.advice || '';
    $('#regime-stats').innerHTML = `
      <span>上涨<b class="up">${b.up ?? '—'}</b></span>
      <span>下跌<b class="down">${b.down ?? '—'}</b></span>
      <span>涨停<b>${b.limit_up ?? '—'}</b></span>
      <span>涨跌比<b>${b.up_ratio ?? '—'}%</b></span>
      <span>平均涨跌<b class="${cls(b.avg_pct)}">${fmtPct(b.avg_pct)}</b></span>`;

    const lp = d.limit_pool || {};
    $('#pool-date').textContent = lp.date ? lp.date + ' 收盘' : '';
    const poolStats = [
      ['涨停', lp.count || 0, '家'],
      ['连板', lp.streak_count || 0, '家'],
      ['一字板', lp.one_word || 0, '家'],
      ['晋级率', lp.promotion_rate != null ? lp.promotion_rate + '%' : '—', ''],
    ];
    $('#pool-stats').innerHTML = poolStats.map(([k, v, u]) => `
      <div class="ps"><span>${k}</span><b>${v}</b><i>${u}</i></div>`).join('') +
      `<div class="ps-ind">${(lp.top_industries || []).map((x) => `<span class="pool-ind">${esc(x.name)} ${x.count}</span>`).join('') || '<span class="muted">—</span>'}</div>`;
    $('#pool-leaders').innerHTML = (lp.leaders || []).map((x, i) => `
      <div class="pool-leader" data-code="${esc(x.code)}">
        <span class="pl-rank">${i + 1}</span>
        <div class="pl-main"><b>${esc(x.name)}</b><span>${esc(x.shape)}${x.industry ? ' · ' + esc(x.industry) : ''}</span></div>
        <span class="pl-streak">${x.streak >= 2 ? x.streak + '板' : '首板'}</span>
      </div>`).join('') || '<span class="muted">昨日无涨停</span>';
    $$('#pool-leaders .pool-leader').forEach((el) => el.addEventListener('click', () => openStock(el.dataset.code)));

    $('#picks-strategies').innerHTML = (d.strategies || []).map((s) => `
      <div class="strategy-chip" data-preset="${esc(s.id)}">
        <b>${esc(s.name)}</b><span>${esc(s.desc)}</span>
      </div>`).join('') || '<span class="muted">暂无</span>';
    $$('#picks-strategies .strategy-chip').forEach((el) => el.addEventListener('click', () => {
      sessionStorage.setItem('pendingPreset', el.dataset.preset);
      location.hash = '#/screener';
    }));

    $('#picks-industries').innerHTML = (r.industries || []).slice(0, 6).map((x) => `
      <div class="strategy-chip">
        <b>${esc(x.name)}</b>
        <span class="pct ${cls(x.pct)}">${fmtPct(x.pct)} · ${x.count} 只</span>
      </div>`).join('') || '<span class="muted">暂无行业数据</span>';

    const allRows = d.rows || [];
    const poolRows = allRows.filter((x) => x.pool === 'limit');
    const generalRows = allRows.filter((x) => x.pool !== 'limit');
    $('#picks-pool-note').textContent = poolRows.length
      ? `共 ${poolRows.length} 只 · 按连板高度排序，附风险提示`
      : '昨日无涨停，本次全部来自全市场';
    $('#picks-general-note').textContent = `扫描 ${d.scanned || 0} 只 · 精选 ${generalRows.length} 只`;
    renderPickGrid('#picks-grid', poolRows, '昨日没有涨停股');
    renderPickGrid('#picks-grid2', generalRows, '暂无可推荐标的');
  }

  function renderPickGrid(selector, rows, emptyText) {
    const grid = $(selector);
    if (!grid) return;
    grid.innerHTML = rows.map((row, i) => `
      <div class="pick-card" data-code="${esc(row.code)}">
        <div class="pick-top">
          <span class="pick-rank">${i + 1}</span>
          <div class="pick-name"><b>${esc(row.name)}</b><span>${esc(row.code)} · ${esc(row.board)}</span></div>
          <span class="pick-score">${fmt(row.display_score != null ? row.display_score : row.score, 1)}</span>
        </div>
        <div class="pick-price">
          <span class="p ${cls(row.pct)}">${price(row.price)}</span>
          <span class="c ${cls(row.pct)}">${fmtPct(row.pct)}</span>
          <span class="ind">${esc(row.industry || '')}</span>
        </div>
        ${row.pool === 'limit' ? `<div class="pick-pool">昨日涨停 · ${row.streak >= 2 ? row.streak + '连板' : '首板'} · ${esc(row.shape || '')}</div>` : ''}
        <div class="pick-reasons">${(row.reasons || []).map((x) => `<span>${esc(x)}</span>`).join('')}</div>
        <div class="pick-risks">${(row.risks || []).map((x) => `<span>${esc(x)}</span>`).join('')}</div>
        <div class="pick-actions">
          <button class="btn ghost sm" data-act="kline">查看 K 线</button>
          <button class="btn ghost sm" data-act="watch" data-on="${row.watch ? '1' : ''}">${row.watch ? '★ 已自选' : '☆ 自选'}</button>
        </div>
      </div>`).join('') ||
      `<div class="empty"><div class="empty-icon">✦</div><p>${esc(emptyText)}</p></div>`;
    $$('.pick-card', grid).forEach((card) => {
      card.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-act]');
        if (!btn) { openStock(card.dataset.code); return; }
        if (btn.dataset.act === 'watch') toggleWatch(card.dataset.code, btn);
        else openStock(card.dataset.code);
      });
    });
  }

  /* ---------------- 涨停板 ---------------- */
  async function loadLimitUp(kind) {
    state.limitupKind = kind || 'up';
    $$('#limitup-toggle button').forEach((b) => b.classList.toggle('active', b.dataset.kind === state.limitupKind));
    try {
      const d = await api('/api/limitup?type=' + state.limitupKind);
      renderLimitUp(d);
    } catch (e) {
      toast(e.message, true);
    }
  }

  function renderLimitUp(d) {
    const up = d.kind === 'up';
    const st = d.stats || {};
    $('#limitup-date').textContent = d.trade_date ? '交易日 ' + d.trade_date : '';
    $('#limitup-title').textContent = up ? '涨停股' : '跌停股';
    const avg = d.rows.length ? d.rows.reduce((s, r) => s + (r.pct || 0), 0) / d.rows.length : 0;
    const cards = up
      ? [
        ['涨停家数', st.count || 0, '只'],
        ['连板家数', Math.max(0, (st.count || 0) - (st.dist ? st.dist[1] : 0)), '只'],
        ['最高连板', st.max_streak || 0, '板'],
        ['一字板', st.one_word || 0, '只'],
      ]
      : [
        ['跌停家数', st.count || 0, '只'],
        ['一字跌停', st.one_word || 0, '只'],
        ['涉及成交额', money(st.amount || 0), ''],
        ['平均跌幅', fmtPct(avg), ''],
      ];
    $('#limitup-stats').innerHTML = cards.map(([k, v, u]) => `
      <div class="stat wide"><div class="k">${k}</div><div class="v">${v}<span class="muted" style="font-size:11px;margin-left:4px">${u}</span></div></div>`).join('');
    $('#limitup-meta').textContent = `共 ${st.count || 0} 只 · 成交额 ${money(st.amount || 0)}`;

    const tb = $('#tbl-limitup tbody');
    tb.innerHTML = d.rows.map((r) => `
      <tr data-code="${esc(r.code)}">
        <td><div class="stock-cell"><span class="code">${esc(r.code)}</span><b>${esc(r.name)}${r.is_st ? '<span class="tag warn">ST</span>' : ''}</b></div></td>
        <td class="num">${price(r.price)}</td>
        <td class="num ${cls(r.pct)}">${fmtPct(r.pct)}</td>
        <td class="num">${up ? `<span class="streak-badge ${r.streak >= 2 ? 'hot' : ''}">${r.streak >= 2 ? r.streak + '连板' : '首板'}</span>` : '—'}</td>
        <td><span class="shape-tag ${r.one_word ? 'gold' : ''}">${esc(r.shape)}</span></td>
        <td><span class="tag">${esc(r.limit_label || '—')}</span></td>
        <td class="num">${fmt(r.turnover)}%</td>
        <td class="num">${money(r.amount)}</td>
        <td class="num">${money(r.float_mktcap)}</td>
        <td>${esc(r.industry || '—')}</td>
        <td class="num"><button class="star ${r.watch ? 'on' : ''}" data-star="${esc(r.code)}">${r.watch ? '★' : '☆'}</button></td>
      </tr>`).join('');
    $('#limitup-empty').classList.toggle('hidden', d.rows.length > 0);
    $$('#tbl-limitup tbody tr').forEach((tr) => tr.addEventListener('click', (e) => {
      if (e.target.closest('[data-star]')) return;
      openStock(tr.dataset.code);
    }));
    $$('#tbl-limitup [data-star]').forEach((b) => b.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleWatch(b.dataset.star, b);
    }));
  }

  async function toggleWatch(code, btn) {
    try {
      if (btn.dataset.on === '1') {
        await api('/api/watchlist/' + code, { method: 'DELETE' });
        btn.dataset.on = '';
        btn.textContent = '☆ 自选';
        btn.classList.remove('on');
        toast('已移出自选');
      } else {
        await api('/api/watchlist', { method: 'POST', body: { code } });
        btn.dataset.on = '1';
        btn.textContent = '★ 已自选';
        btn.classList.add('on');
        toast('已加入自选');
      }
    } catch (e) {
      toast(e.message, true);
    }
  }

  /* ---------------- 数据管理 ---------------- */
  async function loadDataStatus() {
    try {
      const b = await api('/api/bootstrap');
      state.boot = b;
      renderDataStats(b.stats);
      if (!b.sync.running) updateSyncUI(b.sync);
    } catch (e) { /* ignore */ }
  }

  function renderDataStats(st) {
    const cards = [
      ['股票数量', st.stocks || 0, '只'],
      ['日线记录', (st.bar_rows || 0).toLocaleString(), '条'],
      ['已覆盖', st.bar_codes || 0, '只股票'],
      ['技术指标', st.tech_codes || 0, '只缓存'],
    ];
    $('#data-stats').innerHTML = cards.map(([k, v, u]) => `<div class="stat wide"><div class="k">${k}</div><div class="v">${v}<span class="muted" style="font-size:11px;margin-left:4px">${u}</span></div></div>`).join('');
    const info = [
      ['K线区间', (st.bar_min_date || '—') + ' ~ ' + (st.bar_max_date || '—')],
      ['行情快照', st.spot_ts || '—'],
      ['最近同步', st.last_sync || '—'],
      ['数据库', 'data/ashare.db'],
    ];
    $('#db-info').innerHTML = info.map(([k, v]) => `<div class="kv"><span>${k}</span><b>${v}</b></div>`).join('');
  }

  function bindPicks() {
    $('#btn-picks-refresh').addEventListener('click', () => loadPicks());
    $$('#limitup-toggle button').forEach((b) => b.addEventListener('click', () => loadLimitUp(b.dataset.kind)));
  }

  function bindData() {
    $('#btn-sync').addEventListener('click', () => startSync('auto'));
    $('#btn-sync-full').addEventListener('click', () => {
      if (confirm('全量重建会重新抓取约 5500 只股票的日线，耗时 2–4 分钟，确定继续？')) startSync('full');
    });
    $('#btn-refresh').addEventListener('click', async () => {
      try {
        const r = await api('/api/spot/refresh', { method: 'POST' });
        if (!r.started) { toast('已有任务在执行'); return; }
        toast('正在刷新行情…');
        pollSync();
      } catch (e) { toast(e.message, true); }
    });
    $('#btn-vacuum').addEventListener('click', async () => {
      try { await api('/api/db/vacuum', { method: 'POST' }); toast('数据库已整理'); } catch (e) { toast(e.message, true); }
    });
  }

  async function startSync(mode) {
    try {
      const r = await api('/api/sync', { method: 'POST', body: { years: 2, mode } });
      if (!r.started) { toast('已有同步任务在执行'); return; }
      toast('同步任务已启动');
      pollSync();
    } catch (e) { toast(e.message, true); }
  }

  function updateSyncUI(st) {
    if (!st) return;
    const running = !!st.running;
    const pct = st.total ? Math.min(100, Math.round((st.done / st.total) * 100)) : (running ? 3 : (st.phase === '完成' ? 100 : 0));
    $('#sync-bar').style.width = pct + '%';
    $('#sync-pct').textContent = pct + '%';
    $('#sync-phase').textContent = running ? (st.phase || '同步中') : (st.phase === '失败' ? '失败' : '空闲');
    const msg = st.message || '—';
    $('#sync-msg').textContent = msg + (st.elapsed ? ` · 已用 ${st.elapsed}s` : '') + (st.failed ? ` · 失败 ${st.failed}` : '');
    const log = $('#sync-log');
    if (!st.started_at) {
      if (log.dataset.mode !== 'idle') {
        log.dataset.mode = 'idle';
        log.dataset.last = '';
        log.innerHTML = '<span style="color:#5c6b82">等待同步任务…</span>';
      }
    } else {
      log.dataset.mode = 'run';
      const key = st.phase + '|' + st.done + '|' + st.message;
      if (log.dataset.key !== key) {
        log.dataset.key = key;
        const line = `[${new Date().toLocaleTimeString()}] ${st.phase} ${st.done}/${st.total} ${st.message}`;
        log.insertAdjacentHTML('afterbegin', esc(line) + '<br>');
        while (log.childElementCount > 60) log.removeChild(log.lastChild);
      }
    }
    updateConn(true, running ? `同步中 ${pct}%` : '已连接');
    $('#btn-sync').disabled = running;
    $('#btn-sync-full').disabled = running;
    $('#btn-refresh').disabled = running;
  }

  async function pollSync() {
    clearTimeout(state.syncTimer);
    try {
      const st = await api('/api/sync/status');
      updateSyncUI(st);
      if (st.running) {
        state.syncTimer = setTimeout(pollSync, 1200);
      } else {
        onSyncDone(st);
      }
    } catch (e) {
      state.syncTimer = setTimeout(pollSync, 3000);
    }
  }

  async function onSyncDone(st) {
    updateConn(true);
    if (st.phase === '失败') { toast('同步失败：' + (st.message || ''), true); return; }
    if (st.phase === '完成') {
      toast(st.mode === 'spot' ? '行情已更新' : '数据同步完成');
      try {
        state.boot = await api('/api/bootstrap');
        renderDataStats(state.boot.stats);
        const view = (location.hash.replace('#/', '') || 'overview').split('?')[0];
        if (view === 'overview') loadOverview();
        if (view === 'picks') loadPicks();
        if (view === 'limitup') loadLimitUp(state.limitupKind);
        if (view === 'watchlist') loadWatchlist();
      } catch (e) { /* ignore */ }
    }
  }

  boot();
})();
