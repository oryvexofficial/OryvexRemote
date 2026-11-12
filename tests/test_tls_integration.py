"""Loopback integration tests. No Windows screen or Android device is required."""
import ctypes
import json
import pathlib
import ssl
import subprocess
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
from http.server import ThreadingHTTPServer

class Win32:
    def __getattr__(self, name):
        return lambda *args: 100

ctypes.windll = types.SimpleNamespace(user32=Win32(), shcore=Win32(), gdi32=Win32(), shell32=Win32())
sys.modules["mss"] = types.ModuleType("mss")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "windows"))
import main
from security import PinThrottle, ensure_identity, format_fingerprint, normalize_fingerprint, open_pinned, server_context

class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        context, cls.fp = server_context(cls.temp.name)
        cls.srv = main.TLSServer(("127.0.0.1", 0), main.Handler, context)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_port

    @classmethod
    def tearDownClass(cls):
        main.S.running = False
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.temp.cleanup()

    def setUp(self):
        main.S.running = True
        main.S.pin = "00123456"
        main.S.approved.clear()
        main.S.generation = 0
        main.S.allow_control = False
        main.S.throttle = PinThrottle()
        main.S.ask = lambda ip: True
        self.inputs = []
        main.handle_input = lambda data: self.inputs.append(data)

    def request(self, path, pin="00123456", fp=None, data=None):
        return open_pinned("127.0.0.1", self.port, path, pin, fp or self.fp, data=data, timeout=4)

    def code(self, path, pin="00123456", data=None):
        try:
            with self.request(path, pin, data=data) as response:
                return response.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_identity_persists_and_exact_fingerprint(self):
        _, _, again = ensure_identity(self.temp.name)
        self.assertEqual(again, self.fp)
        self.assertEqual(normalize_fingerprint(format_fingerprint(self.fp)), self.fp.lower())
        with self.assertRaises(ValueError): normalize_fingerprint(self.fp[:32])
        altered = ("0" if self.fp[0] != "0" else "1") + self.fp[1:]
        with self.assertRaises((urllib.error.URLError, ssl.SSLError)):
            self.request("/ping", fp=altered)
        self.assertEqual(main.S.approved, set(), "Mismatch must fail before HTTP authentication")

    @unittest.skipUnless(shutil.which("openssl"), "openssl is not available")
    def test_independent_openssl_certificate_hash(self):
        cert, _, _ = ensure_identity(self.temp.name)
        result = subprocess.run(["openssl", "x509", "-in", cert, "-noout", "-fingerprint", "-sha256"],
                                check=True, text=True, capture_output=True)
        self.assertEqual(normalize_fingerprint(result.stdout.strip().split("=", 1)[1]), self.fp.lower())

    def test_auth_control_and_body_limit(self):
        self.assertEqual(self.code("/ping", "00000000"), 401)
        with self.request("/ping") as r:
            self.assertFalse(json.loads(r.read())["control"])
        self.assertEqual(self.code("/input", data=b'{"t":"click"}'), 403)
        main.S.allow_control = True
        self.assertEqual(self.code("/input", data=b'{"t":"click"}'), 200)
        self.assertEqual(self.inputs[-1]["t"], "click")
        self.assertEqual(self.code("/input", data=b"x" * 8193), 413)
        self.assertEqual(self.code("/stream?q=bad"), 400)
        self.assertEqual(self.code("/unknown"), 404)
        main.S.running = False
        self.assertEqual(self.code("/ping"), 503)

    def test_pin_throttle(self):
        for _ in range(5):
            self.assertEqual(self.code("/ping", "00000000"), 401)
        self.assertEqual(self.code("/ping"), 429)
        now = [0]
        t = PinThrottle(clock=lambda: now[0])
        for _ in range(5): self.assertTrue(t.check("ip", False))
        self.assertFalse(t.check("ip", True))
        now[0] = 60
        self.assertTrue(t.check("ip", True))

    def test_approval_denial(self):
        main.S.ask = lambda ip: False
        self.assertEqual(self.code("/ping"), 403)
        self.assertFalse(main.S.approved)

    def test_stream_revocation(self):
        def fake_frames(q, fps, width):
            while main.S.running:
                yield b"fakejpeg"
                time.sleep(.02)
        original = main.frames
        main.frames = fake_frames
        try:
            with self.request("/stream") as r:
                self.assertTrue(r.readline().startswith(b"--frame"))
                main.S.generation += 1
                main.S.approved.clear()
                self.assertIn(b"fakejpeg", r.read())
        finally:
            main.frames = original

if __name__ == "__main__":
    unittest.main(verbosity=2)
