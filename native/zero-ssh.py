#!/usr/bin/env python3
"""SSH host picker and terminal for the CardputerZero LCD and keyboard."""

import argparse
import json
import os
import queue
import tempfile
import selectors
import signal
import struct
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from PIL import Image, ImageDraw, ImageFont

SIZE = (320, 170)
FRAMEBUFFER = "/dev/fb_lcd"
KEYBOARD = "/dev/input/by-path/platform-3f804000.i2c-event"
INPUT_EVENT = struct.Struct("@llHHi")
EV_KEY = 1
ESC, BACKSPACE, ENTER, KP_ENTER, DELETE = 1, 14, 28, 96, 111
ESC_HOLD_SECONDS = 3
TAB, UP, DOWN = 15, 103, 108
SHIFT = {42, 54}
CTRL = {29, 97}
ALT = {56, 100}
LIST_MOVES = {33: -1, 103: -1, 45: 1, 108: 1}  # F/X, Up/Down
ARROWS = {103: "A", 108: "B", 106: "C", 105: "D"}
TERMINAL_KEYS = {
    BACKSPACE: "\x7f", ENTER: "\r", KP_ENTER: "\r", 15: "\t",
    102: "\x1b[H", 107: "\x1b[F", 104: "\x1b[5~", 109: "\x1b[6~",
    110: "\x1b[2~", DELETE: "\x1b[3~",
}
PRINTABLE = {57: (" ", " ")}
# Raw evdev codes from APPLaunch's TCA8418 keymap. The keyboard emits the
# symbol code directly (for example Sym+B emits 91 for a period).
CARDPUTER_SYMBOLS = {
    26: "!", 27: "@", 39: "#", 40: "$", 41: "%", 43: "^",
    51: "&", 52: "*", 53: "(", 94: ")", 55: "~", 69: "`",
    70: "_", 71: "-", 72: "+", 73: "=", 74: "[", 75: "]",
    76: "{", 77: "}", 79: ";", 80: ":", 81: "'", 82: '"',
    83: "<", 85: ">", 86: "\\", 89: "|", 90: ",", 91: ".",
    92: "/", 93: "?",
}
HOST_FIELDS = (
    ("name", "名称", "留空使用地址", 80),
    ("hostname", "主机地址", "域名或 IP", 253),
    ("port", "端口", "22", 5),
    ("user", "用户名", "SSH 登录用户名", 64),
    ("password", "密码", "留空可在连接时输入", 1024),
)
for base, plain, shifted in (
    (2, "1234567890-=", "!@#$%^&*()_+"),
    (16, "qwertyuiop[]", "QWERTYUIOP{}"),
    (30, "asdfghjkl;'\x60", "ASDFGHJKL:\"~"),
    (43, "\\zxcvbnm,./", "|ZXCVBNM<>?"),
):
    PRINTABLE.update({base + i: pair for i, pair in enumerate(zip(plain, shifted))})

BACKGROUND = "#10151d"
SURFACE = "#1b2531"
INK = "#edf2f8"
MUTED = "#adbccb"
ACCENT = "#6ce1be"
TERMINAL_COLORS = {
    "black": "#17212d", "red": "#fa7773", "green": "#7cda96",
    "brown": "#e4b566", "blue": "#83acff", "magenta": "#db9bf0",
    "cyan": "#7edbdf", "white": "#eaf1f8", "brightblack": "#778698",
    "brightred": "#ff9b95", "brightgreen": "#9be9a8", "brightbrown": "#ffe099",
    "brightblue": "#a6c4ff", "brightmagenta": "#ebb2fa",
    "brightcyan": "#a0e7ed", "brightwhite": "#ffffff",
}
FONT_CJK = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_UI = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def detect_language(environ=None, locale_path="/etc/default/locale"):
    env = os.environ if environ is None else environ
    locale = next((env[k] for k in ("LC_ALL", "LC_MESSAGES", "LANG") if env.get(k)), "")
    if not locale:
        try:
            values = dict(line.split("=", 1) for line in Path(locale_path).read_text().splitlines()
                          if "=" in line and not line.lstrip().startswith("#"))
            locale = next((values[k].strip().strip('"').strip("'") for k in
                           ("LC_ALL", "LC_MESSAGES", "LANG") if values.get(k)), "")
        except OSError:
            pass
    return "zh" if locale.lower().startswith("zh") else "en"


