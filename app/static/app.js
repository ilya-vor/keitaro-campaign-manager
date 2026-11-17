/* Keitaro Campaign Manager — минималистичный SPA без сборки. */
'use strict';

const state = {
  config: null,
  dicts: { offers: [], domains: [], groups: [], sources: [] },
  filter: { search: '', onlyOurs: true },
  detail: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
  ));
}

function toast(message, type = 'ok') {
  const node = document.createElement('div');
  node.className = `toast toast-${type}`;
  node.textContent = message;
  $('#toasts').appendChild(node);
  setTimeout(() => node.remove(), type === 'err' ? 8000 : 4000);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = null;
  }
  if (!response.ok) {
    const detail = data && data.detail ? data.detail : `HTTP ${response.status}`;
    const suffix = data && data.keitaro && data.keitaro.path ? ` (${data.keitaro.method} ${data.keitaro.path})` : '';
    throw new Error(detail + suffix);
  }
  return data;
}

/* ------------------------------------------------------------------ health */
async function refreshHealth() {
  const pill = $('#apiStatus');
  const banner = $('#apiBanner');
  pill.textContent = 'API: проверка…';
  pill.className = 'pill pill-muted';
  pill.title = 'нажмите, чтобы проверить ключ ещё раз';
  try {
    const health = await api('/api/health');
    state.health = health;
    if (health.keitaro_ok) {
      pill.textContent = 'API: ok';
      pill.className = 'pill pill-ok';
      pill.title = `ключ ${health.api_key_hint} · ${health.keitaro_base_url}`;
      banner.classList.add('hidden');
      banner.textContent = '';
    } else {
      pill.textContent = health.keitaro_status_code ? `API: ошибка ${health.keitaro_status_code}` : 'API: не настроен';
      pill.className = 'pill pill-err';
      pill.title = health.keitaro_error || '';
      banner.textContent = `Keitaro: ${health.keitaro_error || 'неизвестная ошибка'} · ключ ${health.api_key_hint} · ${health.keitaro_base_url}`;
      banner.classList.remove('hidden');
    }
  } catch (error) {
    pill.textContent = 'API: недоступен';
    pill.className = 'pill pill-err';
    pill.title = error.message;
    banner.textContent = error.message;
    banner.classList.remove('hidden');
  }
}

