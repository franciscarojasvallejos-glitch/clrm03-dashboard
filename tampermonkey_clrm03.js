// ==UserScript==
// @name         CLRM03 Dashboard — Auto Update (GitHub direct)
// @namespace    https://wms.adminml.com
// @version      2.0
// @description  Extrae totes de WMS y pushea wms_totes_completo.json directo a GitHub
// @match        https://wms.adminml.com/reports/totes*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @connect      api.github.com
// ==/UserScript==

(function () {
  'use strict';

  // ── CONFIGURACIÓN ─────────────────────────────────────────────────────────
  const GITHUB_OWNER = 'franciscarojasvallejos-glitch';
  const GITHUB_REPO  = 'clrm03-dashboard';
  const GITHUB_FILE  = 'data/wms_totes_completo.json';
  const GITHUB_BRANCH = 'main';
  // Token GitHub con permiso "repo" (Contents: write)
  // Generar en: https://github.com/settings/tokens → Fine-grained → Contents: Read & Write
  const GITHUB_TOKEN = GM_getValue('gh_token', '');
  // ──────────────────────────────────────────────────────────────────────────

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
  document.body.appendChild(badge);

  function msg(text, color = '#e6edf3', sub = '') {
    badge.querySelector('#clrm03-msg').style.color = color;
    badge.querySelector('#clrm03-msg').textContent  = text;
    badge.querySelector('#clrm03-sub').textContent  = sub;
  }

  // ── Token check + prompt ─────────────────────────────────────────────────
  function ensureToken() {
    if (GITHUB_TOKEN) return GITHUB_TOKEN;
    const t = prompt(
      'CLRM03 Dashboard\n\nIngresa tu GitHub Personal Access Token\n' +
      '(Fine-grained, repo: Contents Read & Write)\n\n' +
      'Generar en: github.com/settings/tokens'
    );
    if (t && t.trim()) {
      GM_setValue('gh_token', t.trim());
      return t.trim();
    }
    return null;
  }

  // ── GitHub API helpers ───────────────────────────────────────────────────
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
          } catch (e) {
            reject(new Error('Respuesta inválida de GitHub API'));
          }
        },
        onerror:   () => reject(new Error('Error de red al conectar a GitHub API')),
        ontimeout: () => reject(new Error('Timeout en GitHub API')),
      });
    });
  }

  async function pushToGitHub(data, token) {
    const content = btoa(unescape(encodeURIComponent(JSON.stringify(data, null, 0))));
    // Obtener SHA actual del archivo (necesario para actualizar)
    let sha;
    try {
      const current = await ghRequest(
        'GET',
        `/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/${GITHUB_FILE}?ref=${GITHUB_BRANCH}`,
        null, token
      );
      sha = current.sha;
    } catch (e) {
      // El archivo no existe aún — se crea nuevo
      sha = undefined;
    }

    const now = new Date().toLocaleString('es-CL', { timeZone: 'America/Santiago' });
    await ghRequest(
      'PUT',
      `/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/${GITHUB_FILE}`,
      {
        message: `wms: totes actualizados ${now} (Tampermonkey)`,
        content,
        branch: GITHUB_BRANCH,
        ...(sha ? { sha } : {}),
      },
      token
    );
  }

  // ── Totes extractor ───────────────────────────────────────────────────────
  const RE_TOTES = /"address_id":"([^"]+)","inventory_id_list":\[.*?\],"fixed_position":"[^"]*","unlink":[^,]+,"unlink_user_allowed":[^,]+,"created_at":"([^"]+)","lost":[^,]+,"expire_at_date":"([^"]+)","sla":\{"status":"([^"]+)","time_left":"([^"]+)","process_time":"([^"]+)"\}/g;

  function extractTotes(html) {
    const out = [];
    let m;
    RE_TOTES.lastIndex = 0;
    while ((m = RE_TOTES.exec(html)) !== null) {
      out.push({
        movable:       m[1],
        created_at:    m[2],
        expire_at_date: m[3],
        sla_status:    m[4],
        time_left:     m[5],
        process_time:  m[6],
      });
    }
    return out;
  }

  // ── Detectar páginas totales ──────────────────────────────────────────────
  function detectPages(html) {
    const nums = [...html.matchAll(/[?&]page=(\d+)/g)].map(x => +x[1]);
    return nums.length ? Math.max(...nums) : 1;
  }

  // ── Paginator ─────────────────────────────────────────────────────────────
  async function fetchAllPages(maxPages = 999) {
    const base = 'https://wms.adminml.com/reports/totes?sort=expire_at_date_asc&page=';
    const all  = [];

    const r0    = await fetch(base + '1', { credentials: 'include' });
    const h0    = await r0.text();
    const total = Math.min(detectPages(h0), maxPages);
    all.push(...extractTotes(h0));
    msg(`Extrayendo: pág 1/${total}`, '#FFD700', `${all.length} totes...`);

    for (let p = 2; p <= total; p++) {
      try {
        const r = await fetch(base + p, { credentials: 'include' });
        all.push(...extractTotes(await r.text()));
      } catch (e) {
        console.warn('[CLRM03] pág', p, e);
      }
      if (p % 5 === 0) msg(`Extrayendo: pág ${p}/${total}`, '#FFD700', `${all.length} totes...`);
      await new Promise(r => setTimeout(r, 150));
    }
    return all;
  }

  // ── Main ──────────────────────────────────────────────────────────────────
  async function main() {
    await new Promise(r => setTimeout(r, 1500));

    if (!location.href.includes('/reports/totes')) {
      msg('Solo activo en /reports/totes', '#8b949e');
      return;
    }

    const token = ensureToken();
    if (!token) {
      msg('⚠ Sin token GitHub', '#FF7A00', 'Recarga para ingresar token');
      return;
    }

    try {
      msg('Extrayendo totes de WMS...', '#FFD700');
      const data = await fetchAllPages();
      msg(`Subiendo ${data.length} totes a GitHub...`, '#FFD700', 'Esto dispara Actions automáticamente');

      await pushToGitHub(data, token);

      msg(`✓ ${data.length} totes → GitHub`, '#3fb950', 'Actions regenerando dashboard en ~1 min');
      console.log('[CLRM03] Push exitoso:', data.length, 'totes');

      setTimeout(() => { badge.style.opacity = '0.25'; }, 15000);

    } catch (e) {
      if (e.message.includes('Bad credentials') || e.message.includes('401')) {
        GM_setValue('gh_token', ''); // limpiar token inválido
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
