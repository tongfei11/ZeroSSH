(() => {
  'use strict';
  if ((navigator.language || 'en').toLowerCase().startsWith('zh')) return;
  document.documentElement.lang = 'en';
  const dictionary = {
    'Zero SSH · 主机与会话': 'Zero SSH · Hosts & Sessions',
    '设备访问码 / Device access code': 'Device access code', '登录 / Sign in': 'Sign in',
    '主机与会话': 'Hosts & Sessions', '＋ 添加主机': '＋ Add host', '主机': 'Hosts',
    '搜索主机或地址': 'Search hosts or addresses', '终端会话': 'Terminal sessions', '未连接': 'Disconnected',
    '会话': 'Sessions', 'SSH 登录密码': 'SSH password', 'SSH 私钥口令': 'SSH key passphrase', '登录': 'Sign in',
    '打开一个会话': 'Open a session', '从左侧选择主机。切换会话时，后台继续运行。': 'Select a host on the left. Sessions keep running in the background when you switch.',
    '打开演示终端': 'Open demo terminal', '添加主机': 'Add host', '编辑主机': 'Edit host',
    '名称': 'Name', '留空使用主机地址': 'Leave blank to use host address', '主机地址': 'Host address', '端口': 'Port',
    '用户名': 'Username', '例如 admin': 'e.g. admin', 'SSH 登录方式': 'SSH authentication', '密码': 'Password', '私钥': 'Private key',
    '填写后保存到 Zero SSH，下次连接自动使用。': 'Save in Zero SSH to use automatically on the next connection.',
    '清除已保存的密码': 'Clear saved password', '导入私钥文件': 'Import private key file',
    '支持 OpenSSH 和 PEM 私钥文件。': 'Supports OpenSSH and PEM private key files.', '移除已导入的私钥': 'Remove imported private key',
    '删除主机': 'Delete host', '取消': 'Cancel', '保存主机': 'Save host', '保存并连接': 'Save & connect',
    '本机演示终端': 'Local demo terminal', '没有匹配的主机': 'No matching hosts', '本机演示 · 真实交互式终端': 'Local demo · Interactive terminal',
    '会话已结束': 'Session ended', '连接中': 'Connecting', '终端已接入': 'Connected', '终端已断开': 'Disconnected', '操作失败': 'Operation failed',
    '已导入私钥。选择另一个文件可替换，重新连接时生效。': 'Private key imported. Select another file to replace it; reconnect to apply.',
    '已保存密码。填写新密码可替换，重新连接时生效。': 'Password saved. Enter a new password to replace it; reconnect to apply.',
    '私钥文件须小于 12 KB。': 'Private key file must be smaller than 12 KB.',
    '删除这台主机并关闭相关会话？': 'Delete this host and close its sessions?'
  };
  function translate(value) {
    const trimmed = value.trim();
    let translated = dictionary[trimmed];
    if (!translated && /^\d+ 个会话$/.test(trimmed)) translated = trimmed.replace(/ 个会话$/, ' sessions');
    if (!translated && /^(编辑|关闭) /.test(trimmed)) translated = trimmed.replace(/^编辑 /, 'Edit ').replace(/^关闭 /, 'Close ');
    return translated ? value.replace(trimmed, translated) : value;
  }
  function scan(root) {
    if (root.nodeType === Node.TEXT_NODE) {
      if (!root.parentElement?.closest('#terminal, .xterm, #host-list, #tabs, script, style, textarea, input')) {
        const next = translate(root.data);
        if (next !== root.data) root.data = next;
      }
      return;
    }
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
    if (root.nodeType === Node.ELEMENT_NODE) {
      if (root.matches('#terminal, .xterm, script, style, textarea, input')) {
        if (!root.matches('input')) return;
      }
      for (const attr of ['placeholder', 'aria-label', 'title']) {
        if (root.hasAttribute(attr)) {
          const current = root.getAttribute(attr), next = translate(current);
          if (next !== current) root.setAttribute(attr, next);
        }
      }
      // Host and session names are user content; translate only their action labels.
      if (root.matches('#host-list .host-name, #tabs .tab-name')) return;
    }
    for (const child of root.childNodes) scan(child);
  }
  const originalConfirm = window.confirm.bind(window);
  window.confirm = message => originalConfirm(translate(String(message)));
  scan(document);
  new MutationObserver(records => {
    for (const record of records) {
      if (record.type === 'childList') for (const added of record.addedNodes) scan(added);
      else scan(record.target);
    }
  }).observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['placeholder', 'aria-label', 'title'] });
})();
