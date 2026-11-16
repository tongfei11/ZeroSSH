#!/usr/bin/env python3
"""SSH host picker and terminal for the CardputerZero LCD and keyboard."""

import argparse
import json
import queue
import selectors
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
        draw.text((311, 15), f"{len(active)} 个会话", font=self.ui, fill=ACCENT, anchor="rm")
        first = min(max(0, min(index, len(hosts) - 1) - 1), max(0, len(hosts) - 3))
        for row, host in enumerate(hosts[first:first + 3], start=first):
            y = 30 + (row - first) * 25
            selected = row == index
            draw.rounded_rectangle(
                (7, y, 312, y + 23), radius=5, fill=SURFACE,
                outline=ACCENT if selected else SURFACE,
            )
            draw.text((14, y + 12), fitted(self.ui, host["name"], 143), font=self.ui,
                      fill=INK, anchor="lm")
            if host["id"] in active:
                detail = "已连接"
            elif host.get("kind") == "demo":
                detail = "Local shell"
            else:
                detail = f'{host["user"]}@{host["hostname"]}'
            draw.text((304, y + 12), fitted(self.ui, detail, 132), font=self.ui,
                      fill=ACCENT if host["id"] in active else MUTED, anchor="rm")
        if not hosts:
            draw.text((14, 66), "暂无主机", font=self.ui, fill=MUTED, anchor="lm")
        y = 105
        draw.rounded_rectangle((7, y, 312, y + 23), radius=5, fill=SURFACE,
                               outline=ACCENT if index == len(hosts) else SURFACE)
        draw.text((14, y + 12), "+ 添加主机", font=self.ui, fill=ACCENT, anchor="lm")
        draw.text((9, 139), fitted(self.ui, status, 300), font=self.ui, fill=MUTED, anchor="lm")
        draw.rectangle((0, 149, 319, 169), fill=SURFACE)
        draw.text((9, 159), "F/X 选择  Enter 打开  Bksp 结束  长按 Esc 3 秒退出", font=self.ui,
                  fill=INK, anchor="lm")
        return image

    def host_form_image(self, fields, focus, status):
        image = Image.new("RGB", SIZE, BACKGROUND)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 319, 22), fill=SURFACE)
        draw.text((9, 11), "添加主机", font=self.title, fill=INK, anchor="lm")
        draw.text((311, 11), "Tab/↑↓ 切换   Esc 取消", font=self.ui, fill=MUTED, anchor="rm")
        for row, (key, label, placeholder, _) in enumerate(HOST_FIELDS):
            y = 25 + row * 19
            selected = row == focus
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
        draw.text((9, 127), "使用 SSH 私钥请使用 Web 访问", font=self.ui, fill=MUTED, anchor="lm")
        draw.text((9, 142), "电脑 SSH 转发后访问 http://127.0.0.1:4173", font=self.ui,
                  fill=MUTED, anchor="lm")
        draw.rounded_rectangle((7, 151, 312, 168), radius=4, fill=SURFACE,
                               outline=ACCENT if focus == len(HOST_FIELDS) else SURFACE)
        draw.text((160, 159), fitted(self.ui, status or "Enter 保存主机", 294), font=self.ui,
                  fill=INK if status else ACCENT, anchor="mm")
        return image

    def terminal_image(self, name, screen, status):
        image = Image.new("RGB", SIZE, BACKGROUND)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 319, 17), fill=SURFACE)
        draw.text((5, 9), fitted(self.ui, name, 190), font=self.ui, fill=ACCENT, anchor="lm")
        draw.text((315, 9), "Esc 返回主机", font=self.ui, fill=INK, anchor="rm")
        for line_number in range(screen.lines):
            line = screen.buffer[line_number]
            for column in range(screen.columns):
                cell = line[column]
                x = 4 + column * self.cell_width
                y = 19 + line_number * self.cell_height
                fg = self.color(cell.fg, INK)
                bg = self.color(cell.bg, BACKGROUND)
                if cell.reverse:
                    fg, bg = bg, fg
                if bg != BACKGROUND:
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

    @staticmethod
    def color(value, default):
        if len(value) == 6 and all(c in "0123456789abcdefABCDEF" for c in value):
            return "#" + value
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
    def __init__(self, origin, display):
        self.origin = origin
        self.display = display
        self.opener = build_opener(ProxyHandler({}))
        self.hosts = []
        self.active = {}
        self.index = 0
        self.status = ""
        self.terminal = None
        self.host_form = None
        self.form_focus = 0
        self.pressed = set()
        self.caps = False
        self.exit_at = None

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
            self.status = "Waiting for Zero SSH service"
            return
        selecting_add = bool(self.hosts) and self.index == len(self.hosts)
        selected = self.hosts[self.index]["id"] if self.index < len(self.hosts) else None
        self.hosts = data["hosts"]
        self.index = len(self.hosts) if selecting_add else next(
            (i for i, host in enumerate(self.hosts) if host["id"] == selected),
            min(self.index, max(0, len(self.hosts) - 1)),
        )
        self.active = {item["hostId"]: item for item in data["sessions"] if not item.get("exited")}
        self.status = ""

    def draw(self):
        if self.terminal:
            return self.display.terminal_image(self.terminal.name, self.terminal.screen, self.status)
        if self.host_form is not None:
            return self.display.host_form_image(self.host_form, self.form_focus, self.status)
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
        elif self.host_form is not None:
            self.handle_form_key(code, value)
        else:
            self.handle_host_key(code, value)
        return True

    def handle_escape(self, value):
        if value == 1 and self.exit_at is None:
            self.exit_at = time.monotonic() + ESC_HOLD_SECONDS
        if value != 0 or self.exit_at is None:
            return True
        held = time.monotonic() >= self.exit_at
        self.exit_at = None
        if held:
            return False
        if self.host_form is not None:
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
            self.status = "Connection closed"

    def handle_host_key(self, code, value):
        if code in LIST_MOVES:
            self.index = (self.index + LIST_MOVES[code]) % (len(self.hosts) + 1)
            return
        if value != 1:
            return
        if self.index == len(self.hosts):
            if code in (ENTER, KP_ENTER):
                self.host_form = {key: "" for key, *_ in HOST_FIELDS}
                self.form_focus = 0
                self.status = ""
            return
        import websocket

        host = self.hosts[self.index]
        if code in (ENTER, KP_ENTER):
            try:
                session = self.request("/api/sessions", "POST", {"hostId": host["id"]})
                self.terminal = Terminal(self.origin, session, self.display.columns, self.display.rows)
                self.status = ""
            except (OSError, websocket.WebSocketException) as error:
                self.status = str(error)
        elif code in (BACKSPACE, DELETE) and host["id"] in self.active:
            try:
                self.request("/api/sessions/" + self.active[host["id"]]["id"], "DELETE")
                self.refresh()
            except OSError as error:
                self.status = str(error)

    def handle_form_key(self, code, value):
        if code in (TAB, UP, DOWN):
            direction = -1 if code == UP or (code == TAB and self.pressed & SHIFT) else 1
            self.form_focus = (self.form_focus + direction) % (len(HOST_FIELDS) + 1)
            return
        if code in (ENTER, KP_ENTER):
            if value == 1:
                if self.form_focus == len(HOST_FIELDS):
                    self.save_host()
                else:
                    self.form_focus += 1
            return
        if self.form_focus == len(HOST_FIELDS):
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
        for key, message in (("hostname", "请输入主机地址"), ("user", "请输入 SSH 用户名")):
            if not fields[key]:
                self.form_focus = next(i for i, field in enumerate(HOST_FIELDS) if field[0] == key)
                self.status = message
                return
        fields["name"] = fields["name"] or fields["hostname"][:80]
        fields["port"] = int(fields["port"] or "22")
        if not 1 <= fields["port"] <= 65535:
            self.form_focus = 2
            self.status = "端口须为 1–65535"
            return
        if self.host_form["password"]:
            fields["password"] = self.host_form["password"]
        try:
            host = self.request("/api/hosts", "POST", {**fields, "authMode": "password"})
        except OSError as error:
            self.status = str(error)
            return
        self.index = len(self.hosts)
        self.hosts = [*self.hosts, host]
        self.host_form = None
        self.status = "主机已保存"

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
                    self.status = "Connection closed"
                else:
                    self.terminal.stream.feed(data)
        return changed


def run(args):
    app = App(f"http://127.0.0.1:{args.port}", Display())
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
                if app.exit_at is None and not app.terminal and app.host_form is None and time.monotonic() >= refresh_at:
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--framebuffer", default=FRAMEBUFFER)
    parser.add_argument("--keyboard", default=KEYBOARD)
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args()
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