/* ------------------------------------------------------------------ routing */
function route() {
  const hash = location.hash || '#/campaigns';
  const campaignMatch = hash.match(/^#\/campaign\/(\d+)/);
  $$('.tabs a').forEach((tab) => tab.classList.remove('active'));
  $$('.view').forEach((view) => view.classList.add('hidden'));

  if (campaignMatch) {
    $('#view-campaign').classList.remove('hidden');
    openCampaign(Number(campaignMatch[1]));
    return;
  }
  if (hash.startsWith('#/logs')) {
    $('#view-logs').classList.remove('hidden');
    $$('.tabs a')[1].classList.add('active');
    loadLogs();
    return;
  }
  $('#view-campaigns').classList.remove('hidden');
  $$('.tabs a')[0].classList.add('active');
  loadCampaigns();
}

/* ------------------------------------------------------------------ config */
async function init() {
  try {
    state.config = await api('/api/config');
  } catch (error) {
    toast(error.message, 'err');
  }

  await refreshHealth();
  $('#apiStatus').addEventListener('click', refreshHealth);

  await loadDictionaries();

  $('#refreshBtn').addEventListener('click', loadCampaigns);
  $('#refreshLogs').addEventListener('click', loadLogs);
  $('#newCampaignBtn').addEventListener('click', () => $('#createForm').classList.toggle('hidden'));
  $('#createCancel').addEventListener('click', () => $('#createForm').classList.add('hidden'));
  $('#createForm').addEventListener('submit', onCreateSubmit);
  $('#geoQuick').addEventListener('click', (event) => {
    const chip = event.target.closest('.chip');
    if (!chip) return;
    const input = $('#createForm [name="geo"]');
    const code = chip.dataset.code;
    const current = input.value.split(/[,\s]+/).filter(Boolean);
    if (current.includes(code)) {
      input.value = current.filter((item) => item !== code).join(', ');
    } else {
      input.value = [...current, code].join(', ');
    }
  });
  $('#createForm').addEventListener('click', async (event) => {
    const trigger = event.target.closest('[data-create]');
    if (!trigger) return;
    const kind = trigger.dataset.create;
    const label = kind === 'groups' ? 'группы' : 'источника трафика';
    const name = prompt(`Название ${label}:`);
    if (!name) return;
    try {
      await api('/api/dictionaries', { method: 'POST', body: JSON.stringify({ kind, name }) });
      toast(`Создано: ${name}`);
      await loadDictionaries();
    } catch (error) {
      toast(error.message, 'err');
    }
  });

  let timer = null;
  $('#searchInput').addEventListener('input', (event) => {
    state.filter.search = event.target.value;
    clearTimeout(timer);
    timer = setTimeout(loadCampaigns, 250);
  });
  $('#onlyOurs').addEventListener('change', (event) => {
    state.filter.onlyOurs = event.target.checked;
    loadCampaigns();
  });

  renderGeoQuick();
  route();
}

function renderGeoQuick() {
  const codes = ['AU', 'RO', 'DE', 'MX', 'BR', 'IN', 'PH', 'US'];
  $('#geoQuick').innerHTML = codes
    .map((code) => `<button type="button" class="chip" data-code="${code}">${code}</button>`)
    .join('');
}

/* ------------------------------------------------------------ dictionaries */
async function loadDictionaries(refresh = false) {
  try {
    state.dicts = await api(`/api/dictionaries${refresh ? '?refresh=1' : ''}`);
  } catch (error) {
    toast(`Справочники недоступны: ${error.message}`, 'err');
    state.dicts = state.dicts || { offers: [], domains: [], groups: [], sources: [] };
  }

  const offersList = $('#offersList');
  offersList.innerHTML = state.dicts.offers
    .map((offer) => `<option value="${offer.id} — ${esc(offer.name)}"></option>`)
    .join('');

  const fill = (select, items, defaultId) => {
    if (!select) return;
    select.innerHTML = items
      .map((item) => {
        const selected = String(item.id) === String(defaultId) ? ' selected' : '';
        return `<option value="${item.id}"${selected}>${esc(item.name || item.id)} (#${item.id})</option>`;
      })
      .join('');
    if (!items.length) select.innerHTML = '';
  };

  const form = $('#createForm');
  fill(form.querySelector('[name="domain_id"]'), state.dicts.domains, state.config?.default_domain_id);
  fill(form.querySelector('[name="group_id"]'), state.dicts.groups, state.config?.default_group_id);
  fill(form.querySelector('[name="traffic_source_id"]'), state.dicts.sources, state.config?.default_traffic_source_id);
}

function offerName(offerId) {
  const found = (state.dicts.offers || []).find((item) => Number(item.id) === Number(offerId));
  return found ? found.name : `offer ${offerId}`;
}

/* ---------------------------------------------------------------- campaigns */
async function loadCampaigns() {
  const wrap = $('#campaignsWrap');
  const params = new URLSearchParams({
    search: state.filter.search,
    only_ours: String(state.filter.onlyOurs),
  });
  wrap.innerHTML = '<div class="empty">Загрузка…</div>';
  try {
    const data = await api(`/api/campaigns?${params}`);
    renderCampaigns(data.items);
  } catch (error) {
    wrap.innerHTML = `<div class="empty">Ошибка: ${esc(error.message)}</div>`;
  }
}

function renderCampaigns(items) {
  const wrap = $('#campaignsWrap');
  if (!items.length) {
    wrap.innerHTML = '<div class="empty">Кампаний нет. Создайте первую кнопкой «Создать кампанию».</div>';
    return;
  }
  const rows = items
    .map((item) => `
      <tr>
        <td>
          <div class="camp-name"><a class="link" href="#/campaign/${item.id}">${esc(item.name)}</a></div>
          <div class="camp-alias">#${item.id} · ${esc(item.alias || '')}</div>
        </td>
        <td>${item.geo ? item.geo.split(',').map((code) => `<span class="badge">${esc(code)}</span>`).join(' ') : '<span class="muted">—</span>'}</td>
        <td>${item.offer_id ? `<span class="badge badge-muted">#${item.offer_id}</span>` : '<span class="muted">—</span>'}</td>
        <td class="num">${item.clicks}</td>
        <td class="num">${item.uniques}</td>
        <td class="num">${item.bots}</td>
        <td><span class="badge ${item.state === 'active' ? 'badge-ok' : 'badge-warn'}">${esc(item.state || '—')}</span></td>
        <td><a class="btn btn-sm" href="#/campaign/${item.id}">Открыть</a></td>
      </tr>`)
    .join('');
  wrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Кампания</th><th>Гео</th><th>Оффер</th>
          <th class="num">Клики</th><th class="num">Уник.</th><th class="num">Боты</th>
          <th>Статус</th><th></th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function onCreateSubmit(event) {
  event.preventDefault();
  const form = event.target;
  const button = $('#createSubmit');
  const data = new FormData(form);
  const offerId = String(data.get('offer') || '').match(/^\s*(\d+)/);
  if (!offerId) {
    toast('Выберите оффер из подсказок (нужен id)', 'err');
    return;
  }
  const payload = {
    name: data.get('name'),
    geo: data.get('geo'),
    offer_id: Number(offerId[1]),
    domain_id: numberOrNull(data.get('domain_id')),
    group_id: numberOrNull(data.get('group_id')),
    traffic_source_id: numberOrNull(data.get('traffic_source_id')),
  };
  button.disabled = true;
  button.textContent = 'Создаём…';
  try {
    const detail = await api('/api/campaigns', { method: 'POST', body: JSON.stringify(payload) });
    toast(`Кампания «${detail.name}» создана (id ${detail.id})`);
    form.reset();
    form.classList.add('hidden');
    location.hash = `#/campaign/${detail.id}`;
  } catch (error) {
    toast(error.message, 'err');
  } finally {
    button.disabled = false;
    button.textContent = 'Создать кампанию';
  }
}

function numberOrNull(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}

/* ------------------------------------------------------------------- detail */
async function openCampaign(campaignId) {
  const view = $('#view-campaign');
  view.innerHTML = '<div class="card"><div class="empty">Загрузка кампании…</div></div>';
  try {
    const detail = await api(`/api/campaigns/${campaignId}`);
    state.detail = detail;
    renderDetail(detail);
  } catch (error) {
    view.innerHTML = `<div class="card"><div class="empty">Ошибка: ${esc(error.message)}</div>
      <div class="card-actions"><a class="btn" href="#/campaigns">← К списку</a></div></div>`;
  }
}

function renderDetail(detail) {
  const googleFlow = detail.flows.find((flow) => flow.schema === 'redirect');
  const offersFlow = detail.flows.find((flow) => flow.schema === 'landings');
  const totals = detail.flows.reduce(
    (acc, flow) => ({ clicks: acc.clicks + flow.clicks, uniques: acc.uniques + flow.uniques, bots: acc.bots + flow.bots }),
    { clicks: 0, uniques: 0, bots: 0 },
  );

  const geoBadges = detail.geo.length
    ? detail.geo.map((code) => `<span class="badge">${esc(code)}</span>`).join(' ')
    : '<span class="muted">—</span>';

  const view = $('#view-campaign');
  view.innerHTML = `
    <div class="toolbar"><a class="btn" href="#/campaigns">← Все кампании</a>
      <button class="btn" id="reloadDetail">Обновить</button>
      <span class="muted">данные из Keitaro API</span>
    </div>

    <div class="card">
      <div class="camp-header">
        <div>
          <h1>${esc(detail.name)}</h1>
          <div class="meta">
            <span>id <b>${detail.id}</b></span>
            <span>alias <b>${esc(detail.alias || '—')}</b></span>
            <span>гео ${geoBadges}</span>
            <span>домен <b>${esc(detail.domain || '—')}</b></span>
            <span>группа <b>${esc(detail.group || detail.group_id || '—')}</b></span>
            <span>статус <b>${esc(detail.state || '—')}</b></span>
          </div>
          ${detail.url ? `<div class="meta" style="margin-top:8px">ссылка кампании: <a class="link" href="${esc(detail.url)}" target="_blank" rel="noopener">${esc(detail.url)}</a></div>` : ''}
        </div>
        <div class="flow-stats">
          <div><b>${totals.clicks}</b>клики</div>
          <div><b>${totals.uniques}</b>уникальные</div>
          <div><b>${totals.bots}</b>боты</div>
        </div>
      </div>
    </div>

    ${googleFlow ? renderGoogleFlow(googleFlow) : ''}
    ${offersFlow ? renderOffersFlow(offersFlow, detail) : '<div class="card"><div class="empty">В кампании нет потока с офферами (schema=landings).</div></div>'}
  `;

  $('#reloadDetail').addEventListener('click', () => openCampaign(detail.id));
  if (offersFlow) bindOffersFlow(offersFlow, detail);
}

function renderGoogleFlow(flow) {
  const filters = (flow.filters || [])
    .map((filter) => {
      const payload = Array.isArray(filter.payload) ? filter.payload.join(', ') : filter.payload;
      return `<span class="badge badge-muted">${esc(filter.name)}: ${esc(filter.mode || '')} ${esc(payload || '')}</span>`;
    })
    .join(' ');
  return `
    <div class="flow">
      <div class="flow-head">
        <h3>Поток ${flow.position || ''} · ${esc(flow.name)}</h3>
        <span class="badge">${esc(flow.schema)}</span>
        <span class="badge badge-muted">${esc(flow.action_type)}</span>
        <span class="badge ${flow.state === 'active' ? 'badge-ok' : 'badge-warn'}">${esc(flow.state || '—')}</span>
      </div>
      <div class="flow-sub">
        редирект на: ${flow.target_url ? `<code>${esc(flow.target_url)}</code>` : '<span class="muted">url не задан</span>'}
        ${filters ? ` · фильтры: ${filters}` : ''}
      </div>
      <div class="flow-stats">
        <div><b>${flow.clicks}</b>клики</div>
        <div><b>${flow.uniques}</b>уникальные</div>
        <div><b>${flow.bots}</b>боты</div>
      </div>
    </div>`;
}

function renderOffersFlow(flow, detail) {
  const rows = flow.offers
    .map((offer) => `
      <tr data-offer-row="${offer.offer_id}">
        <td>
          <div class="camp-name">${esc(offer.name)}</div>
          <div class="camp-alias">offer_id ${offer.offer_id}</div>
        </td>
        <td>
          <input class="share-input" type="number" min="0" max="100" step="1"
                 data-offer-id="${offer.offer_id}" value="${offer.share}">
        </td>
        <td class="num">${offer.clicks}</td>
        <td class="num">${offer.uniques}</td>
        <td class="num">${offer.bots}</td>
        <td>
          <button class="btn btn-sm btn-danger" data-remove-offer="${offer.offer_id}">Удалить</button>
        </td>
      </tr>`)
    .join('');

  return `
    <div class="flow" id="offersFlow">
      <div class="flow-head">
        <h3>Поток ${flow.position || ''} · ${esc(flow.name)}</h3>
        <span class="badge">${esc(flow.schema)}</span>
        <span class="badge badge-muted">${esc(flow.action_type)}</span>
        <span class="badge badge-muted">поток ${flow.id}</span>
      </div>
      <div class="flow-sub">ротация офферов: веса в процентах, сумма всегда приводится к 100</div>

      <table>
        <thead>
          <tr>
            <th>Оффер</th><th>Вес, %</th>
            <th class="num">Клики</th><th class="num">Уник.</th><th class="num">Боты</th><th></th>
          </tr>
        </thead>
        <tbody id="offersBody">${rows || '<tr><td colspan="6" class="empty">В потоке нет офферов</td></tr>'}</tbody>
      </table>

      <div class="offer-add">
        <input class="input" id="addOfferInput" list="detailOffersList" placeholder="Оффер: id или название">
        <datalist id="detailOffersList">
          ${(detail.offers_catalog || []).map((offer) => `<option value="${offer.id} — ${esc(offer.name)}"></option>`).join('')}
        </datalist>
        <input class="share-input" id="addOfferShare" type="number" min="0" max="100" placeholder="%">
        <button class="btn btn-primary" id="addOfferBtn">＋ Добавить оффер</button>
        <button class="btn" id="saveSharesBtn">Сохранить веса</button>
      </div>
    </div>`;
}

function bindOffersFlow(flow, detail) {
  $('#addOfferBtn').addEventListener('click', async () => {
    const raw = $('#addOfferInput').value || '';
    const match = raw.match(/^\s*(\d+)/);
    if (!match) {
      toast('Выберите оффер из списка (нужен id)', 'err');
      return;
    }
    const offerId = Number(match[1]);
    if (flow.offers.some((offer) => offer.offer_id === offerId)) {
      toast(`Оффер ${offerId} уже есть в потоке`, 'err');
      return;
    }
    const shareRaw = $('#addOfferShare').value;
    const share = shareRaw === '' ? null : Number(shareRaw);
    const button = $('#addOfferBtn');
    button.disabled = true;
    try {
      const updated = await api(`/api/campaigns/${detail.id}/flows/${flow.id}/offers`, {
        method: 'POST',
        body: JSON.stringify({ offer_id: offerId, share }),
      });
      nextDetail(updated);
      toast(`Оффер ${offerId} добавлен`);
    } catch (error) {
      toast(error.message, 'err');
    } finally {
      button.disabled = false;
    }
  });

  $('#saveSharesBtn').addEventListener('click', async () => {
    const offers = $$('.share-input[data-offer-id]').map((input) => ({
      offer_id: Number(input.dataset.offerId),
      share: input.value === '' ? null : Number(input.value),
    }));
    if (!offers.length) {
      toast('В потоке нет офферов', 'err');
      return;
    }
    const button = $('#saveSharesBtn');
    button.disabled = true;
    try {
      const updated = await api(`/api/campaigns/${detail.id}/flows/${flow.id}/offers`, {
        method: 'PUT',
        body: JSON.stringify({ offers }),
      });
      nextDetail(updated);
      toast('Веса сохранены (сумма приведена к 100%)');
    } catch (error) {
      toast(error.message, 'err');
    } finally {
      button.disabled = false;
    }
  });

  $('#offersBody').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-remove-offer]');
    if (!button) return;
    const offerId = Number(button.dataset.removeOffer);
    if (!confirm(`Удалить оффер ${offerId} из потока?`)) return;
    button.disabled = true;
    try {
      const updated = await api(`/api/campaigns/${detail.id}/flows/${flow.id}/offers/${offerId}`, {
        method: 'DELETE',
      });
      nextDetail(updated);
      toast(`Оффер ${offerId} удалён`);
    } catch (error) {
      toast(error.message, 'err');
      button.disabled = false;
    }
  });
}

