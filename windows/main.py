"""ScreenBridge - share your Windows screen to your phone over Wi-Fi/LAN."""
import ctypes, io, json, math, os, secrets, socket, sys, threading, time
from ctypes import wintypes
import tkinter as tk
from tkinter import messagebox
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import mss
from PIL import Image, ImageTk, ImageDraw
import urllib.request, urllib.error
from security import PinThrottle, server_context

APP, PORT = "ScreenBridge", 8765
CFG = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP, "config.json")
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass
user32 = ctypes.windll.user32


def res(name):
    return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), name)


def load_cfg():
    try:
        with open(CFG) as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(c):
    os.makedirs(os.path.dirname(CFG), exist_ok=True)
    with open(CFG, "w") as f:
        json.dump(c, f)


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# ---------- remote input (Win32) ----------
ULP = ctypes.POINTER(ctypes.c_ulong)
class KI(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ULP)]
class MI(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", ULP)]
class _U(ctypes.Union):
    _fields_ = [("ki", KI), ("mi", MI)]
class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]

VK = {"enter": 0x0D, "esc": 0x1B, "win": 0x5B, "tab": 0x09, "backspace": 0x08,
      "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "delete": 0x2E,
      "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "space": 0x20}
SHORTCUTS = {"copy": (0x11, 0x43), "paste": (0x11, 0x56), "cut": (0x11, 0x58),
             "select_all": (0x11, 0x41), "undo": (0x11, 0x5A),
             "alt_tab": (0x12, 0x09), "win_d": (0x5B, 0x44)}
input_lock = threading.Lock()


def press(vk):
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)


def type_text(s):
    for ch in s:
        if ch == "\n":
            press(0x0D)
            continue
        if ord(ch) > 0xFFFF:
            continue
        for flag in (0x0004, 0x0006):
            i = INPUT(1, _U(ki=KI(0, ord(ch), flag, 0, None)))
            user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


def coordinate(x, extent):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not 0 <= x <= 1:
        raise ValueError("invalid normalized coordinate")
    return round(x * max(0, extent - 1))


def _click(down, up):
    user32.mouse_event(down, 0, 0, 0, 0)
    try:
        time.sleep(0.02)
    finally:
        user32.mouse_event(up, 0, 0, 0, 0)


def handle_input(d):
    if not isinstance(d, dict):
        raise ValueError("invalid input")
    t = d.get("t")
    if t not in ("move", "click", "dbl", "rclick", "scroll", "text", "key", "shortcut", "drag"):
        raise ValueError("unknown input action")
    if t in ("move", "click", "dbl", "rclick", "drag"):
        width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        x, y = coordinate(d.get("x"), width), coordinate(d.get("y"), height)
        if t == "drag":
            end_x, end_y = coordinate(d.get("x2"), width), coordinate(d.get("y2"), height)
        user32.SetCursorPos(x, y)
    if t == "click":
        _click(2, 4)
    elif t == "dbl":
        for _ in range(2):
            _click(2, 4)
    elif t == "rclick":
        _click(8, 16)
    elif t == "scroll":
        dy = int(d.get("dy", 0))
        if not -10 <= dy <= 10: raise ValueError("invalid scroll")
        user32.mouse_event(0x0800, 0, 0, dy * 120, 0)
    elif t == "text":
        text = d.get("s")
        if not isinstance(text, str) or len(text) > 2000: raise ValueError("invalid text")
        type_text(text)
    elif t == "key":
        if d.get("k") not in VK: raise ValueError("invalid key")
        press(VK[d["k"]])
    elif t == "shortcut":
        if d.get("k") not in SHORTCUTS: raise ValueError("invalid shortcut")
        modifier, key = SHORTCUTS[d["k"]]
        user32.keybd_event(modifier, 0, 0, 0)
        try: press(key)
        finally: user32.keybd_event(modifier, 0, 2, 0)
    elif t == "drag":
        generation = S.generation
        user32.mouse_event(2, 0, 0, 0, 0)
        try:
            for step in range(1, 13):
                if not S.running or not S.allow_control or generation != S.generation:
                    break
                user32.SetCursorPos(round(x + (end_x - x) * step / 12),
                                    round(y + (end_y - y) * step / 12))
                time.sleep(0.015)
        finally:
            user32.mouse_event(4, 0, 0, 0, 0)


# ---------- server ----------
class State:
    pin = ""
    allow_control = False
    running = False
    approved = set()
    lock = threading.Lock()
    ask = None  # set by GUI
    generation = 0
    throttle = PinThrottle()

S = State()

class TLSServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, context):
        self.tls_context = context
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(address, handler)

    def get_request(self):
        sock, address = self.socket.accept()
        sock.settimeout(10)
        try:
            return self.tls_context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), address
        except Exception:
            sock.close()
            raise

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def frames(q, fps, width):
    with mss.mss() as sct:
        mon = sct.monitors[1]
        while S.running:
            t0 = time.time()
            shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            try:
                point = wintypes.POINT()
                if user32.GetCursorPos(ctypes.byref(point)):
                    x, y = point.x - mon["left"], point.y - mon["top"]
                    if 0 <= x < img.width and 0 <= y < img.height:
                        draw = ImageDraw.Draw(img)
                        draw.polygon([(x, y), (x, y+22), (x+6, y+16), (x+10, y+25),
                                      (x+14, y+23), (x+10, y+14), (x+19, y+14)],
                                     fill="white", outline="black", width=2)
            except Exception:
                pass
            if width and img.width > width:
                img = img.resize((width, int(img.height * width / img.width)), Image.BILINEAR)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=q)
            yield buf.getvalue()
            time.sleep(max(0, 1 / fps - (time.time() - t0)))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a):
        pass

    def reply(self, code, body=b"", ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def auth(self, qs):
        if not S.running:
            self.reply(503, b"sharing stopped")
            return False
        supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
        correct = supplied.isascii() and secrets.compare_digest(supplied, S.pin)
        ip = self.client_address[0]
        if not S.throttle.check(ip, correct):
            self.reply(429, b"too many failed attempts; wait 60 seconds")
            return False
        if not correct:
            self.reply(401, b"bad pin")
            return False
        with S.lock:
            if ip not in S.approved:
                generation = S.generation
                if not S.ask(ip):
                    self.reply(403, b"denied")
                    return False
                if not S.running or generation != S.generation or supplied != S.pin:
                    self.reply(403, b"session changed")
                    return False
                S.approved.add(ip)
        return True

    def do_GET(self):
        u = urlparse(self.path); qs = parse_qs(u.query)
        if u.path not in ("/ping", "/stream") or not self.auth(qs):
            if u.path not in ("/ping", "/stream"):
                self.reply(404)
            return
        if u.path == "/ping":
            info = {"name": socket.gethostname(), "w": user32.GetSystemMetrics(0),
                    "h": user32.GetSystemMetrics(1), "control": S.allow_control}
            return self.reply(200, json.dumps(info).encode(), "application/json")
        try:
            q = max(10, min(90, int(qs.get("q", ["60"])[0])))
            fps = max(1, min(30, int(qs.get("fps", ["24"])[0])))
            w = max(320, min(1920, int(qs.get("w", ["1280"])[0])))
        except ValueError:
            return self.reply(400, b"invalid stream settings")
        generation = S.generation
        viewer = self.client_address[0]
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            for jpg in frames(q, fps, w):
                if generation != S.generation or viewer not in S.approved:
                    break
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpg))
                self.wfile.write(jpg + b"\r\n")
        except (ConnectionError, OSError):
            pass

    def do_POST(self):
        u = urlparse(self.path); qs = parse_qs(u.query)
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self.reply(400)
        if n < 0 or n > 8192:
            return self.reply(413)
        body = self.rfile.read(n) if n else b"{}"
        if u.path != "/input" or not self.auth(qs):
            if u.path != "/input":
                self.reply(404)
            return
        if not S.allow_control:
            return self.reply(403, b"control disabled")
        generation = S.generation
        try:
            with input_lock:
                if (not S.running or not S.allow_control or generation != S.generation
                        or self.client_address[0] not in S.approved):
                    return self.reply(403, b"control disabled")
                handle_input(json.loads(body))
        except Exception:
            return self.reply(400)
        self.reply(200, b"ok")


# ---------- GUI ----------
BG, CARD, ACC, FG, MUTED = "#0f172a", "#1e293b", "#6366f1", "#e2e8f0", "#94a3b8"


