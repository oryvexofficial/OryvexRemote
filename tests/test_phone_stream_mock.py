"""Exercise the real desktop transport/parser against a simulated phone host."""
import io
import json
import pathlib
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "windows"))
from mjpeg import read_frame
from security import open_lan, server_context

class PhoneHost(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer 00123456":
            self.send_error(401); return
        if self.path == "/ping":
            body = b'{"control":false}'
            self.send_response(200); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        if self.path != "/stream":
            self.send_error(404); return
        jpg = io.BytesIO()
        Image.new("RGB", (4, 6), (40, 80, 100)).save(jpg, "JPEG")
        frame = jpg.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        for _ in range(2):
            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(frame))
            self.wfile.write(frame + b"\r\n")

class ViewerTests(unittest.TestCase):
    def test_phone_mock_ping_and_jpeg_stream(self):
        with tempfile.TemporaryDirectory() as d:
            server = ThreadingHTTPServer(("127.0.0.1", 0), PhoneHost)
            server.socket = server_context(d).wrap_socket(server.socket, server_side=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with open_lan("127.0.0.1", server.server_port, "/ping", "00123456", timeout=3) as r:
                    self.assertFalse(json.loads(r.read())["control"])
                with open_lan("127.0.0.1", server.server_port, "/stream", "00123456", timeout=3) as r:
                    for _ in range(2):
                        image = Image.open(io.BytesIO(read_frame(r)))
                        image.load()
                        self.assertEqual(image.size, (4, 6))
            finally:
                server.shutdown(); server.server_close()

    def test_rejects_invalid_or_partial_frames(self):
        for data in (b"--frame\r\nContent-Length: 12000001\r\n\r\n",
                     b"--frame\r\nContent-Length: 10\r\n\r\nabc"):
            with self.assertRaises((ValueError, EOFError)): read_frame(io.BytesIO(data))

if __name__ == "__main__":
    unittest.main(verbosity=2)
