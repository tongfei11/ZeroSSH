import { randomUUID } from 'node:crypto';
import { constants, accessSync, chmodSync, mkdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { isIP } from 'node:net';
import { extname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import pty from 'node-pty';
import { WebSocket, WebSocketServer } from 'ws';

const version = '0002';
const port = Number(process.env.PORT ?? 4173);
const root = fileURLToPath(new URL('.', import.meta.url));
const publicDir = resolve(root, 'public');
const dataDir = resolve(process.env.CARDPUTER_DATA_DIR ?? resolve(root, '.data'));
const hostsFile = resolve(dataDir, 'hosts.json');
const keysDir = resolve(dataDir, 'keys');
const replayLimit = 512 * 1024;

if (!Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error('PORT must be an integer from 1 to 65535.');
}

// Some npm configurations skip node-pty's postinstall, leaving its macOS helper non-executable.
if (process.platform === 'darwin') {
  for (const path of ['build/Release', `prebuilds/darwin-${process.arch}`]) {
    const helper = resolve(root, 'node_modules/node-pty', path, 'spawn-helper');
    try {
      accessSync(helper, constants.X_OK);
    } catch (error) {
      if (error.code !== 'ENOENT') chmodSync(helper, statSync(helper).mode | 0o100);
    }
  }
}

const fixtures = [
  { id: 'demo-edge', name: 'edge-gateway', hostname: '192.168.1.10', port: 22, user: 'admin', group: '本地演示', kind: 'demo' },
  { id: 'demo-build', name: 'build-node', hostname: '192.168.1.24', port: 22, user: 'deploy', group: '本地演示', kind: 'demo' },
  { id: 'demo-home', name: 'home-lab', hostname: '192.168.1.35', port: 22, user: 'root', group: '本地演示', kind: 'demo' },
];
let hosts;
try {
  hosts = JSON.parse(readFileSync(hostsFile, 'utf8'));
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
  hosts = process.env.ZERO_SSH_MODE === 'device' ? [] : fixtures;
}
const sessions = new Map();

function saveHosts(next) {
  mkdirSync(dataDir, { recursive: true, mode: 0o700 });
  const temporary = resolve(dataDir, 'hosts.json.tmp');
  writeFileSync(temporary, JSON.stringify(next, null, 2) + '\n', { mode: 0o600 });
  renameSync(temporary, hostsFile);
  hosts = next;
}

function publicHost({ keyId, ...host }) {
  return { ...host, authMode: host.authMode ?? (keyId ? 'key' : 'password'), hasKey: Boolean(keyId) };
}

function keyPath(id) {
  return resolve(keysDir, id);
}

function saveHostUpdate(current, fields, body) {
  const authMode = body.authMode ?? (body.removeKey ? 'password'
    : body.privateKey !== undefined ? 'key'
    : current?.authMode ?? (current?.keyId ? 'key' : 'password'));
  if (!['password', 'key'].includes(authMode)) throw new RequestError(400, '请选择 SSH 登录方式。');
  if (authMode === 'key' && body.privateKey === undefined && (!current?.keyId || body.removeKey)) {
    throw new RequestError(400, '请先导入 SSH 私钥。');
  }
  let keyId = body.removeKey ? undefined : current?.keyId;
  if (body.privateKey !== undefined) {
    const key = typeof body.privateKey === 'string' && body.privateKey.replace(/\r\n/g, '\n').trim();
    const format = key && /^-----BEGIN ((?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY)-----\n[\s\S]+\n-----END \1-----$/.test(key);
    if (!format || key.length > 12000) throw new RequestError(400, '请选择 OpenSSH 或 PEM 私钥文件。');
    keyId = randomUUID();
    mkdirSync(keysDir, { recursive: true, mode: 0o700 });
    writeFileSync(keyPath(keyId), key + '\n', { mode: 0o600, flag: 'wx' });
  }
  const host = { id: current?.id ?? randomUUID(), ...fields, kind: 'ssh', authMode, keyId };
  try {
    saveHosts(current ? hosts.map((entry) => entry.id === host.id ? host : entry) : [...hosts, host]);
  } catch (error) {
    if (keyId && keyId !== current?.keyId) rmSync(keyPath(keyId), { force: true });
    throw error;
  }
  if (current?.keyId && current.keyId !== keyId) rmSync(keyPath(current.keyId), { force: true });
  return publicHost(host);
}

const vendorFiles = new Map([
  ['/vendor/xterm.js', 'node_modules/@xterm/xterm/lib/xterm.js'],
  ['/vendor/xterm.css', 'node_modules/@xterm/xterm/css/xterm.css'],
  ['/vendor/xterm-fit.js', 'node_modules/@xterm/addon-fit/lib/addon-fit.js'],
]);
const mimeTypes = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
};

class RequestError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function sendJson(response, status, body) {
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  response.end(JSON.stringify(body));
}

async function readJson(request) {
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers['content-type'] ?? '')) {
    throw new RequestError(415, '请使用 JSON 请求格式。');
  }
  let text = '';
  request.setEncoding('utf8');
  for await (const chunk of request.iterator({ destroyOnReturn: false })) {
    text += chunk;
    if (text.length > 16 * 1024) {
      request.resume();
      throw new RequestError(413, '请求内容过长。');
    }
  }
  try {
    const body = JSON.parse(text);
    if (body && typeof body === 'object' && !Array.isArray(body)) return body;
  } catch {
    // Report malformed JSON the same way as a non-object body.
  }
  throw new RequestError(400, '请提供有效的 JSON 对象。');
}

