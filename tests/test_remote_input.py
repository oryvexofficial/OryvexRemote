"""Test Windows control validation, gating, and release safety with fake Win32."""
import ctypes
import json
import pathlib
import sys
import tempfile
import threading
import types
import unittest
import urllib.error

class FakeWin32:
    def __init__(self):
        self.events = []
        self.fail_after_down = False
        self.down = False
    def GetSystemMetrics(self, axis): return (1920, 1080)[axis]
    def SetCursorPos(self, x, y):
        if self.fail_after_down and self.down: raise OSError("simulated input failure")
        self.events.append(("move", x, y))
    def mouse_event(self, flag, x, y, data, extra):
        self.events.append(("mouse", flag))
        if flag == 2: self.down = True
        if flag == 4: self.down = False
    def keybd_event(self, vk, scan, flags, extra): self.events.append(("key", vk, flags))
    def SendInput(self, *args): return 1

fake = FakeWin32()
ctypes.windll = types.SimpleNamespace(user32=fake, shcore=FakeWin32(), gdi32=FakeWin32(), shell32=FakeWin32())
sys.modules["mss"] = types.ModuleType("mss")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "windows"))
import main
from security import open_lan, server_context

class InputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = main.TLSServer(("127.0.0.1", 0), main.Handler, server_context(cls.temp.name))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        main.S.running = False
        cls.server.shutdown(); cls.server.server_close(); cls.temp.cleanup()

    def setUp(self):
        fake.events.clear()
        fake.fail_after_down = False
        fake.down = False
        main.S.running = True
        main.S.pin = "00123456"
        main.S.allow_control = False
        main.S.approved.clear()
        main.S.ask = lambda ip: True

    def post(self, payload):
        try:
            with open_lan("127.0.0.1", self.server.server_port, "/input", "00123456",
                          data=json.dumps(payload).encode(), timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_control_disabled_by_default_then_enabled(self):
        self.assertEqual(self.post({"t": "move", "x": .5, "y": .5}), 403)
        self.assertEqual(fake.events, [])
        main.S.allow_control = True
        self.assertEqual(self.post({"t": "move", "x": 1, "y": 1}), 200)
        self.assertIn(("move", 1919, 1079), fake.events)
        main.S.allow_control = False
        self.assertEqual(self.post({"t": "move", "x": 0, "y": 0}), 403)

    def test_drag_completes_and_releases_button(self):
        main.S.allow_control = True
        self.assertEqual(self.post({"t":"drag", "x":0, "y":0, "x2":1, "y2":1}), 200)
        self.assertIn(("mouse", 2), fake.events)
        self.assertEqual(fake.events[-1], ("mouse", 4))
        self.assertFalse(fake.down)

    def test_drag_releases_on_failure(self):
        main.S.allow_control = True
        fake.fail_after_down = True
        self.assertEqual(self.post({"t":"drag", "x":0, "y":0, "x2":1, "y2":1}), 400)
        self.assertEqual(fake.events[-1], ("mouse", 4))
        self.assertFalse(fake.down)

    def test_invalid_coordinates_and_shortcuts(self):
        main.S.allow_control = True
        for payload in ({"t":"move","x":1.1,"y":0}, {"t":"move","x":True,"y":0},
                        {"t":"drag","x":0,"y":0,"x2":-1,"y2":0},
                        {"t":"shortcut","k":"not_allowed"}):
            self.assertEqual(self.post(payload), 400)
        self.assertEqual(fake.events, [])
        self.assertEqual(self.post({"t":"shortcut", "k":"copy"}), 200)
        self.assertEqual(fake.events, [("key", 0x11, 0), ("key", 0x43, 0),
                                       ("key", 0x43, 2), ("key", 0x11, 2)])

if __name__ == "__main__":
    unittest.main(verbosity=2)
