"""ScreenBridge desktop: a clear, consent-first LAN sharing workspace."""
import ctypes
import io
import ipaddress
import json
import os
import secrets
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog
from urllib import error, request
from PIL import Image, ImageTk
from security import open_lan

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
            tk.Button(tool, text='Type', command=self.type_text, bg=PAPER, fg=INK, relief='flat', padx=10, pady=5, cursor='hand2').pack(side='left', padx=3)
            tk.Button(tool, text='Screenshot', command=self.shot, bg=PAPER, fg=INK, relief='flat', padx=10, pady=5, cursor='hand2').pack(side='left', padx=3)
            tk.Button(tool, text='Disconnect', command=self.disconnect, bg=PAPER, fg=ACCENT_DARK, relief='flat', padx=10, pady=5, cursor='hand2').pack(side='left', padx=3)
            self.canvas = tk.Canvas(self.win, bg=DARK, highlightthickness=0, cursor='crosshair'); self.canvas.pack(fill='both', expand=True, padx=24, pady=(0, 24))
            self.canvas.bind('<Configure>', lambda e: self.show())
            self.canvas.bind('<Button-1>', lambda e: self.pointer('click', e))
            self.canvas.bind('<Double-Button-1>', lambda e: self.pointer('dbl', e))
            self.canvas.bind('<Button-3>', lambda e: self.pointer('rclick', e))
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
                if port == 8765:
                    with open_lan(host, port, '/ping', pin, timeout=70) as r:
                        info = json.loads(r.read(8192))
                    if gen != self.gen: return
                    self.control_allowed = bool(info.get('control', False))
                conn = open_lan(host, port, '/stream', pin, timeout=15)
                if gen != self.gen:
                    conn.close()
                    return
                self.connection = conn
                self.say('Connected. Host identity is not verified.')
                while gen == self.gen:
                    length = 0
                    while gen == self.gen:
                        line = conn.readline(8192)
                        if not line: raise EOFError()
                        if line.lower().startswith(b'content-length:'):
                            length = int(line.split(b':', 1)[1].strip())
                        if not line.strip() and length: break
                    if length < 1 or length > 12000000: raise ValueError('Invalid frame')
                    data = conn.read(length)
                    if len(data) != length: raise EOFError()
                    image = Image.open(io.BytesIO(data)); image.load()
                    if gen != self.gen: break
                    self.latest = image
                    if not self.pending:
                        self.pending = True
                        self.win.after(0, self.draw_pending)
            except error.HTTPError as e:
                if gen == self.gen: self.say('Wrong PIN or device denied access.' if e.code in (401, 403) else f'Connection failed (HTTP {e.code}).')
            except Exception:
                if gen == self.gen: self.say('Connection failed. Check sharing, Wi-Fi and address.')
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
            if not self.control_allowed:
                return self.say('Control is available only when enabled on a connected Windows PC.')
            self.control = not self.control
            self.control_btn.config(text='Control: on' if self.control else 'Control: off')

        def pointer(self, kind, event):
            if not self.control or not hasattr(self, 'image_box'): return
            x, y, w, h = self.image_box
            if not (x <= event.x <= x+w and y <= event.y <= y+h): return
            self.send({'t': kind, 'x': (event.x-x)/w, 'y': (event.y-y)/h})

        def send(self, payload):
            if not self.control or not self.control_allowed: return
            host, pin, port = self.host, self.session_pin, self.port
            def post():
                try:
                    with open_lan(host, port, '/input', pin, data=json.dumps(payload).encode(), timeout=3): pass
                except Exception: self.say('Could not send control input.')
            threading.Thread(target=post, daemon=True).start()

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

        def copy_details(self):
            self.root.clipboard_clear()
            self.root.clipboard_append(f'ScreenBridge\nIP: {self.addr.cget("text")}\nPort: {m.PORT}\nPIN: {m.S.pin}')
            messagebox.showinfo('Copied', 'Share the PIN only with someone you trust. Host identity is not verified.', parent=self.root)

        def open_phone(self):
            if getattr(self, 'pv', None) and self.pv.win.winfo_exists(): return self.pv.win.lift()
            self.pv = Viewer(self.root)

    return FreshApp()