function hostFields(body) {
  const name = typeof body.name === 'string' ? body.name.trim() : '';
  const group = typeof body.group === 'string' ? body.group.trim() : '';
  const hostname = typeof body.hostname === 'string' ? body.hostname.trim().replace(/^\[(.*)\]$/, '$1') : '';
  const user = typeof body.user === 'string' ? body.user.trim() : '';
  const portInput = ['string', 'number'].includes(typeof body.port) ? String(body.port) : '';
  const sshPort = /^\d{1,5}$/.test(portInput) ? Number(portInput) : NaN;
  const dnsName = /^(?:[a-z\d](?:[a-z\d-]{0,61}[a-z\d])?\.)*[a-z\d](?:[a-z\d-]{0,61}[a-z\d])?\.?$/i;
  if (!name || name.length > 80 || group.length > 80 || /[\u0000-\u001f\u007f]/.test(name + group)) {
    throw new RequestError(400, '请输入有效的主机名称和分组。');
  }
  if (hostname.length > 253 || (!isIP(hostname) && !dnsName.test(hostname))) {
    throw new RequestError(400, '请输入有效的域名或 IP 地址。');
  }
  if (!/^[a-z\d_][\w.-]{0,63}$/i.test(user)) {
    throw new RequestError(400, '请输入有效的 SSH 用户名。');
  }
  if (sshPort < 1 || sshPort > 65535 || Number.isNaN(sshPort)) {
    throw new RequestError(400, '端口须为 1–65535。');
  }
  return { name, hostname, port: sshPort, user, group };
}

function writeTerminal(session, data) {
  const buffer = session.buffer + data;
  const start = Math.max(0, buffer.length - replayLimit);
  session.buffer = buffer.slice(start + (/[\uDC00-\uDFFF]/.test(buffer.charAt(start)) ? 1 : 0));
  for (const socket of session.clients) {
    if (socket.bufferedAmount > replayLimit * 2) socket.terminate();
    else if (socket.readyState === WebSocket.OPEN) socket.send(data);
  }
}

