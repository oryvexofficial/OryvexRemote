"""LAN-only TLS transport and bounded failed-PIN throttling.

WARNING: this simplified pairing mode does not authenticate the server.
An active network attacker can impersonate it and steal the PIN and screen.
"""
import datetime
import http.client
import ipaddress
import os
import re
import ssl
import threading
import time
import urllib.request
from collections import OrderedDict, deque

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def ensure_identity(directory):
    os.makedirs(directory, exist_ok=True)
    cert_path, key_path = os.path.join(directory, "certificate.pem"), os.path.join(directory, "private-key.pem")
    if not (os.path.exists(cert_path) and os.path.exists(key_path)):
        if os.path.exists(cert_path) or os.path.exists(key_path):
            raise RuntimeError("Incomplete TLS identity; restore both certificate and key or remove both to regenerate.")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ScreenBridge local host")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=3650))
                .sign(key, hashes.SHA256()))
        with open(key_path, "xb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
        try:
            os.chmod(key_path, 0o600)
            with open(cert_path, "xb") as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
        except Exception:
            os.unlink(key_path)
            raise
    return cert_path, key_path


def server_context(directory):
    cert, key = ensure_identity(directory)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    return context


class LanHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        kwargs["context"] = context
        super().__init__(*args, **kwargs)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class LanHandler(urllib.request.HTTPSHandler):
    def __init__(self):
        super().__init__()

    def https_open(self, req):
        def factory(host, **kwargs):
            return LanHTTPSConnection(host, **kwargs)
        return self.do_open(factory, req)


def open_lan(host, port, path, pin, data=None, timeout=10):
    ipaddress.IPv4Address(host)
    if not re.fullmatch(r"[0-9]{8}", pin):
        raise ValueError("PIN must be exactly eight digits")
    if not path.startswith("/") or "\r" in path or "\n" in path:
        raise ValueError("Invalid path")
    req = urllib.request.Request(f"https://{host}:{port}{path}", data=data,
                                 headers={"Authorization": "Bearer " + pin, "Content-Type": "application/json"})
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), LanHandler(), NoRedirect()).open(req, timeout=timeout)


class PinThrottle:
    """At most five failures per IP in 60 seconds, with bounded memory."""
    def __init__(self, limit=5, seconds=60, max_ips=1024, clock=time.monotonic):
        self.limit, self.seconds, self.max_ips, self.clock = limit, seconds, max_ips, clock
        self.entries, self.lock = OrderedDict(), threading.Lock()

    def check(self, ip, correct):
        with self.lock:
            now = self.clock()
            attempts = self.entries.pop(ip, deque())
            while attempts and attempts[0] <= now - self.seconds:
                attempts.popleft()
            blocked = len(attempts) >= self.limit
            if not blocked and not correct:
                attempts.append(now)
            if attempts:
                self.entries[ip] = attempts
            while len(self.entries) > self.max_ips:
                self.entries.popitem(last=False)
            return not blocked
