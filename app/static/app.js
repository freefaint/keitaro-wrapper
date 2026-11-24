const form = document.querySelector('#create');
const campaign = document.querySelector('#campaign');
const flows = document.querySelector('#flows');
const status = document.querySelector('#status');
const statsNote = document.querySelector('#stats-note');
let catalog;
let campaignCount = 0;
const number = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2});

function message(text, error = false) {
  status.textContent = text;
  status.toggleAttribute('data-error', error);
}

async function api(path, method = 'GET', data) {
  const response = await fetch('/api' + path, {
    method,
    headers: data ? {'Content-Type': 'application/json'} : {},
    body: data ? JSON.stringify(data) : undefined
  });
  const result = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = result?.detail ?? `Ошибка запроса (HTTP ${response.status}).`;
    throw new Error(Array.isArray(detail) ? detail.map(item => item.msg).join('; ') : detail);
  }
  if (result === null) throw new Error('Сервер вернул пустой или некорректный ответ.');
  return result;
}

function options(select, rows, placeholder) {
  select.replaceChildren(new Option(placeholder, ''));
  for (const row of rows) select.add(new Option(`${row.name} · #${row.id}`, row.id));
}

function element(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
}

async function loadCampaigns(selected = campaign.value) {
  const rows = await api('/campaigns');
  campaignCount = rows.length;
  options(campaign, rows, rows.length ? 'Выберите кампанию' : 'Нет доступных кампаний');
  campaign.value = String(selected);
  if (!campaign.value) campaign.selectedIndex = 0;
  await loadFlows();
}

async function loadFlows() {
  flows.replaceChildren();
  statsNote.textContent = '';
  if (!campaign.value) {
    const empty = element('p', campaignCount
      ? 'Выберите кампанию, чтобы увидеть её потоки и офферы.'
      : 'В списке пока нет ваших тестовых кампаний. Заполните форму «Новая кампания», чтобы создать первую.');
    empty.className = 'empty';
    flows.append(empty);
    return;
  }
  const id = campaign.value;
  const loading = element('p', 'Загружаем потоки и статистику…');
  loading.className = 'empty';
  flows.append(loading);
  let data;
  try {
    data = await api('/campaigns/' + id);
  } catch (error) {
    loading.textContent = 'Не удалось открыть кампанию. Попробуйте обновить список.';
    throw error;
  }
  let stats;
  try {
    stats = (await api(`/campaigns/${id}/stats`)).rows;
    statsNote.textContent = 'Статистика за всё время · валюта трекера';
  } catch (err) {
    statsNote.textContent = 'Статистика недоступна. ' + err.message;
  }
  flows.replaceChildren();
  for (const flow of data.flows) {
    const box = element('article');
    box.className = 'flow';
    box.classList.toggle('dirty', flow.dirty);
    box.classList.toggle('dim', !flow.editable);
    const headingWrap = element('div');
    headingWrap.className = 'flow-heading';
    const flowId = element('span', `#${flow.id}`);
    flowId.className = 'flow-id';
    headingWrap.append(element('h3', flow.name), flowId);
    box.append(headingWrap);
    if (!flow.editable) {
      box.append(element('p', 'Поток без офферов. Его переходы настраиваются отдельно в Keitaro.'));
      flows.append(box);
      continue;
    }
    const draftPath = `/campaigns/${id}/flows/${flow.id}/draft`;
    const act = (action, offerId) => run(async () => {
      await api(draftPath, 'POST', {action, offer_id: offerId});
      await loadFlows();
      message({
        publish: 'Изменения опубликованы в Keitaro.',
        cancel: 'Черновик отменён.',
      }[action] ?? 'Изменения сохранены в черновике.');
    });
    if (flow.dirty) {
      const actions = element('div');
      actions.className = 'draft-banner';
      const publish = element('button', 'Опубликовать');
      const cancel = element('button', 'Отменить черновик');
      cancel.className = 'secondary';
      publish.addEventListener('click', () => act('publish'));
      cancel.addEventListener('click', () => act('cancel'));
      actions.append(element('span', 'Есть неопубликованные изменения'), publish, cancel);
      box.append(actions);
    }
    const table = element('table');
    const heading = table.createTHead().insertRow();
    for (const title of ['Оффер', 'Доля', 'Клики', 'Конверсии', 'Доход', 'Расход', 'Прибыль', '']) {
      const cell = element('th', title || 'Действия');
      cell.scope = 'col';
      if (['Доля', 'Клики', 'Конверсии', 'Доход', 'Расход', 'Прибыль'].includes(title)) cell.className = 'stat';
      heading.append(cell);
    }
    const body = table.createTBody();
    const shownOffers = flow.offers.filter(offer => !['deleted', 'archived'].includes(offer.state));
    if (!shownOffers.length) {
      const cell = body.insertRow().insertCell();
      cell.colSpan = heading.cells.length;
      cell.textContent = 'В этом потоке пока нет офферов.';
    }
    for (const offer of shownOffers) {
      const row = body.insertRow();
      if (offer.state === 'removed') row.className = 'removed';
      const name = catalog.offers.find(item => item.id === offer.offer_id)?.name ?? `#${offer.offer_id}`;
      const stat = stats?.find(item => +item.stream_id === flow.id && +item.offer_id === offer.offer_id);
      const offerCell = row.insertCell();
      offerCell.textContent = name;
      const state = offer.state === 'removed' ? 'В архиве' : offer.state === 'disabled' ? 'Выключен' : offer.pinned ? 'Доля закреплена' : '';
      if (state) {
        const badge = element('small', state);
        badge.className = 'offer-state';
        offerCell.append(badge);
      }
      const shareCell = row.insertCell();
      shareCell.className = 'stat';
      shareCell.textContent = offer.state === 'disabled' ? '—' : number.format(offer.share) + '%';
      for (const field of ['clicks', 'conversions', 'revenue', 'cost', 'profit']) {
        const cell = row.insertCell();
        cell.className = 'stat';
        const value = stat?.[field] ?? (stats ? 0 : null);
        cell.textContent = value === null ? '—' : Number.isFinite(Number(value)) ? number.format(Number(value)) : String(value);
      }
      const buttons = row.insertCell();
      buttons.className = 'actions';
      const removed = offer.state === 'removed';
      const remove = element('button', removed ? 'Вернуть' : 'Убрать');
      remove.className = 'secondary';
      remove.addEventListener('click', () => act(removed ? 'restore' : 'remove', offer.offer_id));
      buttons.append(remove);
      if (offer.state === 'active') {
        const pin = element('button', offer.pinned ? 'Открепить долю' : 'Закрепить долю');
        pin.className = 'secondary';
        pin.addEventListener('click', () => act(offer.pinned ? 'unpin' : 'pin', offer.offer_id));
        buttons.append(pin);
      }
    }
    const wrap = element('div');
    wrap.className = 'table-wrap';
    wrap.tabIndex = 0;
    wrap.setAttribute('role', 'region');
    wrap.setAttribute('aria-label', `Статистика и офферы: ${flow.name}`);
    wrap.append(table);
    box.append(wrap);
    const candidates = catalog.offers.filter(item => !shownOffers.some(offer => offer.offer_id === item.id));
    if (!candidates.length) {
      box.append(element('p', catalog.offers.length
        ? 'Все доступные офферы уже есть в потоке или его архиве.'
        : 'Keitaro не вернул доступных офферов.'));
      flows.append(box);
      continue;
    }
    const addForm = element('form');
    const select = element('select');
    select.required = true;
    select.setAttribute('aria-label', 'Оффер для добавления');
    options(select, candidates, 'Выберите оффер');
    addForm.append(select, element('button', 'Добавить'));
    addForm.addEventListener('submit', event => {
      event.preventDefault();
      act('add', Number(select.value));
    });
    box.append(addForm);
    flows.append(box);
  }
  if (!data.flows.length) flows.textContent = 'В кампании нет потоков с офферами.';
}