function startSession(host) {
  if (sessions.size >= 20) throw new RequestError(409, '请先关闭一些终端会话。');
  const id = randomUUID();
  const isDemo = host.kind === 'demo';
  const command = isDemo ? '/bin/bash' : '/usr/bin/ssh';
  const authMode = publicHost(host).authMode;
  const authArgs = authMode === 'key'
    ? ['-i', keyPath(host.keyId), '-o', 'IdentitiesOnly=yes', '-o', 'PreferredAuthentications=publickey']
    : ['-o', 'PubkeyAuthentication=no', '-o', 'PreferredAuthentications=keyboard-interactive,password'];
  const args = isDemo
    ? ['--noprofile', '--norc', '-i']
    : [
      '-tt', '-o', 'ConnectTimeout=10',
      ...authArgs,
      '-p', String(host.port), '-l', host.user, '--', host.hostname,
    ];
  const terminal = pty.spawn(command, args, {
    name: 'xterm-256color',
    cols: 80,
    rows: 24,
    cwd: root,
    env: isDemo ? {
      ...process.env,
      BASH_SILENCE_DEPRECATION_WARNING: '1',
      PS1: String.raw`${host.name}:\w\$ `,
    } : process.env,
  });
  const record = { id, hostId: host.id, name: host.name };
  const session = { record, terminal, clients: new Set(), buffer: '', exited: false };
  sessions.set(id, session);
  if (isDemo) writeTerminal(session, 'CardputerZero · 本地演示 Shell\r\n\r\n');
  terminal.onData((data) => writeTerminal(session, data));
  terminal.onExit(({ exitCode, signal }) => {
    session.exited = true;
    record.exited = true;
    writeTerminal(session, `\r\n[会话已结束 · ${signal ? `信号 ${signal}` : `退出码 ${exitCode}`}]\r\n`);
  });
  return record;
}

function closeSession(id) {
  const session = sessions.get(id);
  if (!session) return false;
  sessions.delete(id);
  for (const socket of session.clients) socket.close(1000, 'Session closed');
  if (!session.exited) session.terminal.kill();
  return true;
}

async function handleApi(request, response, path) {
  const { method } = request;
  if (path === '/api/state' && method === 'GET') {
    return sendJson(response, 200, {
      hosts: hosts.map(publicHost), sessions: Array.from(sessions.values(), (session) => session.record), version,
    });
  }
  if (path === '/api/hosts' && method === 'POST') {
    const body = await readJson(request);
    return sendJson(response, 201, saveHostUpdate(undefined, hostFields(body), body));
  }
  const hostId = /^\/api\/hosts\/([\w-]+)$/.exec(path)?.[1];
  if (hostId && method === 'PUT') {
    const body = await readJson(request);
    const current = hosts.find((host) => host.id === hostId);
    if (!current) throw new RequestError(404, '主机不存在。');
    if (current.kind === 'demo') throw new RequestError(400, '演示主机使用内置配置。');
    return sendJson(response, 200, saveHostUpdate(current, hostFields(body), body));
  }
  if (hostId && method === 'DELETE') {
    const current = hosts.find((host) => host.id === hostId);
    if (!current) throw new RequestError(404, '主机不存在。');
    if (current.kind === 'demo') throw new RequestError(400, '演示主机使用内置配置。');
    saveHosts(hosts.filter((host) => host.id !== hostId));
    if (current.keyId) rmSync(keyPath(current.keyId), { force: true });
    for (const [id, session] of sessions) {
      if (session.record.hostId === hostId) closeSession(id);
    }
    return sendJson(response, 200, { ok: true });
  }
  if (path === '/api/sessions' && method === 'POST') {
    const { hostId } = await readJson(request);
    const host = hosts.find((entry) => entry.id === hostId);
    if (!host) throw new RequestError(404, '主机不存在。');
    const existing = Array.from(sessions.values()).find((session) => session.record.hostId === hostId);
    if (existing && !existing.exited) return sendJson(response, 200, existing.record);
    if (existing) closeSession(existing.record.id);
    return sendJson(response, 201, startSession(host));
  }
  const sessionId = /^\/api\/sessions\/([\w-]+)$/.exec(path)?.[1];
  if (sessionId && method === 'DELETE') {
    if (!closeSession(sessionId)) throw new RequestError(404, '会话不存在。');
    return sendJson(response, 200, { ok: true });
  }
  throw new RequestError(404, '未找到 API。');
}

