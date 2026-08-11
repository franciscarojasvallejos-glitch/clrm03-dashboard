// ==UserScript==
// @name         CLRM03 Dashboard — Auto Update (GitHub direct)
// @namespace    https://wms.adminml.com
// @version      3.0
// @description  Extrae totes y address de WMS y pushea directo a GitHub
// @match        https://wms.adminml.com/reports/totes*
// @match        https://wms.adminml.com/reports/address*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @connect      api.github.com
// ==/UserScript==

(function () {
  'use strict';

  const GITHUB_OWNER  = 'franciscarojasvallejos-glitch';
  const GITHUB_REPO   = 'clrm03-dashboard';
  const GITHUB_BRANCH = 'main';

  // ── UI badge ─────────────────────────────────────────────────────────────
  const badge = document.createElement('div');
  badge.style.cssText = `
    position:fixed;bottom:18px;right:18px;z-index:99999;
    background:#0d1117;color:#e6edf3;padding:10px 14px;
    border-radius:8px;font-family:monospace;font-size:12px;line-height:1.6;
    box-shadow:0 4px 16px rgba(0,0,0,0.5);min-width:260px;
    border:1px solid #3483FA;transition:opacity 0.4s;
  `;
  badge.innerHTML = `
    <div style="font-weight:bold;color:#3483FA;margin-bottom:4px">CLRM03 Dashboard</div>
    <div id="clrm03-msg">Iniciando...</div>
    <div id="clrm03-sub" style="color:#8b949e;font-size:11px"></div>
  `;

  function mountBadge() {
    const container = document.body || document.documentElement;
    if (!document.getElementById('clrm03-badge-root')) {
      badge.id = 'clrm03-badge-root';
      container.appendChild(badge);
    }
  }

  function msg(text, color = '#e6edf3', sub = '') {
    badge.querySelector('#clrm03-msg').style.color = color;
    badge.querySelector('#clrm03-msg').textContent  = text;
    badge.querySelector('#clrm03-sub').textContent  = sub;
  }

  // ── Token ────────────────────────────────────────────────────────────────
  function ensureToken() {
    const t = GM_getValue('gh_token_v2', '');
    if (t) return t;
    const input = prompt(
      'CLRM03 Dashboard\n\nIngresa tu GitHub Personal Access Token\n(Classic, scope: repo)\n\nGenerar en: github.com/settings/tokens'
    );
    if (input && input.trim()) {
      GM_setValue('gh_token_v2', input.trim());
      return input.trim();
    }
    return null;
  }

  // ── GitHub API ───────────────────────────────────────────────────────────
  function ghRequest(method, path, body, token) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method,
        url: `https://api.github.com${path}`,
        headers: {
          'Authorization': `Bearer ${token}`,
          'Accept': 'application/vnd.github+json',
          'Content-Type': 'application/json',
          'X-GitHub-Api-Version': '2022-11-28',
        },
        data: body ? JSON.stringify(body) : undefined,
        timeout: 30000,
        onload: r => {
          try {
            const parsed = JSON.parse(r.responseText);
            if (r.status >= 400) reject(new Error(parsed.message || `HTTP ${r.status}`));
            else resolve(parsed);
          } catch (e) { reject(new Error('Respuesta inválida de GitHub API')); }
        },
        onerror:   () => reject(new Error('Error de red al conectar a GitHub API')),
        ontimeout: () => reject(new Error('Timeout en GitHub API')),
      });
    });
  }

  async function pushToGitHub(filePath, data, token, commitMsg) {
    const content = btoa(unescape(encodeURIComponent(JSON.stringify(data, null, 0))));
    let sha;
    try {
      const current = await ghRequest('GET',
        `/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/${filePath}?ref=${GITHUB_BRANCH}`,
        null, token);
      sha = current.sha;
    } catch (e) { sha = undefined; }

    await ghRequest('PUT',
      `/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/${filePath}`,
      { message: commitMsg, content, branch: GITHUB_BRANCH, ...(sha ? { sha } : {}) },
      token);
  }

  // ── TOTES extractor ───────────────────────────────────────────────────────
  const RE_TOTES = /"address_id":"([^"]+)","inventory_id_list":\[.*?\],"fixed_position":"[^"]*","unlink":[^,]+,"unlink_user_allowed":[^,]+,"created_at":"([^"]+)","lost":[^,]+,"expire_at_date":"([^"]+)","sla":\{"status":"([^"]+)","time_left":"([^"]+)","process_time":"([^"]+)"\}/g;

  function extractTotes(html) {
    const out = [];
    let m;
    RE_TOTES.lastIndex = 0;
    while ((m = RE_TOTES.exec(html)) !== null) {
      out.push({ movable: m[1], created_at: m[2], expire_at_date: m[3],
                 sla_status: m[4], time_left: m[5], process_time: m[6] });
    }
    return out;
  }

  function detectPages(html) {
    const nums = [...html.matchAll(/[?&]page=(\d+)/g)].map(x => +x[1]);
    return nums.length ? Math.max(...nums) : 1;
  }

  async function fetchTotes() {
    const base = 'https://wms.adminml.com/reports/totes?sort=expire_at_date_asc&page=';
    const all  = [];
    const r0   = await fetch(base + '1', { credentials: 'include' });
    const h0   = await r0.text();
    const total = Math.min(detectPages(h0), 999);
    all.push(...extractTotes(h0));
    msg(`Totes: pág 1/${total}`, '#FFD700', `${all.length} totes...`);
    for (let p = 2; p <= total; p++) {
      try {
        const r = await fetch(base + p, { credentials: 'include' });
        all.push(...extractTotes(await r.text()));
      } catch (e) { console.warn('[CLRM03] totes pág', p, e); }
      if (p % 5 === 0) msg(`Totes: pág ${p}/${total}`, '#FFD700', `${all.length} totes...`);
      await new Promise(r => setTimeout(r, 150));
    }
    return all;
  }

  // ── ADDRESS extractor ─────────────────────────────────────────────────────
  function extractAddress(html) {
    const out    = [];
    const startRE = /"address_id":"((?:RK|BL)[^"]+)"/g;
    let m;
    while ((m = startRE.exec(html)) !== null) {
      const chunk  = html.substring(m.index, m.index + 2200);
      const stock  = (chunk.match(/"stock_quantity":(\d+)/)        || [])[1];
      const avail  = (chunk.match(/"own_available_quantity":(\d+)/) || [])[1];
      const res    = (chunk.match(/"reserved_quantity":(\d+)/)      || [])[1];
      const inv_m  = chunk.match(/"inventory_id_list":\[([^\]]*)\]/);
      const skus   = inv_m ? inv_m[1].replace(/"/g, '').split(',').filter(Boolean) : [];
      const w_m    = chunk.match(/"width":\{"value":([\d.]+)/);
      const h_m    = chunk.match(/"height":\{"value":([\d.]+)/);
      const l_m    = chunk.match(/"length":\{"value":([\d.]+)/);
      const kg_m   = chunk.match(/"weight":\{"value":([\d.]+)/);
      out.push({
        address_id: m[1],
        stock:    stock  ? +stock  : 0,
        available: avail ? +avail  : 0,
        reserved:  res   ? +res    : 0,
        skus,
        dim_w:  w_m  ? +w_m[1]  : null,
        dim_h:  h_m  ? +h_m[1]  : null,
        dim_l:  l_m  ? +l_m[1]  : null,
        dim_kg: kg_m ? +kg_m[1] : null,
      });
    }
    return out;
  }

  async function fetchAddress(limit = 50) {
    const all = [];
    let offset = 0, page = 1;
    while (true) {
      const url = `https://wms.adminml.com/reports/address?limit=${limit}&sort=address_id_asc&offset=${offset}`;
      try {
        const r = await fetch(url, { credentials: 'include' });
        const h = await r.text();
        const batch = extractAddress(h);
        if (batch.length === 0) break;
        all.push(...batch);
        msg(`Address: pág ${page}`, '#FFD700', `${all.length} ubicaciones...`);
        if (batch.length < limit) break;
        offset += limit;
        page++;
      } catch (e) { console.warn('[CLRM03] address offset', offset, e); break; }
      await new Promise(r => setTimeout(r, 150));
    }
    return all;
  }

  // ── Main ──────────────────────────────────────────────────────────────────
  async function main() {
    await new Promise(r => setTimeout(r, 1500));
    mountBadge();
    const url = location.href;

    const token = ensureToken();
    if (!token) { msg('⚠ Sin token GitHub', '#FF7A00', 'Recarga para ingresar token'); return; }

    try {
      const now = new Date().toLocaleString('es-CL', { timeZone: 'America/Santiago' });

      if (url.includes('/reports/totes')) {
        msg('Extrayendo totes...', '#FFD700');
        const data = await fetchTotes();
        msg(`Subiendo ${data.length} totes...`, '#FFD700', 'Conectando a GitHub');
        await pushToGitHub('data/wms_totes_completo.json', data, token,
          `wms: ${data.length} totes actualizados ${now}`);
        msg(`✓ ${data.length} totes → GitHub`, '#3fb950', 'Actions regenerando en ~1 min');

      } else if (url.includes('/reports/address')) {
        msg('Extrayendo ubicaciones...', '#FFD700');
        const data = await fetchAddress();
        msg(`Subiendo ${data.length} ubicaciones...`, '#FFD700', 'Conectando a GitHub');
        await pushToGitHub('data/wms_address_CLRM03.json', data, token,
          `wms: ${data.length} ubicaciones actualizadas ${now}`);
        msg(`✓ ${data.length} ubicaciones → GitHub`, '#3fb950', 'Actions regenerando en ~1 min');

      } else {
        msg('Página no reconocida', '#8b949e');
        return;
      }

      setTimeout(() => { badge.style.opacity = '0.25'; }, 15000);

    } catch (e) {
      if (e.message.includes('Bad credentials') || e.message.includes('401')) {
        GM_setValue('gh_token_v2', '');
        msg('⚠ Token inválido', '#FF7A00', 'Recarga para ingresar nuevo token');
      } else {
        msg('✗ ' + e.message, '#f85149');
      }
      console.error('[CLRM03]', e);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', main);
  } else {
    main();
  }

})();
