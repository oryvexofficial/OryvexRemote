"""ScreenBridge desktop: a clear, consent-first LAN sharing workspace."""
import ctypes
import io
import ipaddress
import json
import os
import secrets
import socket
import ssl
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog
from urllib import error, request
from PIL import Image, ImageTk
from security import open_lan
from mjpeg import read_frame

BG = '#f1f4ed'
PAPER = '#fcfdf9'
INK = '#19332e'
MUTED = '#536c64'
ACCENT = '#a94d2c'
ACCENT_DARK = '#80381f'
TINT = '#f6e4d8'
LINE = '#d5e0d7'
DARK = '#19332e'
FONT = 'Manrope'


def build_app(m):
    try:
        for filename in ('Manrope-Regular.ttf', 'Manrope-Bold.ttf'):
            ctypes.windll.gdi32.AddFontResourceExW(m.res(filename), 0x10, 0)
    except Exception:
        pass

    class Viewer:
        def __init__(self, root):
            self.gen = 0
            self.latest = None
            self.photo = None
            self.pending = False
            self.connection = None
            self.control = False
            self.control_allowed = False
            self.drag_mode = False
            self.drag_start = None
            self.last_move = 0.0
            self.move_busy = threading.Lock()
            self.ip = tk.StringVar()
            self.pin = tk.StringVar()
            self.device = tk.StringVar(value='Android phone')
            self.win = tk.Toplevel(root)
            self.win.title('Connect to a device | ScreenBridge')
            self.win.configure(bg=BG)
            self.win.geometry('860x720')
            self.win.minsize(600, 500)
            head = tk.Frame(self.win, bg=PAPER, padx=24, pady=18); head.pack(fill='x')
            tk.Label(head, text='CONNECT TO A DEVICE', font=(FONT, 10, 'bold'), fg=ACCENT, bg=PAPER).pack(anchor='w')
            tk.Label(head, text='Your remote screen', font=(FONT, 20, 'bold'), fg=INK, bg=PAPER).pack(anchor='w', pady=(3, 12))
            controls = tk.Frame(head, bg=PAPER); controls.pack(fill='x')
            tk.OptionMenu(controls, self.device, 'Android phone', 'Windows PC').pack(side='left', padx=(0, 8))
            for var, label, width in ((self.ip, 'IP address', 21), (self.pin, 'PIN', 10)):
                tk.Label(controls, text=label, bg=PAPER, fg=MUTED, font=(FONT, 9)).pack(side='left', padx=(0, 4))
                entry = tk.Entry(controls, textvariable=var, font=(FONT, 10), width=width, relief='solid', bd=1)
                entry.pack(side='left', padx=(0, 8), ipady=7)
                entry.insert(0, '')
            tk.Button(controls, text='Connect', command=self.connect, bg=ACCENT, fg='white', activebackground=ACCENT_DARK, activeforeground='white', relief='flat', padx=16, pady=6, font=(FONT, 10, 'bold'), cursor='hand2').pack(side='left')
            tk.Label(head, text='Same private network only. Host identity is not verified.', bg=PAPER, fg=ACCENT_DARK, font=(FONT, 9)).pack(anchor='w', pady=(10, 0))
            tool = tk.Frame(self.win, bg=BG, padx=24, pady=10); tool.pack(fill='x')
            self.status = tk.Label(tool, text='Choose the device, then enter its IP and PIN.', fg=MUTED, bg=BG, font=(FONT, 9), anchor='w'); self.status.pack(side='left', fill='x', expand=True)
            self.control_btn = tk.Button(tool, text='Control: off', command=self.toggle_control, bg=PAPER, fg=INK, relief='flat', padx=10, pady=5, cursor='hand2'); self.control_btn.pack(side='left', padx=3)
            self.drag_btn = tk.Button(tool, text='Drag: off', command=self.toggle_drag, bg=PAPER, fg=INK, relief='flat', padx=10, pady=5, cursor='hand2'); self.drag_btn.pack(side='left', padx=3)
            tk.Button(tool, text='Type', command=self.type_text, bg=PAPER, fg=INK, relief='flat', padx=10, pady=5, cursor='hand2').pack(side='left', padx=3)
            for label, key in (('Back', 'back'), ('Home', 'home'), ('Recents', 'recents')):
                tk.Button(tool, text=label, command=lambda k=key: self.nav(k), bg=PAPER, fg=INK, relief='flat', padx=8, pady=5, cursor='hand2').pack(side='left', padx=2)
            tk.Button(tool, text='Screenshot', command=self.shot, bg=PAPER, fg=INK, relief='flat', padx=10, pady=5, cursor='hand2').pack(side='left', padx=3)
            tk.Button(tool, text='Disconnect', command=self.disconnect, bg=PAPER, fg=ACCENT_DARK, relief='flat', padx=10, pady=5, cursor='hand2').pack(side='left', padx=3)
            self.canvas = tk.Canvas(self.win, bg=DARK, highlightthickness=0, cursor='crosshair'); self.canvas.pack(fill='both', expand=True, padx=24, pady=(0, 24))
            self.canvas.bind('<Configure>', lambda e: self.show())
            self.canvas.bind('<Button-1>', self.left_down)
            self.canvas.bind('<ButtonRelease-1>', self.left_up)
            self.canvas.bind('<Motion>', self.move_pointer)
            self.canvas.bind('<Double-Button-1>', self.double_down)
            self.canvas.bind('<Button-3>', self.right_click)
            self.canvas.bind('<MouseWheel>', lambda e: self.send({'t': 'scroll', 'dy': 1 if e.delta > 0 else -1}))
            self.win.protocol('WM_DELETE_WINDOW', self.close)

        def say(self, text):
            try:
                self.win.after(0, lambda: self.status.config(text=text) if self.win.winfo_exists() else None)
            except Exception:
                pass

        def connect(self):
            try:
                host = str(ipaddress.IPv4Address(self.ip.get().strip()))
            except ValueError:
                return self.say('Enter a valid local IPv4 address.')
            pin = self.pin.get().strip()
            if not pin.isascii() or not pin.isdigit() or len(pin) != 8:
                return self.say('Enter the eight-digit PIN shown on the sharing device.')
            self.disconnect(silent=True)
            self.gen += 1
            gen = self.gen
            self.host, self.session_pin = host, pin
            self.port = 8766 if self.device.get() == 'Android phone' else 8765
            self.control_allowed = False
            self.say('Connecting. Approve on the other device if prompted.')
            threading.Thread(target=self.read, args=(gen, host, pin, self.port), daemon=True).start()

        def read(self, gen, host, pin, port):
            conn = None
            try:
                with open_lan(host, port, '/ping', pin, timeout=70 if port == 8765 else 15) as r:
                    info = json.loads(r.read(8192))
                if gen != self.gen: return
                self.control_allowed = bool(info.get('control', False))
                self.say('Connected. Waiting for the first frame...')
                conn = open_lan(host, port, '/stream', pin, timeout=15)
                if gen != self.gen:
                    conn.close()
                    return
                self.connection = conn
                self.say('Connected. Host identity is not verified.')
                while gen == self.gen:
                    data = read_frame(conn)
                    image = Image.open(io.BytesIO(data)); image.load()
                    if gen != self.gen: break
                    self.latest = image
                    if not self.pending:
                        self.pending = True
                        self.win.after(0, self.draw_pending)
            except error.HTTPError as e:
                if gen == self.gen:
                    text = {401: 'Wrong PIN.', 403: 'Host denied access.', 429: 'Too many PIN attempts. Wait 60 seconds.', 503: 'Host is not sharing.'}.get(e.code, f'Host error HTTP {e.code}.')
                    self.say(text)
            except Exception as exc:
                if gen == self.gen:
                    reason = exc.reason if isinstance(exc, error.URLError) else exc
                    if isinstance(reason, (socket.timeout, TimeoutError)):
                        msg = 'Timed out: check host sharing, firewall, Wi-Fi and target device type.'
                    elif isinstance(reason, ssl.SSLError):
                        msg = 'TLS handshake failed. Update both apps; check Android sharing is running.'
                    elif isinstance(reason, ConnectionRefusedError):
                        msg = 'Connection refused: host sharing is stopped, wrong IP or wrong device type.'
                    else:
                        msg = f'Connection failed ({type(reason).__name__}). Check IP and sharing.'
                    self.say(msg)
            finally:
                if conn:
                    try: conn.close()
                    except Exception: pass
                    if self.connection is conn: self.connection = None

        def draw_pending(self):
            self.pending = False
            self.show()

        def show(self):
            if not self.latest or not self.win.winfo_exists(): return
            cw, ch = max(self.canvas.winfo_width(), 1), max(self.canvas.winfo_height(), 1)
            scale = min(cw / self.latest.width, ch / self.latest.height)
            size = (max(1, int(self.latest.width * scale)), max(1, int(self.latest.height * scale)))
            self.image_box = ((cw-size[0])/2, (ch-size[1])/2, size[0], size[1])
            self.photo = ImageTk.PhotoImage(self.latest.resize(size, Image.Resampling.BILINEAR))
            self.canvas.delete('all'); self.canvas.create_image(cw//2, ch//2, image=self.photo)

        def toggle_control(self):
            if self.control:
                self.control = False
                self.control_btn.config(text='Control: off')
                return
            if not self.connection:
                return self.say('Connect to a device first.')
            host, port, pin, gen = self.host, self.port, self.session_pin, self.gen
            self.say('Checking whether the Windows host permits control...')
            def check():
                try:
                    with open_lan(host, port, '/ping', pin, timeout=5) as response:
                        allowed = bool(json.loads(response.read(8192)).get('control', False))
                except Exception:
                    return self.say('Cannot check control permission. Reconnect and try again.')
                def finish():
                    if gen != self.gen: return
                    self.control_allowed = allowed
                    self.control = allowed
                    self.control_btn.config(text='Control: on' if allowed else 'Control: off')
                    self.say('Control enabled.' if allowed else ('On the phone, turn on Remote control in ScreenBridge, then click Control again.' if port == 8766 else 'On the PC, check Allow remote mouse and keyboard, then click Control again.'))
                self.win.after(0, finish)
            threading.Thread(target=check, daemon=True).start()

        def pointer(self, kind, event):
            if self.port == 8766 and kind in ('click', 'dbl'): return  # handled on press/release
            if not self.control or not hasattr(self, 'image_box'): return
            x, y, w, h = self.image_box
            if not (x <= event.x <= x+w and y <= event.y <= y+h): return
            self.send({'t': kind, 'x': (event.x-x)/w, 'y': (event.y-y)/h})

        def toggle_drag(self):
            if not self.control:
                return self.say('Turn control on first.')
            self.drag_mode = not self.drag_mode
            self.drag_start = None
            self.drag_btn.config(text='Drag: on' if self.drag_mode else 'Drag: off')
            self.say('Drag mode: press and release to move an item.' if self.drag_mode else 'Drag mode off.')

        def normalized(self, event):
            if not hasattr(self, 'image_box'): return None
            x, y, w, h = self.image_box
            if not (x <= event.x <= x+w and y <= event.y <= y+h): return None
            return ((event.x-x)/w, (event.y-y)/h)

        def double_down(self, event):
            # Tk replaces the 2nd Button-1 with this event; treat it as a normal press.
            if self.port == 8766: return self.left_down(event)
            self.pointer('dbl', event)

        def right_click(self, event):
            if self.port == 8766:
                if self.control: self.send({'t': 'nav', 'k': 'back'})  # right-click = Android Back
            else:
                self.pointer('rclick', event)

        def left_down(self, event):
            if not self.control: return
            if self.port == 8766:
                self.press = (self.normalized(event), time.monotonic(), event.x, event.y)
                return
            if self.drag_mode: self.drag_start = self.normalized(event)
            else: self.pointer('click', event)

        def left_up(self, event):
            if self.control and self.port == 8766:
                press, self.press = getattr(self, 'press', None), None
                end = self.normalized(event)
                if not press or not press[0] or not end: return
                start, t0, x0, y0 = press
                held = time.monotonic() - t0
                if ((event.x - x0) ** 2 + (event.y - y0) ** 2) ** 0.5 > 8:
                    ms = int(max(120, min(1500, held * 1000)))
                    self.send({'t': 'drag', 'x': start[0], 'y': start[1], 'x2': end[0], 'y2': end[1], 'ms': ms})
                elif held > 0.55:
                    self.send({'t': 'rclick', 'x': start[0], 'y': start[1]})
                else:
                    self.send({'t': 'click', 'x': start[0], 'y': start[1]})
                return
            if not self.control or not self.drag_mode: return
            start, end = self.drag_start, self.normalized(event)
            self.drag_start = None
            if start and end:
                self.send({'t': 'drag', 'x': start[0], 'y': start[1], 'x2': end[0], 'y2': end[1]})

        def move_pointer(self, event):
            if not self.control or self.drag_mode or self.port == 8766 or time.monotonic() - self.last_move < 0.05: return
            point = self.normalized(event)
            if point is None or not self.move_busy.acquire(blocking=False): return
            self.last_move = time.monotonic()
            host, pin, port, gen = self.host, self.session_pin, self.port, self.gen
            def move():
                try:
                    if gen == self.gen and self.control:
                        payload = json.dumps({'t': 'move', 'x': point[0], 'y': point[1]}).encode()
                        with open_lan(host, port, '/input', pin, data=payload, timeout=3): pass
                except Exception: pass
                finally: self.move_busy.release()
            threading.Thread(target=move, daemon=True).start()

        def send(self, payload):
            if not self.control or not self.control_allowed: return
            host, pin, port = self.host, self.session_pin, self.port
            def post():
                try:
                    with open_lan(host, port, '/input', pin, data=json.dumps(payload).encode(), timeout=3): pass
                except Exception: self.say('Could not send control input.')
            threading.Thread(target=post, daemon=True).start()

        def nav(self, key):
            if self.port != 8766: return self.say('Back, Home and Recents work on Android hosts only.')
            if not self.control: return self.say('Turn control on first.')
            self.send({'t': 'nav', 'k': key})

        def type_text(self):
            if not self.control: return self.say('Turn control on first.')
            text = simpledialog.askstring('Type on Windows', 'Text to send:', parent=self.win)
            if text: self.send({'t': 'text', 's': text[:2000]})

        def shot(self):
            if self.latest is None: return self.say('Connect to a screen first.')
            folder = os.path.join(os.path.expanduser('~'), 'Pictures', 'ScreenBridge')
            try:
                os.makedirs(folder, exist_ok=True)
                path = os.path.join(folder, f'ScreenBridge_{int(time.time())}.png')
                self.latest.save(path); self.say('Screenshot saved to Pictures/ScreenBridge.')
            except Exception as e: self.say(f'Could not save screenshot: {e}')

        def disconnect(self, silent=False):
            self.gen += 1
            self.control = False
            self.control_allowed = False
            self.drag_mode = False
            self.drag_start = None
            self.drag_btn.config(text='Drag: off')
            if self.connection:
                conn = self.connection
                self.connection = None
                threading.Thread(target=lambda: conn.close(), daemon=True).start()
            self.control_btn.config(text='Control: off')
            self.latest = None
            self.canvas.delete('all')
            if not silent: self.say('Disconnected.')

        def close(self):
            self.disconnect(silent=True)
            self.win.destroy()

    class FreshApp(m.App):
        def __init__(self):
            self.cfg = m.load_cfg()
            self.cfg.pop('pin', None)
            self.cfg.pop('phone_pin', None)
            m.save_cfg(self.cfg)
            m.S.pin = '%08d' % secrets.randbelow(100000000)
            m.S.allow_control = False
            self.server = None
            r = self.root = tk.Tk()
            r.title('ScreenBridge'); r.configure(bg=BG); r.geometry('900x850'); r.minsize(760, 760)
            try:
                animations = ctypes.c_int()
                ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(animations), 0)
                if animations.value:
                    r.attributes('-alpha', 0.0)
                    def reveal(step=0):
                        if not r.winfo_exists(): return
                        r.attributes('-alpha', min(1.0, (step / 10) ** 0.7))
                        if step < 10: r.after(20, reveal, step + 1)
                    r.after(30, reveal)
            except Exception:
                pass
            try:
                self.logo = tk.PhotoImage(file=m.res('icon.png')).subsample(8, 8)
                r.iconphoto(True, self.logo)
            except Exception: pass
            header = tk.Frame(r, bg=BG); header.pack(fill='x', padx=36, pady=(28, 12))
            brand = tk.Frame(header, bg=BG); brand.pack(side='left')
            tk.Label(brand, text='●  SCREENBRIDGE', font=(FONT, 11, 'bold'), bg=BG, fg=INK).pack(anchor='w')
            tk.Label(header, text='LOCAL CONNECTION', font=(FONT, 9, 'bold'), bg=BG, fg=MUTED).pack(side='right')
            title = tk.Frame(r, bg=BG); title.pack(fill='x', padx=36, pady=(10, 18))
            tk.Label(title, text='Closer, from anywhere\non your network.', justify='left', font=(FONT, 25, 'bold'), bg=BG, fg=INK).pack(anchor='w')
            tk.Label(title, text='Share this screen or connect to another device.', font=(FONT, 11), bg=BG, fg=MUTED).pack(anchor='w', pady=(7, 0))
            row = tk.Frame(r, bg=BG); row.pack(fill='both', expand=True, padx=36)
            left = tk.Frame(row, bg=PAPER, padx=22, pady=18); left.pack(side='left', fill='both', expand=True, padx=(0, 8))
            right = tk.Frame(row, bg=DARK, padx=22, pady=18); right.pack(side='left', fill='both', expand=True, padx=(8, 0))
            tk.Label(left, text='SHARE THIS PC', font=(FONT, 9, 'bold'), bg=PAPER, fg=ACCENT).pack(anchor='w')
            self.status = tk.Label(left, text='●  Not sharing', font=(FONT, 12, 'bold'), bg=PAPER, fg=MUTED); self.status.pack(anchor='w', pady=(10, 12))
            tk.Label(left, text='YOUR IP ADDRESS', font=(FONT, 9, 'bold'), bg=PAPER, fg=MUTED).pack(anchor='w')
            self.addr = tk.Label(left, text=m.local_ip(), font=(FONT, 20, 'bold'), bg=PAPER, fg=INK); self.addr.pack(anchor='w', pady=(3, 4))
            tk.Label(left, text='PORT  8765', font=(FONT, 9), bg=PAPER, fg=MUTED).pack(anchor='w')
            pinrow = tk.Frame(left, bg=TINT, padx=12, pady=10); pinrow.pack(fill='x', pady=(16, 8))
            tk.Label(pinrow, text='ACCESS PIN', font=(FONT, 9, 'bold'), bg=TINT, fg=ACCENT_DARK).pack(anchor='w')
            self.pin_lbl = tk.Label(pinrow, text=m.S.pin, font=(FONT, 19, 'bold'), bg=TINT, fg=INK); self.pin_lbl.pack(side='left', anchor='w')
            tk.Button(pinrow, text='New PIN', command=self.new_pin, bg=TINT, fg=ACCENT_DARK, relief='flat', cursor='hand2').pack(side='right')
            tk.Label(left, text='For a fixed IP, reserve this address in your router.', font=(FONT, 9), bg=PAPER, fg=MUTED, wraplength=310, justify='left').pack(anchor='w', pady=(6, 0))
            tk.Button(left, text='Refresh IP', command=lambda: self.addr.config(text=m.local_ip()), bg=PAPER, fg=ACCENT_DARK, relief='flat', cursor='hand2').pack(anchor='w')
            self.ctl = tk.BooleanVar(value=False)
            tk.Checkbutton(left, text='Allow remote mouse and keyboard', variable=self.ctl, command=self.toggle_ctl, bg=PAPER, fg=INK, selectcolor=PAPER, activebackground=PAPER, font=(FONT, 9), anchor='w').pack(fill='x', pady=(4, 10))
            self.main_btn = tk.Button(left, text='Start sharing', command=self.toggle, bg=ACCENT, fg='white', activebackground=ACCENT_DARK, activeforeground='white', relief='flat', font=(FONT, 11, 'bold'), pady=12, cursor='hand2'); self.main_btn.pack(fill='x')
            tk.Button(left, text='Diagnose / repair connection', command=self.diagnose, bg=PAPER, fg=ACCENT_DARK, activebackground=TINT, relief='flat', font=(FONT, 9, 'bold'), pady=7, cursor='hand2').pack(fill='x')
            tk.Button(left, text='Copy IP and PIN', command=self.copy_details, bg=PAPER, fg=ACCENT_DARK, activebackground=TINT, relief='flat', font=(FONT, 9, 'bold'), pady=7, cursor='hand2').pack(fill='x')
            tk.Label(right, text='CONNECT TO A DEVICE', font=(FONT, 9, 'bold'), bg=DARK, fg='#ffac96').pack(anchor='w')
            tk.Label(right, text='See their screen.', font=(FONT, 19, 'bold'), bg=DARK, fg='white').pack(anchor='w', pady=(14, 8))
            tk.Label(right, text='Connect to an Android phone\nor another Windows PC.', justify='left', font=(FONT, 10), bg=DARK, fg='#c0d2c9').pack(anchor='w')
            tk.Label(right, text='1   Connect both devices to the same router\n\n2   Start sharing on the other device\n\n3   Enter its IP address and PIN\n\n4   Approve if prompted', justify='left', anchor='w', font=(FONT, 10), bg=DARK, fg='white').pack(fill='x', pady=(22, 18))
            tk.Button(right, text='Open remote screen  →', command=self.open_phone, bg='#fff5ef', fg=INK, activebackground='#fbece6', relief='flat', font=(FONT, 10, 'bold'), pady=12, cursor='hand2').pack(fill='x', side='bottom')
            bottom = tk.Frame(r, bg=BG); bottom.pack(fill='x', padx=36, pady=(14, 18))
            tk.Label(bottom, text='APPROVED DEVICES', font=(FONT, 9, 'bold'), bg=BG, fg=MUTED).pack(anchor='w')
            self.lst = tk.Listbox(bottom, height=2, bg=PAPER, fg=INK, bd=0, highlightthickness=0, font=(FONT, 9)); self.lst.pack(fill='x', pady=(6, 0))
            tk.Button(bottom, text='Revoke all', command=self.revoke, bg=BG, fg=ACCENT_DARK, relief='flat', cursor='hand2').pack(anchor='e')
            tk.Label(bottom, text='Private LAN only • Host identity is not verified', bg=BG, fg=ACCENT_DARK, font=(FONT, 9)).pack(anchor='w')
            m.S.ask = self.ask
            r.protocol('WM_DELETE_WINDOW', self.quit)
            r.after(300, self.first_run)

        def diagnose(self):
            import subprocess
            lines = []
            ip = m.local_ip()
            lines.append(f'PC address: {ip}' + ('  (PROBLEM: no network)' if ip.startswith('127.') else ''))
            if not ip.startswith(('192.168.', '10.', '172.')):
                lines.append('  Warning: not a normal private LAN address (VPN or odd adapter?). Phone must be on the same Wi-Fi.')
            lines.append('Sharing: ' + ('ON' if m.S.running else 'OFF (click Start sharing first)'))
            if m.S.running:
                try:
                    s = socket.create_connection((ip, m.PORT), timeout=3); s.close()
                    lines.append(f'Port {m.PORT} reachable on this PC: yes')
                except Exception as e:
                    lines.append(f'Port {m.PORT} reachable on this PC: NO ({type(e).__name__})')
            try:
                out = subprocess.run(['netsh', 'advfirewall', 'firewall', 'show', 'rule', 'name=ScreenBridge'],
                                     capture_output=True, text=True, timeout=10, creationflags=0x08000000).stdout
                has = 'Rule Name' in out
                prof = [l.split(':', 1)[1].strip() for l in out.splitlines() if l.strip().startswith('Profiles:')]
                lines.append('Firewall rule: ' + ('found, profiles ' + ', '.join(prof) if has else 'MISSING'))
                needs_fix = (not has) or any(p.lower() != 'domain,private,public' and p.lower() != 'any' for p in prof)
            except Exception:
                lines.append('Firewall rule: could not check'); needs_fix = True
            try:
                admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
            except Exception:
                admin = False
            lines.append('Running as administrator: ' + ('yes' if admin else 'no (cannot control UAC prompts or admin windows; right-click > Run as administrator)'))
            lines.append('Remote mouse/keyboard allowed: ' + ('yes' if m.S.allow_control else 'NO (tick the checkbox)'))
            lines.append('Approved devices: ' + (', '.join(sorted(m.S.approved)) or 'none yet'))
            text = '\n'.join(lines)
            if needs_fix and messagebox.askyesno('Diagnostics', text + '\n\nRepair firewall rule for ALL networks (Public too)? Windows will ask for administrator permission.', parent=self.root):
                ctypes.windll.shell32.ShellExecuteW(None, 'runas', 'cmd.exe',
                    f'/c netsh advfirewall firewall delete rule name="ScreenBridge" & netsh advfirewall firewall add rule name="ScreenBridge" dir=in action=allow protocol=TCP localport={m.PORT} profile=any', None, 0)
            elif not needs_fix:
                messagebox.showinfo('Diagnostics', text, parent=self.root)

        def copy_details(self):
            self.root.clipboard_clear()
            self.root.clipboard_append(f'ScreenBridge\nIP: {self.addr.cget("text")}\nPort: {m.PORT}\nPIN: {m.S.pin}')
            messagebox.showinfo('Copied', 'Share the PIN only with someone you trust. Host identity is not verified.', parent=self.root)

        def open_phone(self):
            if getattr(self, 'pv', None) and self.pv.win.winfo_exists(): return self.pv.win.lift()
            self.pv = Viewer(self.root)

    return FreshApp()
