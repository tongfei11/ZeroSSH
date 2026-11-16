const element = (id) => document.getElementById(id);
const hostList = element('host-list');
const terminalNode = element('terminal');
const dialog = element('host-dialog');
const form = element('host-form');
let state = {hosts: [], sessions: []};
let activeId;
let socket;
let terminal;
let fitter;
let input;
let editing;
let starting = false;

function showNotice(message = '') {
  element('notice').textContent = message;
}

async function request(path, method = 'GET', body) {
  const options = {method};
  if (body !== undefined) {
    options.headers = {'Content-Type': 'application/json'};
    options.body = JSON.stringify(body);
  }
  const response = await fetch('/api/' + path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '操作失败');
  return data;
}

function node(tag, className, label) {
  const item = document.createElement(tag);
  item.className = className;
  if (label) item.textContent = label;
  return item;
}

function renderHosts() {
  const query = element('search').value.trim().toLowerCase();
  const groups = Map.groupBy(state.hosts.filter((host) =>
    [host.name, host.hostname, host.user, host.group].some((value) => value?.toLowerCase().includes(query))
  ), (host) => host.group || '未分组');
  const activeHost = state.sessions.find((session) => session.id === activeId)?.hostId;
  const rows = [];
  for (const [group, hosts] of groups) {
    rows.push(node('h3', 'host-group', group));
    for (const host of hosts) {
      const row = node('div', 'host-row' + (activeHost === host.id ? ' selected' : ''));
      const open = node('button', 'host-open');
      const details = node('span', 'host-copy');
      const isDemo = host.kind === 'demo';
      const address = isDemo ? '本机演示终端' : host.user + '@' + host.hostname + ':' + host.port;
      details.append(node('span', 'host-name', host.name), node('span', 'host-meta', address));
      open.append(node('span', 'host-icon', '>_'), details);
      open.onclick = () => connect(host.id);
      row.append(open);
      if (!isDemo) {
        const edit = node('button', 'host-edit', '⋯');
        edit.setAttribute('aria-label', '编辑 ' + host.name);
        edit.onclick = () => openForm(host);
        row.append(edit);
      }
      rows.push(row);
    }
  }
  if (!rows.length) rows.push(node('p', 'no-hosts', '没有匹配的主机'));
  hostList.replaceChildren(...rows);
  element('host-count').textContent = state.hosts.length;
  element('try-demo').disabled = !state.hosts.some((host) => host.kind === 'demo');
}

function renderTabs() {
  const tabs = state.sessions.map((session) => {
    const selected = session.id === activeId;
    const tab = node('div', 'tab' + (selected ? ' active' : ''));
    const title = node('button', '', session.name);
    title.setAttribute('aria-pressed', String(selected));
    title.onclick = () => activate(session.id);
    const close = node('button', '', '×');
    close.setAttribute('aria-label', '关闭 ' + session.name);
    close.onclick = async () => {
      try {
        await request('sessions/' + encodeURIComponent(session.id), 'DELETE');
        await refresh();
      } catch (error) { showNotice(error.message); }
    };
    tab.append(title, close);
    return tab;
  });
  element('tabs').replaceChildren(...tabs);
  element('session-total').textContent = state.sessions.length + ' 个会话';
  const current = state.sessions.find((session) => session.id === activeId);
  const host = state.hosts.find((candidate) => candidate.id === current?.hostId);
  element('session-type').textContent = host?.kind === 'demo' ? '本机演示 · 真实交互式终端' : 'SSH · OpenSSH';
}

async function refresh() {
  const next = await request('state');
  if (JSON.stringify(next) === JSON.stringify(state)) return;
  state = next;
  element('version').textContent = next.version || '0001';
  if (activeId && !state.sessions.some((session) => session.id === activeId)) {
    activate(state.sessions[0]?.id);
    return;
  }
  renderHosts();
  renderTabs();
}

function renderConnection(label, live = false) {
  const connection = element('connection');
  connection.textContent = label;
  connection.classList.toggle('live', live);
}

function fitTerminal() {
  if (!terminal || !fitter) return;
  fitter.fit();
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({type: 'resize', cols: terminal.cols, rows: terminal.rows}));
  }
}