LANGUAGE = detect_language()


THEMES = {"Default": (INK, BACKGROUND), "Dracula": ("#f8f8f2", "#282a36"),
          "Nord": ("#d8dee9", "#2e3440"), "Solarized Dark": ("#839496", "#002b36"),
          "Gruvbox Dark": ("#ebdbb2", "#282828")}
THEME_ANSI = {
    "Dracula": ["#21222c", "#ff5555", "#50fa7b", "#f1fa8c", "#bd93f9", "#ff79c6", "#8be9fd", "#f8f8f2",
                "#6272a4", "#ff6e6e", "#69ff94", "#ffffa5", "#d6acff", "#ff92df", "#a4ffff", "#ffffff"],
    "Nord": ["#3b4252", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0",
             "#4c566a", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#8fbcbb", "#eceff4"],
    "Solarized Dark": ["#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
                       "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3"],
    "Gruvbox Dark": ["#282828", "#cc241d", "#98971a", "#d79921", "#458588", "#b16286", "#689d6a", "#a89984",
                     "#928374", "#fb4934", "#b8bb26", "#fabd2f", "#83a598", "#d3869b", "#8ec07c", "#ebdbb2"],
}
LANG_OVERRIDE = None


class Settings:
    def __init__(self, path=None):
        self.path = Path(path or Path.home() / ".config/zero-ssh/settings.json")
        self.language, self.theme = "system", "Default"
        try:
            data = json.loads(self.path.read_text())
            if data.get("language") in ("system", "zh", "en"):
                self.language = data["language"]
            if data.get("theme") in THEMES:
                self.theme = data["theme"]
        except (OSError, ValueError, AttributeError):
            pass

    def apply(self):
        global LANGUAGE
        LANGUAGE = LANG_OVERRIDE or (detect_language() if self.language == "system" else self.language)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".settings-")
        try:
            with os.fdopen(fd, "w") as output:
                json.dump({"language": self.language, "theme": self.theme}, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def tr(zh, en):
    return zh if LANGUAGE == "zh" else en


def load_font(paths, size):
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def fitted(font, value, width):
    if font.getlength(value) <= width:
        return value
    while value and font.getlength(value + "…") > width:
        value = value[:-1]
    return value + "…"


def rgb565(image):
    pixels = bytearray(SIZE[0] * SIZE[1] * 2)
    for index, (red, green, blue) in enumerate(image.getdata()):
        color = ((red & 248) << 8) | ((green & 252) << 3) | (blue >> 3)
        struct.pack_into("<H", pixels, index * 2, color)
    return pixels


def terminal_key(code, pressed, caps, application_cursor=False):
    if code in CARDPUTER_SYMBOLS:
        value = CARDPUTER_SYMBOLS[code]
    elif code in ARROWS:
        value = ("\x1bO" if application_cursor else "\x1b[") + ARROWS[code]
    elif code == 15 and pressed & SHIFT:
        value = "\x1b[Z"
    elif code in TERMINAL_KEYS:
        value = TERMINAL_KEYS[code]
    elif code in PRINTABLE:
        normal, shifted = PRINTABLE[code]
        value = shifted if bool(pressed & SHIFT) ^ (caps and normal.isalpha()) else normal
        if pressed & CTRL:
            if normal.isalpha() or value in "@[\\]^_":
                value = chr(ord(value.upper()) & 31)
            elif value == " ":
                value = "\x00"
    else:
        return None
    return "\x1b" + value if pressed & ALT else value


class Display:
    def __init__(self):
        self.theme = "Default"
        self.ui = load_font((FONT_CJK, FONT_UI, "DejaVuSans.ttf"), 12)
        self.title = load_font((FONT_CJK, FONT_UI, "DejaVuSans.ttf"), 17)
        self.mono = load_font((FONT_MONO, "DejaVuSansMono.ttf"), 11)
        self.cell_width = round(self.mono.getlength("M"))
        self.cell_height = 13
        self.columns = (SIZE[0] - 8) // self.cell_width
        self.rows = (SIZE[1] - 37) // self.cell_height

    def list_image(self, hosts, active, index, status):
        image = Image.new("RGB", SIZE, BACKGROUND)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 319, 27), fill=SURFACE)
        draw.text((9, 14), "Zero SSH", font=self.title, fill=INK, anchor="lm")
        draw.text((311, 15), tr("设置 (S)", "Settings (S)"), font=self.ui, fill=ACCENT, anchor="rm")
        first = min(max(0, min(index, len(hosts) - 1) - 1), max(0, len(hosts) - 3))
        for row, host in enumerate(hosts[first:first + 3], start=first):
            y = 55 + (row - first) * 25
            selected = row == index
            draw.rounded_rectangle(
                (7, y, 312, y + 23), radius=5, fill=SURFACE,
                outline=ACCENT if selected else SURFACE,
            )
            draw.text((14, y + 12), fitted(self.ui, host["name"], 143), font=self.ui,
                      fill=INK, anchor="lm")
            if host["id"] in active:
                detail = tr("已连接", "Connected")
            elif host.get("kind") == "demo":
                detail = "Local shell"
            else:
                detail = f'{host["user"]}@{host["hostname"]}'
            draw.text((304, y + 12), fitted(self.ui, detail, 132), font=self.ui,
                      fill=ACCENT if host["id"] in active else MUTED, anchor="rm")
        if not hosts:
            draw.text((14, 66), tr("暂无主机", "No hosts"), font=self.ui, fill=MUTED, anchor="lm")
        y = 30
        draw.rounded_rectangle((7, y, 312, y + 23), radius=5, fill=SURFACE,
                               outline=ACCENT if index == len(hosts) else SURFACE)
        draw.text((14, y + 12), tr("+ 添加主机", "+ Add host"), font=self.ui, fill=ACCENT, anchor="lm")
        draw.text((9, 139), fitted(self.ui, status, 300), font=self.ui, fill=MUTED, anchor="lm")
        draw.rectangle((0, 149, 319, 169), fill=SURFACE)
        for x, label in ((9, tr("Tab 切换", "Tab Switch")),
                         (100, tr("E 编辑", "E Edit")),
                         (169, tr("D 断开", "D Disconnect"))):
            draw.text((x, 159), label, font=self.ui, fill=INK, anchor="lm")
        return image

    def host_form_image(self, fields, focus, status, access=None, editing=False):
        image = Image.new("RGB", SIZE, BACKGROUND)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 319, 22), fill=SURFACE)
        title = tr("编辑主机", "Edit host") if editing else tr("添加主机", "Add host")
        draw.text((9, 11), title, font=self.title, fill=INK, anchor="lm")
        draw.text((311, 11), tr("Tab 切换", "Tab to switch"), font=self.ui, fill=MUTED, anchor="rm")
        page = 0 if focus <= 2 else 1
        visible = HOST_FIELDS[:3] if page == 0 else HOST_FIELDS[3:]
        english = {"name": ("Name", "Address if empty"), "hostname": ("Address", "Hostname or IP"),
                   "port": ("Port", "22"), "user": ("User", "SSH username"),
                   "password": ("Password", "Ask on connection")}
        for row, (key, label, placeholder, _) in enumerate(visible):
            if LANGUAGE != "zh":
                label, placeholder = english[key]
            y = 27 + row * 24
            selected = (row if page == 0 else row + 3) == focus
            draw.text((9, y + 8), label, font=self.ui, fill=INK, anchor="lm")
            draw.rounded_rectangle((69, y, 312, y + 17), radius=4, fill=SURFACE,
                                   outline=ACCENT if selected else SURFACE)
            value = fields[key]
            if key == "password":
                value = "•" * len(value)
            if selected and value:
                while self.ui.getlength(value + "_") > 228:
                    value = value[1:]
                value += "_"
            draw.text((76, y + 8), fitted(self.ui, value or placeholder, 228), font=self.ui,
                      fill=INK if value else MUTED, anchor="lm")
        if editing and page == 1:
            draw.rounded_rectangle((69, 77, 312, 96), radius=4, fill=SURFACE,
                                   outline=ACCENT if focus == 6 else SURFACE)
            draw.text((76, 86), tr("删除主机", "Delete host"), font=self.ui,
                      fill="#ff7777", anchor="lm")
        draw.text((9, 106), tr("私钥请用 Web 导入", "Import private keys via Web"), font=self.ui, fill=MUTED, anchor="lm")
        if access and access.get("url"):
            draw.text((9, 123), fitted(self.ui, access["url"], 300), font=self.ui, fill=ACCENT, anchor="lm")
            draw.text((9, 140), tr("访问码: ", "Access code: ") + access.get("code", ""), font=self.ui, fill=INK, anchor="lm")
        draw.rounded_rectangle((7, 151, 312, 168), radius=4, fill=SURFACE,
                               outline=ACCENT)
        action = tr("Enter 下一页 · 1/2", "Enter Next · 1/2") if page == 0 else tr("Enter 保存 · 2/2", "Enter Save · 2/2")
        draw.text((160, 159), fitted(self.ui, status or action, 294), font=self.ui,
                  fill=INK if status else ACCENT, anchor="mm")
        return image

    def settings_image(self, settings, focus, status):
        image = Image.new("RGB", SIZE, BACKGROUND)
        draw = ImageDraw.Draw(image)
        draw.text((9, 12), tr("设置", "Settings"), font=self.title, fill=INK)
        labels = [(tr("语言", "Language"), {"system": "System", "zh": "中文", "en": "English"}[settings.language]),
                  (tr("终端主题", "Terminal theme"), settings.theme)]
        for row, (label, value) in enumerate(labels):
            y = 40 + row * 28
            draw.rounded_rectangle((7, y, 312, y + 24), radius=4, fill=SURFACE,
                                   outline=ACCENT if row == focus else SURFACE)
            draw.text((14, y + 12), label, font=self.ui, fill=INK, anchor="lm")
            draw.text((302, y + 12), value, font=self.ui, fill=ACCENT, anchor="rm")
        fg, bg = THEMES[self.theme]
        draw.rectangle((7, 101, 312, 132), fill=bg)
        draw.text((13, 104), "user@host $ ls", font=self.mono, fill=fg)
        draw.text((13, 118), "folder/", font=self.mono, fill=self.color("blue", fg))
        draw.text((80, 118), "ready", font=self.mono, fill=self.color("green", fg))
        draw.text((9, 139), fitted(self.ui, status, 300), font=self.ui, fill=MUTED)
        draw.text((9, 156), tr("Tab 切换   Enter 调整", "Tab Select   Enter Change"), font=self.ui, fill=ACCENT)
        return image

    def terminal_image(self, name, screen, status):
        foreground, background = THEMES[self.theme]
        image = Image.new("RGB", SIZE, background)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 319, 17), fill=SURFACE)
        draw.text((5, 9), fitted(self.ui, name, 190), font=self.ui, fill=ACCENT, anchor="lm")
        for line_number in range(screen.lines):
            line = screen.buffer[line_number]
            for column in range(screen.columns):
                cell = line[column]
                x = 4 + column * self.cell_width
                y = 19 + line_number * self.cell_height
                fg = self.color(cell.fg, foreground)
                bg = self.color(cell.bg, background)
                if cell.reverse:
                    fg, bg = bg, fg
                if bg != background:
                    draw.rectangle((x, y, x + self.cell_width - 1, y + self.cell_height - 1), fill=bg)
                if not cell.data or cell.data == " ":
                    continue
                draw.text((x, y + 10), cell.data, font=self.mono if cell.data.isascii() else self.ui,
                          fill=fg, anchor="ls")
                if cell.underscore:
                    draw.line((x, y + 11, x + self.cell_width - 1, y + 11), fill=fg)
        cursor = screen.cursor
        if not cursor.hidden and cursor.x < screen.columns:
            x = 4 + cursor.x * self.cell_width
            y = 29 + cursor.y * self.cell_height
            draw.line((x, y, x + self.cell_width - 1, y), fill=ACCENT)
        if status:
            draw.text((5, 160), fitted(self.ui, status, 310), font=self.ui, fill=MUTED, anchor="lm")
        return image

    def color(self, value, default):
        if len(value) == 6 and all(c in "0123456789abcdefABCDEF" for c in value):
            return "#" + value
        if self.theme in THEME_ANSI and value in TERMINAL_COLORS:
            return THEME_ANSI[self.theme][list(TERMINAL_COLORS).index(value)]
        return TERMINAL_COLORS.get(value, default)