function nextDetail(detail) {
  state.detail = detail;
  renderDetail(detail);
}

/* --------------------------------------------------------------------- logs */
async function loadLogs() {
  const wrap = $('#logsWrap');
  wrap.innerHTML = '<div class="empty">Загрузка…</div>';
  try {
    const data = await api('/api/logs?limit=100');
    if (!data.items.length) {
      wrap.innerHTML = '<div class="empty">Журнал пуст — приложение ещё не обращалось к Keitaro.</div>';
      return;
    }
    const rows = data.items.map((item) => `
      <tr>
        <td>${esc(item.created_at || '')}</td>
        <td><span class="badge ${item.status === 'ok' ? 'badge-ok' : 'badge-warn'}">${esc(item.status)}</span></td>
        <td>${esc(item.action)}</td>
        <td>${esc(item.entity)}${item.entity_id ? ` #${esc(item.entity_id)}` : ''}</td>
        <td><code>${esc(item.method)} ${esc(item.path)}</code></td>
        <td>
          ${item.error ? `<div class="muted">${esc(item.error)}</div>` : ''}
          <details>
            <summary>payload</summary>
            <pre>${esc(JSON.stringify({ request: item.request_body, response: item.response_body }, null, 2))}</pre>
          </details>
        </td>
      </tr>`).join('');
    wrap.innerHTML = `
      <table>
        <thead><tr><th>Время</th><th>Статус</th><th>Действие</th><th>Объект</th><th>Запрос</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  } catch (error) {
    wrap.innerHTML = `<div class="empty">Ошибка: ${esc(error.message)}</div>`;
  }
}

window.addEventListener('hashchange', route);
window.addEventListener('DOMContentLoaded', init);