class App:
    def __init__(self):
        self.cfg = load_cfg()
        S.pin = "%08d" % secrets.randbelow(100000000)
        self.server = None
        r = self.root = tk.Tk()
        r.title(APP); r.configure(bg=BG); r.geometry("420x640"); r.resizable(False, False)
        try:
            self.logo = tk.PhotoImage(file=res("icon.png")).subsample(8, 8)
            r.iconphoto(True, self.logo)
            tk.Label(r, image=self.logo, bg=BG).pack(pady=(18, 0))
        except Exception:
            pass
        tk.Label(r, text=APP, font=("Segoe UI", 22, "bold"), fg=FG, bg=BG).pack()
        tk.Label(r, text="Mirror this PC to your phone over Wi-Fi", fg=MUTED, bg=BG).pack(pady=(0, 12))

        card = tk.Frame(r, bg=CARD, padx=16, pady=14); card.pack(fill="x", padx=22)
        self.status = tk.Label(card, text="● Stopped", fg="#f87171", bg=CARD, font=("Segoe UI", 11, "bold"))
        self.status.pack(anchor="w")
        tk.Label(card, text="Enter on your phone:", fg=MUTED, bg=CARD).pack(anchor="w", pady=(8, 0))
        self.addr = tk.Label(card, text=f"{local_ip()}", fg=FG, bg=CARD, font=("Consolas", 20, "bold"))
        self.addr.pack(anchor="w")
        tk.Label(card, text=f"Port {PORT}", fg=MUTED, bg=CARD).pack(anchor="w")
        row = tk.Frame(card, bg=CARD); row.pack(fill="x", pady=(10, 0))
        tk.Label(row, text="PIN", fg=MUTED, bg=CARD).pack(side="left")
        self.pin_lbl = tk.Label(row, text=S.pin, fg=FG, bg=CARD, font=("Consolas", 20, "bold")); self.pin_lbl.pack(side="left", padx=10)
        self.btn(row, "New PIN", self.new_pin, CARD).pack(side="right")

        self.ctl = tk.BooleanVar(value=False)
        tk.Checkbutton(r, text="Allow phone to control mouse & keyboard", variable=self.ctl, command=self.toggle_ctl,
                       fg=FG, bg=BG, selectcolor=CARD, activebackground=BG, activeforeground=FG).pack(pady=10)
        self.main_btn = self.btn(r, "Start sharing", self.toggle, ACC, big=True); self.main_btn.pack(fill="x", padx=22)
        self.btn(r, "View phone screen on this PC", self.open_phone, CARD, big=True).pack(fill="x", padx=22, pady=(8, 0))

        tk.Label(r, text="Approved devices", fg=MUTED, bg=BG).pack(anchor="w", padx=22, pady=(14, 2))
        self.lst = tk.Listbox(r, height=4, bg=CARD, fg=FG, bd=0, highlightthickness=0); self.lst.pack(fill="x", padx=22)
        self.btn(r, "Revoke all devices", self.revoke, BG).pack(pady=6)
        S.ask = self.ask
        r.protocol("WM_DELETE_WINDOW", self.quit)
        r.after(300, self.first_run)

    def btn(self, parent, text, cmd, color, big=False):
        return tk.Button(parent, text=text, command=cmd, bg=color, fg="white", relief="flat", bd=0, cursor="hand2",
                         activebackground="#4f46e5", activeforeground="white",
                         font=("Segoe UI", 12 if big else 9, "bold" if big else "normal"), pady=8 if big else 2)

    def first_run(self):
        if self.cfg.get("consented"):
            return
        ok = messagebox.askokcancel(
            "Permissions needed",
            f"{APP} needs your permission to:\n\n"
            "• Capture your screen and send it to approved devices\n"
            f"• Run a LAN server on port {PORT}; host identity is not verified\n"
            "• Allow remote mouse and keyboard input only if you enable it\n\n"
            "Every new device must know your PIN and be approved by you.", parent=self.root)
        if not ok:
            self.quit(); return
        if messagebox.askyesno("Windows Firewall", "Allow ScreenBridge through Windows Firewall (private networks)?\n"
                               "Windows will ask for administrator permission.", parent=self.root):
            args = f'advfirewall firewall add rule name="{APP}" dir=in action=allow protocol=TCP localport={PORT} profile=any'
            ctypes.windll.shell32.ShellExecuteW(None, "runas", "netsh", args, None, 0)
        self.cfg["consented"] = True; save_cfg(self.cfg)

    def ask(self, ip):
        ev, out = threading.Event(), [False]
        def show():
            self.root.deiconify(); self.root.lift()
            out[0] = messagebox.askyesno("Allow device?", f"Device {ip} wants to view your screen"
                                         f"{' and control this PC' if S.allow_control else ''}.\n\nAllow it?", parent=self.root)
            ev.set()
        self.root.after(0, show)
        ev.wait(60)
        self.root.after(200, self.refresh)
        return out[0]

    def refresh(self):
        self.lst.delete(0, "end")
        for ip in sorted(S.approved):
            self.lst.insert("end", ip)

    def open_phone(self):
        messagebox.showerror(APP, "Use the current ScreenBridge interface.", parent=self.root)

    def revoke(self):
        S.generation += 1
        S.approved.clear(); self.refresh()

    def new_pin(self):
        S.pin = "%08d" % secrets.randbelow(100000000); self.pin_lbl.config(text=S.pin)
        self.cfg.pop("pin", None); save_cfg(self.cfg); self.revoke()

    def toggle_ctl(self):
        S.allow_control = self.ctl.get()

    def toggle(self):
        if S.running:
            S.running = False
            self.revoke()
            self.server.shutdown(); self.server.server_close()
            self.status.config(text="● Stopped", fg="#f87171"); self.main_btn.config(text="Start sharing", bg=ACC)
        else:
            try:
                context = server_context(os.path.join(os.path.dirname(CFG), "identity"))
                self.server = TLSServer(("0.0.0.0", PORT), Handler, context)
            except OSError as e:
                return messagebox.showerror(APP, f"Cannot start server: {e}")
            except Exception as e:
                return messagebox.showerror(APP, f"Cannot initialize TLS identity: {e}")
            self.server.daemon_threads = True
            S.running = True
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
            self.addr.config(text=local_ip())
            self.status.config(text="● Sharing", fg="#4ade80"); self.main_btn.config(text="Stop sharing", bg="#dc2626")

    def quit(self):
        S.running = False
        self.root.destroy()
        os._exit(0)


if __name__ == "__main__":
    from design import build_app
    build_app(sys.modules[__name__]).root.mainloop()
