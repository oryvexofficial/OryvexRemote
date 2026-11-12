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
from security import PinThrottle, ensure_identity, open_lan, server_context

class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        context = server_context(cls.temp.name)
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

    def request(self, path, pin="00123456", data=None):
        return open_lan("127.0.0.1", self.port, path, pin, data=data, timeout=4)

    def code(self, path, pin="00123456", data=None):
        try:
            with self.request(path, pin, data=data) as response:
                return response.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_identity_persists(self):
        cert, key = ensure_identity(self.temp.name)
        before = pathlib.Path(cert).read_bytes(), pathlib.Path(key).read_bytes()
        cert_again, key_again = ensure_identity(self.temp.name)
        self.assertEqual(before, (pathlib.Path(cert_again).read_bytes(), pathlib.Path(key_again).read_bytes()))
        with self.request("/ping") as response:
            self.assertEqual(response.status, 200)

    def test_untrusted_server_is_accepted_and_receives_pin(self):
        """Demonstrates the intentional MITM vulnerability of simplified pairing."""
        from http.server import BaseHTTPRequestHandler
        seen = []
        class Impostor(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
        with tempfile.TemporaryDirectory() as d:
            ctx = server_context(d)
            self.assertNotEqual(pathlib.Path(ensure_identity(d)[0]).read_bytes(),
                                pathlib.Path(ensure_identity(self.temp.name)[0]).read_bytes())
            server = main.TLSServer(("127.0.0.1", 0), Impostor, ctx)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with open_lan("127.0.0.1", server.server_port, "/ping", "00123456", timeout=4) as response:
                    self.assertEqual(response.status, 200)
                self.assertEqual(seen, ["Bearer 00123456"])
            finally:
                server.shutdown()
                server.server_close()

    @unittest.skipUnless(shutil.which("openssl"), "openssl is not available")
    def test_independent_openssl_certificate_hash(self):
        cert, _ = ensure_identity(self.temp.name)
        result = subprocess.run(["openssl", "x509", "-in", cert, "-noout", "-fingerprint", "-sha256"],
                                check=True, text=True, capture_output=True)
        self.assertEqual(len(result.stdout.strip().split("=", 1)[1].replace(":", "")), 64)

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