class Terminal:
    def __init__(self, origin, session, columns, rows):
        import pyte
        import websocket

        self.name = session["name"]
        self.screen = pyte.Screen(columns, rows)
        self.stream = pyte.Stream(self.screen)
        self.events = queue.Queue()
        self.closed = threading.Event()
        address = "ws://" + origin.removeprefix("http://")
        path = "/terminal?" + urlencode({"id": session["id"]})
        self.socket = websocket.create_connection(address + path, origin=origin, timeout=2)
        try:
            self.socket.send(json.dumps({"type": "resize", "cols": columns, "rows": rows}))
            self.socket.settimeout(None)
        except (OSError, websocket.WebSocketException):
            self.socket.abort()
            raise
        self.receiver = threading.Thread(target=self.receive, daemon=True)
        self.receiver.start()

    def receive(self):
        import websocket

        try:
            while not self.closed.is_set():
                opcode, data = self.socket.recv_data()
                if opcode == websocket.ABNF.OPCODE_CLOSE:
                    break
                self.events.put(data.decode("utf-8", "replace"))
        except (OSError, websocket.WebSocketException):
            pass
        finally:
            self.events.put(None)

    def close(self):
        self.closed.set()
        self.socket.abort()
        self.receiver.join(timeout=0.2)
        self.socket.shutdown()