function activate(id) {
  if (activeId === id && socket?.readyState === WebSocket.OPEN) {
    terminal.focus();
    return;
  }
  socket?.close();
  input?.dispose();
  terminal?.dispose();
  socket = terminal = fitter = input = undefined;
  terminalNode.replaceChildren();
  activeId = id;
  element('empty').hidden = Boolean(id);
  renderHosts();
  renderTabs();
  if (!id) {
    renderConnection('未连接');
    return;
  }
  terminal = new Terminal({
    cursorBlink: true, fontFamily: '"IBM Plex Mono", Menlo, monospace', fontSize: 14, lineHeight: 1.22,
    theme: {background: '#0a1015', foreground: '#dce8e6', cursor: '#87efba', selectionBackground: '#355d4d'}
  });
  terminal.open(terminalNode);
  if (window.FitAddon?.FitAddon) {
    fitter = new window.FitAddon.FitAddon();
    terminal.loadAddon(fitter);
    fitter.fit();
  }
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const connection = new WebSocket(protocol + '//' + location.host + '/terminal?id=' + encodeURIComponent(id));
  socket = connection;
  renderConnection('连接中');
  connection.onopen = () => {
    if (socket !== connection) return;
    renderConnection('终端已接入', true);
    fitTerminal();
  };
  connection.onmessage = ({data}) => { if (socket === connection) terminal.write(data); };
  connection.onclose = () => { if (socket === connection) renderConnection('终端已断开'); };
  input = terminal.onData((data) => { if (connection.readyState === WebSocket.OPEN) connection.send(data); });
  terminal.focus();
}

async function connect(hostId) {
  if (starting || !hostId) return;
  const existing = state.sessions.find((session) => session.hostId === hostId);
  if (existing) {
    activate(existing.id);
    return;
  }
  starting = true;
  try {
    const session = await request('sessions', 'POST', {hostId});
    await refresh();
    activate(session.id);
    showNotice();
  } catch (error) { showNotice(error.message); }
  finally { starting = false; }
}

function openForm(host) {
  form.reset();
  editing = host?.id;
  element('form-title').textContent = editing ? '编辑主机' : '添加主机';
  element('form-error').textContent = '';
  element('delete-host').hidden = !editing;
  element('remove-key-row').hidden = !host?.hasKey;
  element('key-help').textContent = host?.hasKey
    ? '已导入私钥。选择另一个文件可替换，重新连接时生效。'
    : '支持 OpenSSH 和 PEM。连接时可在终端输入私钥口令或登录密码。';
  if (host) {
    for (const field of ['name', 'hostname', 'port', 'user', 'group']) {
      form.elements.namedItem(field).value = host[field] ?? '';
    }
  }
  dialog.showModal();
}

form.onsubmit = async (event) => {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(form));
  body.port = Number(body.port);
  const key = element('key-file').files[0];
  try {
    if (key) {
      if (key.size > 12000) throw new Error('私钥文件须小于 12 KB。');
      body.privateKey = await key.text();
    } else if (element('remove-key').checked) {
      body.removeKey = true;
    }
    await request(editing ? 'hosts/' + encodeURIComponent(editing) : 'hosts', editing ? 'PUT' : 'POST', body);
    dialog.close();
    await refresh();
  } catch (error) { element('form-error').textContent = error.message; }
};
element('delete-host').onclick = async () => {
  if (!confirm('删除这台主机并关闭相关会话？')) return;
  try {
    await request('hosts/' + encodeURIComponent(editing), 'DELETE');
    dialog.close();
    await refresh();
  } catch (error) { element('form-error').textContent = error.message; }
};
element('cancel').onclick = () => dialog.close();
element('add').onclick = () => openForm();
element('search').oninput = renderHosts;
element('try-demo').onclick = () => connect(state.hosts.find((host) => host.kind === 'demo')?.id);
new ResizeObserver(fitTerminal).observe(terminalNode);
refresh().catch((error) => showNotice(error.message));
setInterval(() => refresh().catch((error) => showNotice(error.message)), 1500);