function allowedHost(host) {
  return host === `127.0.0.1:${port}` || host === `localhost:${port}`
    || (port === 80 && (host === '127.0.0.1' || host === 'localhost'));
}

const server = createServer(async (request, response) => {
  response.setHeader('X-Content-Type-Options', 'nosniff');
  response.setHeader('X-Frame-Options', 'DENY');
  const host = request.headers.host;
  try {
    if (!allowedHost(host) || (request.headers.origin && request.headers.origin !== `http://${host}`)) {
      throw new RequestError(403, '仅允许同源本地访问。');
    }
    const { pathname } = new URL(request.url, `http://${host}`);
    if (pathname.startsWith('/api/')) return await handleApi(request, response, pathname);
    if (request.method !== 'GET' && request.method !== 'HEAD') throw new RequestError(405, '不支持此请求方法。');
    const path = pathname === '/' ? '/index.html' : decodeURIComponent(pathname);
    const staticFile = vendorFiles.has(path) ? resolve(root, vendorFiles.get(path)) : resolve(publicDir, '.' + path);
    if (!vendorFiles.has(path) && !staticFile.startsWith(publicDir + '/')) throw new RequestError(404, '文件不存在。');
    const content = await readFile(staticFile);
    response.writeHead(200, { 'Content-Type': mimeTypes[extname(path)] ?? 'application/octet-stream' });
    response.end(request.method === 'HEAD' ? undefined : content);
  } catch (error) {
    if (error instanceof RequestError) return sendJson(response, error.status, { error: error.message });
    if (error instanceof URIError || ['ENOENT', 'EISDIR', 'ENOTDIR'].includes(error.code)) {
      return sendJson(response, 404, { error: '文件不存在。' });
    }
    console.error(error);
    sendJson(response, 500, { error: '服务端操作失败。' });
  }
});

const sockets = new WebSocketServer({ noServer: true, maxPayload: 64 * 1024 });
server.on('upgrade', (request, socket, head) => {
  const host = request.headers.host;
  if (!allowedHost(host) || request.headers.origin !== `http://${host}`) {
    socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
    return;
  }
  let url;
  try {
    url = new URL(request.url, `http://${host}`);
  } catch {
    socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n');
    return;
  }
  const session = url.pathname === '/terminal' && sessions.get(url.searchParams.get('id'));
  if (!session) {
    socket.end('HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n');
    return;
  }
  sockets.handleUpgrade(request, socket, head, (client) => {
    session.clients.add(client);
    if (session.buffer) client.send(session.buffer);
    client.on('error', () => client.terminate());
    client.on('close', () => session.clients.delete(client));
    client.on('message', (message) => {
      if (session.exited) return;
      const text = message.toString();
      let control;
      if (text.startsWith('{') && !/[\r\n]/.test(text)) {
        try { control = JSON.parse(text); } catch { /* forward ordinary input */ }
      }
      if (control?.type === 'resize') {
        const { cols, rows } = control;
        if (Number.isInteger(cols) && cols >= 1 && cols <= 500 &&
            Number.isInteger(rows) && rows >= 1 && rows <= 200) {
          try { session.terminal.resize(cols, rows); } catch (error) {
            if (!['EBADF', 'EIO'].includes(error.code)) console.error(error);
          }
        }
        return;
      }
      session.terminal.write(text);
    });
  });
});

const inheritedSocket = process.env.LISTEN_PID === String(process.pid) && process.env.LISTEN_FDS === '1';
server.listen(inheritedSocket ? { fd: 3 } : { port, host: '127.0.0.1' }, () => {
  console.log(`CardputerZero ${version} · http://127.0.0.1:${port}`);
});
function shutdown() {
  for (const id of sessions.keys()) closeSession(id);
  for (const socket of sockets.clients) socket.terminate();
  server.close();
}
process.once('SIGINT', shutdown);
process.once('SIGTERM', shutdown);