class App:
    def __init__(self, origin, display, settings=None):
        self.origin = origin
        self.display = display
        self.settings = settings or Settings()
        self.settings.apply()
        if self.display is not None:
            self.display.theme = self.settings.theme
        self.settings_focus = None
        self.host_action = None
        self.action_focus = 0
        self.delete_confirm = False
        self.editing_host = None
        self.opener = build_opener(ProxyHandler({}))
        self.hosts = []
        self.loaded = False
        self.active = {}
        self.index = 0
        self.status = ""
        self.terminal = None
        self.host_form = None
        self.form_focus = 0
        self.access = None
        self.pressed = set()
        self.caps = False
        self.exit_at = None
        self.escape_pressed = False
        self.toast_until = 0

    def request(self, path, method="GET", data=None):
        payload = json.dumps(data).encode() if data is not None else None
        request = Request(self.origin + path, payload, {"Content-Type": "application/json"}, method=method)
        try:
            with self.opener.open(request, timeout=2) as response:
                return json.load(response)
        except HTTPError as error:
            with error:
                raise OSError(json.load(error).get("error", str(error))) from error

    def refresh(self):
        try:
            data = self.request("/api/state")
        except OSError:
            self.status = tr("等待 Zero SSH 服务", "Waiting for Zero SSH service")
            return
        extra_index = self.index - len(self.hosts) if self.loaded and self.index >= len(self.hosts) else None
        selected = self.hosts[self.index]["id"] if self.index < len(self.hosts) else None
        self.hosts = data["hosts"]
        self.index = len(self.hosts) + extra_index if extra_index is not None else next(
            (i for i, host in enumerate(self.hosts) if host["id"] == selected),
            min(self.index, max(0, len(self.hosts) - 1)),
        )
        self.active = {item["hostId"]: item for item in data["sessions"] if not item.get("exited")}
        self.loaded = True
        self.status = ""

    def draw(self):
        image = self.draw_page()
        if self.on_host_list() and time.monotonic() < self.toast_until:
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((20, 64, 299, 106), radius=7, fill=SURFACE, outline=ACCENT)
            draw.text((160, 85), tr("长按 Esc 3 秒退出", "Hold Esc for 3s to exit"),
                      font=self.display.ui, fill=INK, anchor="mm")
        return image

    def on_host_list(self):
        return (self.host_action is None and self.settings_focus is None
                and self.host_form is None and self.terminal is None)

    def draw_page(self):
        if self.host_action is not None:
            image = Image.new("RGB", SIZE, BACKGROUND)
            draw = ImageDraw.Draw(image)
            draw.text((9, 12), fitted(self.display.title, self.host_action["name"], 300),
                      font=self.display.title, fill=INK)
            labels = ([tr("取消删除", "Cancel"), tr("确认删除主机", "Delete host")] if self.delete_confirm else
                      [tr("连接 / 切回", "Connect / Resume"), tr("断开", "Disconnect"),
                       tr("编辑", "Edit")])
            for index, label in enumerate(labels):
                y = 40 + index * 25
                draw.rounded_rectangle((7, y, 312, y + 22), radius=4, fill=SURFACE,
                                       outline=ACCENT if index == self.action_focus else SURFACE)
                draw.text((15, y + 11), label, font=self.display.ui, fill=INK, anchor="lm")
            draw.text((9, 157), self.status or tr("Tab 切换", "Tab to switch"),
                      font=self.display.ui, fill=MUTED, anchor="lm")
            return image
        if self.settings_focus is not None:
            return self.display.settings_image(self.settings, self.settings_focus, self.status)
        if self.terminal:
            return self.display.terminal_image(self.terminal.name, self.terminal.screen, self.status)
        if self.host_form is not None:
            return self.display.host_form_image(self.host_form, self.form_focus, self.status, self.access,
                                                self.editing_host is not None)
        return self.display.list_image(self.hosts, self.active, self.index, self.status)

    def handle_key(self, code, value):
        if code == ESC:
            return self.handle_escape(value)
        if value == 0:
            self.pressed.discard(code)
            return True
        self.pressed.add(code)
        if code == 58 and value == 1:
            self.caps = not self.caps
        if self.terminal:
            keys = terminal_key(code, self.pressed, self.caps, (1 << 5) in self.terminal.screen.mode)
            if keys and not self.terminal.closed.is_set():
                self.send_keys(keys)
        elif self.host_action is not None:
            self.handle_action_key(code, value)
        elif self.settings_focus is not None:
            if code in (*LIST_MOVES, TAB):
                self.settings_focus = 1 - self.settings_focus
            elif code in (105, 106, 44, 46, ENTER, KP_ENTER) and value == 1:
                key = "language" if self.settings_focus == 0 else "theme"
                choices = ["system", "zh", "en"] if key == "language" else list(THEMES)
                step = -1 if code in (105, 44) else 1
                setattr(self.settings, key, choices[(choices.index(getattr(self.settings, key)) + step) % len(choices)])
                self.settings.apply()
                self.display.theme = self.settings.theme
                try:
                    self.settings.save()
                    self.status = ""
                except OSError as error:
                    self.status = str(error)
        elif self.host_form is not None:
            self.handle_form_key(code, value)
        else:
            self.handle_host_key(code, value)
        return True

    def handle_escape(self, value):
        if value == 1 and not self.escape_pressed:
            self.escape_pressed = True
            if self.on_host_list():
                self.exit_at = time.monotonic() + ESC_HOLD_SECONDS
                self.toast_until = self.exit_at
            return True
        if value != 0 or not self.escape_pressed:
            return True
        self.escape_pressed = False
        released_at = time.monotonic()
        from_list = self.exit_at is not None
        held = from_list and released_at >= self.exit_at
        self.exit_at = None
        if held:
            return False
        self.toast_until = released_at + 2 if from_list else 0
        if self.host_action is not None:
            if self.delete_confirm:
                self.delete_confirm = False
                self.host_action = None
            else:
                self.host_action = None
            self.status = ""
        elif self.settings_focus is not None:
            self.settings_focus = None
            self.status = ""
        elif self.host_form is not None:
            if self.form_focus >= 3:
                self.form_focus = 2
            else:
                self.host_form = None
            self.status = ""
        elif self.terminal:
            self.terminal.close()
            self.terminal = None
            self.refresh()
        return True

    def send_keys(self, keys):
        import websocket

        try:
            self.terminal.socket.send(keys)
        except (OSError, websocket.WebSocketException):
            self.terminal.closed.set()
            self.status = tr("连接已关闭", "Connection closed")

    def handle_host_key(self, code, value):
        if code in (*LIST_MOVES, TAB):
            order = [len(self.hosts), *range(len(self.hosts))]
            self.index = order[(order.index(self.index) + LIST_MOVES.get(code, 1)) % len(order)]
            return
        if value != 1:
            return
        if code == 31:
            self.settings_focus = 0
            self.status = ""
            return
        if self.index == len(self.hosts):
            if code in (ENTER, KP_ENTER):
                self.editing_host = None
                self.host_form = {key: "" for key, *_ in HOST_FIELDS}
                self.form_focus = 0
                try:
                    self.access = self.request("/api/access")
                except OSError:
                    self.access = None
                self.status = ""
            return
        import websocket

        host = self.hosts[self.index]
        if code == 18:
            self.host_action = host
            self.action_focus = 2
            self.handle_action_key(ENTER, 1)
            return
        if code in (ENTER, KP_ENTER):
            self.host_action = host
            self.action_focus = 0
            self.delete_confirm = False
            self.status = ""
            self.handle_action_key(ENTER, 1)
        elif code == 32 and host["id"] in self.active:
            try:
                self.request("/api/sessions/" + self.active[host["id"]]["id"], "DELETE")
                self.refresh()
            except OSError as error:
                self.status = str(error)

    def handle_action_key(self, code, value):
        import websocket

        if code in (*LIST_MOVES, TAB):
            self.action_focus = (self.action_focus + LIST_MOVES.get(code, 1)) % (2 if self.delete_confirm else 3)
            return
        if code not in (ENTER, KP_ENTER) or value != 1:
            return
        host = self.host_action
        try:
            if self.delete_confirm:
                if self.action_focus == 1:
                    self.request("/api/hosts/" + host["id"], "DELETE")
                    self.host_action = None
                    self.host_form = None
                    self.editing_host = None
                    self.refresh()
                else:
                    self.delete_confirm = False
                    self.host_action = None
            elif self.action_focus == 0:
                session = self.request("/api/sessions", "POST", {"hostId": host["id"]})
                self.terminal = Terminal(self.origin, session, self.display.columns, self.display.rows)
                self.host_action = None
            elif self.action_focus == 1:
                session = self.active.get(host["id"])
                if session:
                    self.request("/api/sessions/" + session["id"], "DELETE")
                self.host_action = None
                self.refresh()
            elif self.action_focus == 2:
                self.editing_host = host
                self.host_form = {key: str(host.get(key, "")) if key != "password" else "" for key, *_ in HOST_FIELDS}
                self.form_focus = 0
                self.host_action = None
                self.access = self.request("/api/access")
        except (OSError, websocket.WebSocketException) as error:
            self.status = str(error)

    def handle_form_key(self, code, value):
        if code in (TAB, UP, DOWN):
            direction = -1 if code == UP or (code == TAB and self.pressed & SHIFT) else 1
            low, high = (0, 3) if self.form_focus <= 2 else (3, 7 if self.editing_host else 6)
            self.form_focus = low + (self.form_focus - low + direction) % (high - low)
            return
        if code in (ENTER, KP_ENTER):
            if value == 1:
                if self.form_focus == len(HOST_FIELDS):
                    self.save_host()
                elif self.form_focus == 6:
                    self.host_action = self.editing_host
                    self.delete_confirm = True
                    self.action_focus = 0
                else:
                    self.form_focus += 1
            return
        if self.form_focus >= len(HOST_FIELDS):
            return
        key, _, _, limit = HOST_FIELDS[self.form_focus]
        current = self.host_form[key]
        if code in (BACKSPACE, DELETE):
            self.host_form[key] = current[:-1]
        elif (code in PRINTABLE or code in CARDPUTER_SYMBOLS) and len(current) < limit and not self.pressed & (CTRL | ALT):
            character = terminal_key(code, self.pressed, self.caps)
            if key == "port" and not character.isdigit():
                return
            self.host_form[key] += character
        else:
            return
        self.status = ""

    def save_host(self):
        fields = {key: value.strip() for key, value in self.host_form.items() if key != "password"}
        for key, message in (("hostname", tr("请输入主机地址", "Enter host address")), ("user", tr("请输入 SSH 用户名", "Enter SSH user"))):
            if not fields[key]:
                self.form_focus = next(i for i, field in enumerate(HOST_FIELDS) if field[0] == key)
                self.status = message
                return
        fields["name"] = fields["name"] or fields["hostname"][:80]
        fields["port"] = int(fields["port"] or "22")
        if not 1 <= fields["port"] <= 65535:
            self.form_focus = 2
            self.status = tr("端口须为 1–65535", "Port must be 1–65535")
            return
        if self.host_form["password"]:
            fields["password"] = self.host_form["password"]
        try:
            editing = self.editing_host
            mode = "password" if fields.get("password") else (editing or {}).get("authMode", "password")
            host = self.request("/api/hosts/" + editing["id"] if editing else "/api/hosts",
                                "PUT" if editing else "POST", {**fields, "authMode": mode})
        except OSError as error:
            self.status = str(error)
            return
        if self.editing_host:
            self.hosts = [host if item["id"] == host["id"] else item for item in self.hosts]
        else:
            self.index = len(self.hosts)
            self.hosts = [*self.hosts, host]
        self.editing_host = None
        self.host_form = None
        self.status = tr("主机已保存", "Host saved")

    def drain_terminal(self):
        changed = False
        if self.terminal:
            for _ in range(64):
                try:
                    data = self.terminal.events.get_nowait()
                except queue.Empty:
                    break
                changed = True
                if data is None:
                    self.terminal.closed.set()
                    self.status = tr("连接已关闭", "Connection closed")
                else:
                    self.terminal.stream.feed(data)
        return changed

    def disconnect_all(self):
        try:
            sessions = self.request("/api/state")["sessions"]
        except OSError as error:
            print(f"Zero SSH cleanup: {error}", file=sys.stderr)
            return
        for session in sessions:
            try:
                self.request("/api/sessions/" + session["id"], "DELETE")
            except OSError as error:
                print(f"Zero SSH cleanup: {error}", file=sys.stderr)