let busy = false;
async function run(action) {
  if (busy) return;
  busy = true;
  const controls = [...document.querySelectorAll('button, select, input')];
  const enabled = controls.filter(control => !control.disabled);
  enabled.forEach(control => control.disabled = true);
  message('Выполняется запрос…');
  try {
    await action();
    if (status.textContent === 'Выполняется запрос…') message('');
  } catch (error) {
    message(error.message, true);
  } finally {
    enabled.forEach(control => control.disabled = false);
    form.querySelector('button').disabled = !catalog || ['domains', 'groups', 'sources', 'offers'].some(key => !catalog[key].length);
    campaign.disabled = !campaignCount;
    busy = false;
  }
}

form.addEventListener('submit', event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(form));
  run(async () => {
    for (const key of ['offer_id', 'domain_id', 'group_id', 'traffic_source_id']) data[key] = Number(data[key]);
    const created = await api('/campaigns', 'POST', data);
    form.reset();
    await loadCampaigns(created.id);
    message(`Создана кампания ${created.name} · #${created.id}`);
  });
});
campaign.addEventListener('change', () => run(loadFlows));
document.querySelector('#reload').addEventListener('click', () => run(initialize));

async function initialize() {
  catalog = await api('/catalog');
  document.querySelector('#demo-note').hidden = !catalog.demo;
  document.querySelector('#prefix').textContent = 'Префикс имени: ' + catalog.prefix;
  for (const [field, list] of Object.entries({
    offer_id: 'offers', domain_id: 'domains', group_id: 'groups', traffic_source_id: 'sources'
  })) options(form.elements[field], catalog[list], 'Выберите значение');
  await loadCampaigns();
}

run(initialize);
