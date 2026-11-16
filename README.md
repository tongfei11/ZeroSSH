# Zero SSH

![Zero SSH](assets/zero-ssh.png)

版本 v0.1.0，构建 0015。CardputerZero 和 Web 管理端都可以添加 SSH 主机，并切换独立的交互式会话。私钥从 Web 管理端导入。

## 运行

使用 Node.js 20 或更新版本和系统 OpenSSH。

```sh
npm ci
npm start
```

访问 <http://127.0.0.1:4173/>。内置演示主机使用运行服务的机器上的本地 Shell。添加主机时选择「密码」或「私钥」。填写的密码随主机保存在服务端，再次连接时自动输入；留空即可手动登录。可在编辑主机时清除已保存的密码。选择私钥时支持导入 OpenSSH 或 PEM 文件，加密私钥的口令在连接时输入。主机配置和私钥默认保存在服务端的 `.data/`，含凭据的文件权限为 `0600`。

CardputerZero 版允许同一局域网直接访问。打开设备上的 Zero SSH，按屏幕显示的 Web 地址访问，并输入屏幕上的访问码。访问码在服务重启后更新。

打开新标签或切换目标主机时，已有会话继续运行。退出设备应用会断开全部 SSH 会话。关闭单个会话或停止服务也会结束相应的连接。更新主机地址或私钥后，关闭旧会话并重新连接即可应用更新。

## 安装到 CardputerZero

将源码目录复制到 Zero，用运行 APPLaunch 的用户执行 `bash native/install.sh`。安装完成后在 APPLaunch 里打开 **Zero SSH**。在主机列表选择「添加主机」，第一页填写名称、主机地址和端口，第二页填写用户名和密码。短按 Esc 可返回上一页，输入会保留。输入 IP 地址时用 `Sym+B` 输入 `.`。

`F/X` 选主机，回车打开或切回会话，短按 `Esc` 返回或取消，长按 3 秒退出，D 断开选中的会话。设备上的应用打开时，可同时在同一局域网的电脑上访问 Web 管理和导入私钥。主机、凭据和会话由 Zero 上的同一个服务管理。数据目录为 `~/.local/state/zero-ssh/`。设备应用打开时启动 Web 服务，退出时关闭服务和监听端口。

设备界面按系统 LC_ALL、LC_MESSAGES 或 LANG 切换中英文。Web 管理跟随客户端保存的语言设置，切换后自动更新。

首页「设置」可选择跟随系统、中文或 English，并选择终端主题。偏好保存在运行应用用户的 `~/.config/zero-ssh/settings.json`，重开应用后继续使用。终端主题调整默认文字、背景及 ANSI 调色板。

首页顶部为「Add host」，已有主机时默认选中第一个主机。Tab 可切换主机，S 打开设置。选中主机按 Enter 直接连接或切回，E 编辑，D 断开。删除入口位于编辑页第二页，删除前会确认。F/X 直接上下移动，Tab 顺序切换。仅在主机列表页按 Esc 时，在屏幕中央显示退出提示，长按三秒退出应用。其他页面短按 Esc 返回。

## Debian 安装包

在 ARM64 / Node.js 20 环境执行 sh packaging/build-deb.sh --node-modules /path/to/node_modules。输出到 dist/，包内包含 Node 运行依赖与图标，安装无需 npm 编译。使用 sudo apt install ./zero-ssh_0.1.0+build0015_arm64.deb 安装。

设置中的「退出保留服务」默认关闭。开启后退出设备界面仍保留 SSH 会话与 Web 服务。