def run(args):
    def stop(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    app = App(f"http://127.0.0.1:{args.port}", Display(), Settings(args.settings_path))
    app.refresh()
    refresh_at = time.monotonic() + 3
    with open(args.framebuffer, "wb", buffering=0) as framebuffer, \
            open(args.keyboard, "rb", buffering=0) as keyboard, selectors.DefaultSelector() as poll:
        poll.register(keyboard, selectors.EVENT_READ)
        dirty = True
        try:
            while True:
                if app.exit_at is not None and time.monotonic() >= app.exit_at:
                    return
                dirty |= app.drain_terminal()
                if app.toast_until and time.monotonic() >= app.toast_until:
                    app.toast_until = 0
                    dirty = True
                if app.exit_at is None and not app.terminal and app.host_form is None and app.settings_focus is None and app.host_action is None and time.monotonic() >= refresh_at:
                    previous = (app.hosts, app.active, app.index, app.status)
                    app.refresh()
                    dirty |= previous != (app.hosts, app.active, app.index, app.status)
                    refresh_at = time.monotonic() + 3
                if dirty:
                    framebuffer.seek(0)
                    framebuffer.write(rgb565(app.draw()))
                    dirty = False
                timeout = 0.04 if app.terminal else 0.20
                if app.exit_at is not None:
                    timeout = min(timeout, max(0, app.exit_at - time.monotonic()))
                if not poll.select(timeout):
                    continue
                packets = keyboard.read(INPUT_EVENT.size * 32)
                if not packets:
                    break
                for _, _, kind, code, value in INPUT_EVENT.iter_unpack(packets):
                    if kind != EV_KEY or value not in (0, 1, 2):
                        continue
                    editing = app.host_form is not None
                    if not app.handle_key(code, value):
                        return
                    if editing and app.host_form is None:
                        refresh_at = time.monotonic() + 3
                    dirty |= value != 0 or code == ESC
        finally:
            if app.terminal:
                app.terminal.close()
            app.disconnect_all()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--framebuffer", default=FRAMEBUFFER)
    parser.add_argument("--keyboard", default=KEYBOARD)
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--lang", choices=("zh", "en"))
    parser.add_argument("--settings-path", type=Path)
    args = parser.parse_args()
    global LANGUAGE, LANG_OVERRIDE
    LANG_OVERRIDE = args.lang
    if args.lang:
        LANGUAGE = args.lang
    if args.preview:
        hosts = [
            {"id": "one", "name": "Local demo", "kind": "demo"},
            {"id": "two", "name": "Home server", "user": "admin", "hostname": "192.168.1.40"},
        ]
        Display().list_image(hosts, {"one"}, 0, "").save(args.preview)
        return 0
    try:
        run(args)
    except (OSError, ImportError) as error:
        print(f"Zero SSH: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
