"""Static project checks only, not an Android compiler or a device test."""
import hashlib
import pathlib
import re
import unittest
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]

class ProjectChecks(unittest.TestCase):
    def test_xml_is_well_formed(self):
        for p in (ROOT / "android").rglob("*.xml"):
            ET.parse(p)

    def test_android_view_ids_exist(self):
        layout = (ROOT / "android/app/src/main/res/layout/activity_main.xml").read_text(encoding="utf-8")
        activity = (ROOT / "android/app/src/main/java/com/screenbridge/app/MainActivity.kt").read_text(encoding="utf-8")
        declared = set(re.findall(r'@\+id/(\w+)', layout))
        referenced = set(re.findall(r'R\.id\.(\w+)', activity))
        self.assertEqual(referenced - declared, set())

    def test_no_active_cleartext_clients(self):
        for path in [ROOT / "windows/main.py", ROOT / "windows/design.py",
                     ROOT / "android/app/src/main/java/com/screenbridge/app/MainActivity.kt"]:
            self.assertNotIn("http://", path.read_text(encoding="utf-8"))
        manifest = (ROOT / "android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
        self.assertIn('android:usesCleartextTraffic="false"', manifest)

    def test_font_download_and_license(self):
        a = ROOT / "windows/Manrope.ttf"
        b = ROOT / "android/app/src/main/res/font/manrope.ttf"
        digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        self.assertEqual(digest(a), "3ae11c49db0455a3cc33e37d380f20fdb8c7f8b41dc07625c177e3d87a9d6ae6")
        self.assertEqual(digest(a), digest(b))
        self.assertIn("SIL OPEN FONT LICENSE", (ROOT / "licenses/Manrope-OFL.txt").read_text(encoding="utf-8"))

    def test_android_security_wiring_exists(self):
        d = ROOT / "android/app/src/main/java/com/screenbridge/app"
        client, host, tls = ((d / f).read_text(encoding="utf-8") for f in ("MainActivity.kt", "ShareService.kt", "TlsIdentity.kt"))
        self.assertIn('URL("https://', client)
        self.assertIn('setRequestProperty("Authorization", "Bearer $pin")', client)
        self.assertIn("TlsIdentity.pinnedClient(fingerprint)", client)
        self.assertIn("MessageDigest.isEqual(actual, expectedBytes)", tls)
        self.assertIn('Regex("[0-9a-f]{64}")', tls)
        self.assertIn("TlsIdentity.host()", host)
        self.assertIn("q.size >= 5", host)

if __name__ == "__main__":
    unittest.main(verbosity=2)
