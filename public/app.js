const element = (id) => document.getElementById(id);
const hostList = element('host-list');
const terminalNode = element('terminal');
const dialog = element('host-dialog');
const form = element('host-form');
const authForm = element('ssh-auth');
const secretInput = element('ssh-secret');
let state = {hosts: [], sessions: []};
let activeId;
let socket;
let terminal;
let fitter;
let input;
let editing;
let starting = false;
let terminalTail = '';

function clearAuthPrompt() {
  terminalTail = '';
  authForm.hidden = true;
  secretInput.value = '';
}

function detectAuthPrompt(data) {
  terminalTail = (terminalTail + data).split(/[\r\n]/).at(-1).slice(-500);
  const isPassphrase = /^Enter passphrase for key .+:\s*$/i.test(terminalTail);
  const isPassword = /^(?:.+['’]s )?password:\s*$/i.test(terminalTail);
  if (!isPassphrase && !isPassword) {
    if (!authForm.hidden && /[\r\n]/.test(data)) {
      clearAuthPrompt();
      terminal.focus();
    }
    return;
  }
  element('ssh-secret-label').textContent = isPassphrase ? 'SSH 私钥口令' : 'SSH 登录密码';
  if (authForm.hidden) {
    authForm.hidden = false;
    secretInput.focus();
  }
}

function sendSecret(value) {
  if (socket?.readyState !== WebSocket.OPEN) return;
  socket.send(value + '\r');
  clearAuthPrompt();
  terminal.focus();
}

authForm.onsubmit = (event) => {
  event.preventDefault();
  sendSecret(secretInput.value);
};

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
  if (response.status === 401) {
    if (!element('access-dialog').open) element('access-dialog').showModal();
  }
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
  const hosts = state.hosts.filter((host) =>
    [host.name, host.hostname, host.user].some((value) => value?.toLowerCase().includes(query))
  );
  const activeHost = state.sessions.find((session) => session.id === activeId)?.hostId;
  const rows = [];
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
  element('version').textContent = next.version || '0009';
  if (activeId && !state.sessions.some((session) => session.id === activeId)) {
    activate(state.sessions[0]?.id);
    return;
  }
  renderHosts();
  renderTabs();
  if (next.sessions.find((session) => session.id === activeId)?.exited) {
    clearAuthPrompt();
    renderConnection('会话已结束');
  }
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
  clearAuthPrompt();
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
    const exited = state.sessions.find((item) => item.id === id)?.exited;
    renderConnection(exited ? '会话已结束' : '终端已接入', !exited);
    fitTerminal();
  };
  const session = state.sessions.find((item) => item.id === id);
  const usesSsh = state.hosts.find((host) => host.id === session?.hostId)?.kind === 'ssh';
  connection.onmessage = ({data}) => {
    if (socket !== connection) return;
    terminal.write(data);
    if (usesSsh) detectAuthPrompt(data);
  };
  connection.onclose = () => {
    if (socket !== connection) return;
    clearAuthPrompt();
    renderConnection('终端已断开');
  };
  input = terminal.onData((data) => {
    if (connection.readyState !== WebSocket.OPEN) return;
    if (/[\r\n\x03]/.test(data)) {
      clearAuthPrompt();
    }
    connection.send(data);
  });
  terminal.focus();
}

async function connect(hostId) {
  if (starting || !hostId) return;
  const existing = state.sessions.find((session) => session.hostId === hostId);
  if (existing && !existing.exited) {
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
  form.elements.namedItem('authMode').value = host?.authMode || 'password';
  element('key-help').textContent = host?.hasKey
    ? '已导入私钥。选择另一个文件可替换，重新连接时生效。'
    : '支持 OpenSSH 和 PEM 私钥文件。';
  element('host-password-help').textContent = host?.hasPassword
    ? '已保存密码。填写新密码可替换，重新连接时生效。'
    : '填写后保存到 Zero SSH，下次连接自动使用。';
  if (host) {
    for (const field of ['name', 'hostname', 'port', 'user']) {
      form.elements.namedItem(field).value = host[field] ?? '';
    }
  }
  renderAuthFields();
  dialog.showModal();
}

function renderAuthFields() {
  const passwordMode = form.elements.namedItem('authMode').value === 'password';
  const host = state.hosts.find((entry) => entry.id === editing);
  element('password-fields').hidden = !passwordMode;
  element('key-fields').hidden = passwordMode;
  element('remove-key-row').hidden = !passwordMode || !host?.hasKey;
  element('remove-password-row').hidden = !passwordMode || !host?.hasPassword || Boolean(element('host-password').value);
  element('save-host').textContent = !editing && passwordMode && element('host-password').value
    ? '保存并连接' : '保存主机';
}

form.addEventListener('change', (event) => {
  if (event.target.name === 'authMode') renderAuthFields();
});
element('host-password').oninput = renderAuthFields;
dialog.addEventListener('close', () => { element('host-password').value = ''; });

form.onsubmit = async (event) => {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(form));
  body.port = Number(body.port);
  const password = body.authMode === 'password' ? element('host-password').value : '';
  const key = body.authMode === 'key' ? element('key-file').files[0] : undefined;
  const connectAfterSave = !editing && Boolean(password);
  try {
    if (password) body.password = password;
    else if (body.authMode === 'password' && element('remove-password').checked) body.removePassword = true;
    if (key) {
      if (key.size > 12000) throw new Error('私钥文件须小于 12 KB。');
      body.privateKey = await key.text();
    } else if (body.authMode === 'password' && element('remove-key').checked) {
      body.removeKey = true;
    }
    const host = await request(editing ? 'hosts/' + encodeURIComponent(editing) : 'hosts', editing ? 'PUT' : 'POST', body);
    dialog.close();
    await refresh();
    if (connectAfterSave) await connect(host.id);
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
element('access-form').onsubmit = async (event) => {
  event.preventDefault();
  try {
    await request('login', 'POST', { code: element('access-code').value });
    element('access-code').value = '';
    element('access-dialog').close();
    await refresh();
  } catch (error) {
    element('access-error').textContent = error.message;
  }
};
element('add').onclick = () => openForm();
element('search').oninput = renderHosts;
element('try-demo').onclick = () => connect(state.hosts.find((host) => host.kind === 'demo')?.id);
new ResizeObserver(fitTerminal).observe(terminalNode);
refresh().catch((error) => showNotice(error.message));
setInterval(() => refresh().catch((error) => showNotice(error.message)), 1500);
