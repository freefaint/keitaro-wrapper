const form = document.querySelector('#create');
const campaign = document.querySelector('#campaign');
const flows = document.querySelector('#flows');
const status = document.querySelector('#status');
const statsNote = document.querySelector('#stats-note');
let catalog;

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
  const result = await response.json();
  if (!response.ok) {
    const detail = result.detail;
    throw new Error(Array.isArray(detail) ? detail.map(item => item.msg).join('; ') : detail);
  }
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
  options(campaign, rows, 'Выберите кампанию');
  campaign.value = String(selected);
  if (!campaign.value) campaign.selectedIndex = 0;
  await loadFlows();
}

async function loadFlows() {
  flows.replaceChildren();
  statsNote.textContent = '';
  if (!campaign.value) {
    const empty = element('p', 'Выберите кампанию, чтобы увидеть её потоки и офферы.');
    empty.className = 'empty';
    flows.append(empty);
    return;
  }
  const id = campaign.value;
  const data = await api('/campaigns/' + id);
  let stats;
  try {
    stats = (await api(`/campaigns/${id}/stats`)).rows;
    statsNote.textContent = 'Статистика за всё время · валюта трекера';
  } catch (err) {
    statsNote.textContent = 'Статистика недоступна. ' + err.message;
  }
  for (const flow of data.flows) {
    const box = element('div');
    box.className = flow.dirty ? 'flow dirty' : 'flow';
    box.append(element('h3', `${flow.name} · #${flow.id}`));
    if (!flow.editable) {
      box.append(element('p', 'Поток без офферов'));
      flows.append(box);
      continue;
    }
    const draftPath = `/campaigns/${id}/flows/${flow.id}/draft`;
    const act = (action, offerId) => run(async () => {
      await api(draftPath, 'POST', {action, offer_id: offerId});
      await loadFlows();
    });
    if (flow.dirty) {
      const actions = element('div');
      actions.className = 'actions';
      const publish = element('button', 'Опубликовать');
      const cancel = element('button', 'Отменить черновик');
      cancel.className = 'secondary';
      publish.addEventListener('click', () => act('publish'));
      cancel.addEventListener('click', () => act('cancel'));
      actions.append(element('span', 'Черновик'), publish, cancel);
      box.append(actions);
    }
    const table = element('table');
    const heading = table.createTHead().insertRow();
    for (const title of ['Оффер', 'Доля', 'Клики', 'Конверсии', 'Доход', 'Расход', 'Прибыль', '']) {
      heading.append(element('th', title));
    }
    const body = table.createTBody();
    const activeOffers = flow.offers.filter(offer => !['deleted', 'archived'].includes(offer.state));
    for (const offer of activeOffers) {
      const row = body.insertRow();
      if (offer.state === 'removed') row.className = 'removed';
      const name = catalog.offers.find(item => item.id === offer.offer_id)?.name ?? `#${offer.offer_id}`;
      const stat = stats?.find(item => +item.stream_id === flow.id && +item.offer_id === offer.offer_id);
      row.insertCell().textContent = name;
      row.insertCell().textContent = offer.state === 'disabled' ? 'Выключен' : offer.share + '%';
      for (const field of ['clicks', 'conversions', 'revenue', 'cost', 'profit']) {
        row.insertCell().textContent = stat?.[field] ?? (stats ? '0' : '—');
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
    wrap.append(table);
    box.append(wrap);
    const candidates = catalog.offers.filter(item => !activeOffers.some(offer => offer.offer_id === item.id));
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
  document.querySelector('#prefix').textContent = '· ' + catalog.prefix;
  for (const [field, list] of Object.entries({
    offer_id: 'offers', domain_id: 'domains', group_id: 'groups', traffic_source_id: 'sources'
  })) options(form.elements[field], catalog[list], 'Выберите значение');
  await loadCampaigns();
}

run(initialize);
