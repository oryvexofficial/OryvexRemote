# ScreenBridge 2.1 development source

**Prototype, not a production security product.** This source recreates the intended 2.1 changes on top of the supplied 2.0 project. Version 2.0 used unencrypted HTTP and must not be considered secure. The new protocol requires TLS and a manually verified full SHA-256 certificate fingerprint; it is not compatible with 2.0. Update every device.

No Windows EXE, installer or Android APK is included: this environment had neither Windows build tooling nor an Android SDK/Gradle. The source has not been compiled or tested on those platforms. Do not rely on it for sensitive content until the builds, all four device pairings, and the security behavior have been independently tested.

## Supported paths in the source

| Sharing host | Viewer | Intended behavior, not yet device-verified |
|---|---|---|
| Windows | Windows or Android | View and screenshot; optional mouse/keyboard control |
| Android | Windows or Android | View and screenshot only |

Windows control starts off and must be explicitly enabled on the host and viewer. Windows asks for approval on the first valid connection from each IP. Android uses the OS screen-capture consent and a foreground stop notification, but has **no separate approval prompt for individual viewers**.

## Pairing and use

1. Put both devices on the same trusted private LAN. Start the updated app on each device.
2. On the sharing host, start sharing. Android requires the system capture consent; Windows uses port 8765, Android port 8766.
3. The host displays its IPv4 address, an eight-digit PIN, and a SHA-256 fingerprint. Independently compare **all 64 hex digits** with the host through a trusted channel, ideally in person. Colons and spaces are optional. Do not rely on a fingerprint delivered over the same untrusted channel an attacker could alter.
4. On the viewer choose the host type, enter the IP, PIN and complete verified fingerprint, then connect. Approve the request on the Windows host. Android has no per-viewer approval. A fingerprint mismatch rejects the connection before the PIN is sent.
5. On Windows, use **New PIN** or **Revoke all** to invalidate existing streams; **Stop sharing** ends sharing. On Android, stop using the app or persistent notification; restarting sharing generates a new PIN.
6. Control Windows only when the host explicitly permits it, then switch on Control in the viewer. Disable it again when finished. Android sharing is view-only.

The fingerprint is public, but the PIN is a secret. Copying host details puts the PIN on the clipboard: clear it afterward and send it only through a trusted channel. A PIN can also be visible on a shared screen. Screenshots are saved under Pictures/ScreenBridge.

## Security details and limits

- TLS encrypts app traffic between the two devices. Each host has a self-signed identity: Windows stores a private key and certificate in `%APPDATA%\ScreenBridge\identity`; Android uses Android Keystore. Each viewer checks the SHA-256 hash of the complete DER host certificate on every connection before sending the PIN, including stream and input requests. No automatic trust-on-first-use or cleartext fallback exists.
- Reinstalling Android, deleting its app data, or replacing the Windows identity changes the fingerprint. Stop and independently re-verify a changed fingerprint; never blindly accept it. The pinning scheme trusts the exact certificate rather than a public CA, hostname, expiry or revocation service.
- PINs are randomly generated and eight digits. Five failed attempts from one IP block that IP for 60 seconds; attempts are held in memory. This is basic rate limiting, not protection against distributed guessing, denial of service, or local compromise. Restarting a host clears the counters.
- Windows approval is per IP for the current sharing session, **not a cryptographic viewer identity**. Multiple devices behind one address can share that approval. Android viewers need only the PIN and have no individual approval prompt. Stop sharing if the PIN is exposed.
- Keep Windows account and identity files private. A local attacker who steals a host private key can impersonate that host. TLS does not protect a compromised endpoint or guarantee that capture excludes sensitive information.
- Use a trusted LAN only. No internet relay, public exposure, port forwarding, unattended access or NAT traversal is provided. This implementation has not received an independent security audit or real-device interoperability tests.

## Build

### Windows

On Windows, install Python 3.10+ and dependencies:

```powershell
py -m pip install -r windows/requirements.txt
py windows/main.py
```

For a local EXE, install PyInstaller and run the command in `.github/workflows/build.yml`. It includes both static Manrope weights and the icon; `design.py` and `security.py` are imported normally. Run Inno Setup on `windows/installer.iss` to build the installer. The inherited installer requires admin rights and adds a private-profile inbound firewall rule for TCP 8765, which it removes on uninstall. Review that behavior before installing.

### Android

Install Android Studio, JDK 17 and Android SDK 34. Open `android/`, sync the project, and build/run on Android 10 or later. The workflow uses Gradle 8.7:

```sh
gradle -p android assembleDebug --no-daemon
```

Internet is needed for Gradle and dependencies. The debug APK is signed with a development key, not a release identity; do not distribute it as a trusted production release. Create a release signing key and distribution process separately. Test screen capture, Android Keystore TLS, notification permission, capture stop and rotation on real devices.

### GitHub Actions

Push the project to your repository and choose **Actions > Build ScreenBridge > Run workflow**. The workflow attempts a Windows PyInstaller EXE and Inno Setup installer, plus an Android debug APK. Download the artifacts only after confirming the run succeeds. Tagging `v*` also triggers the inherited release job and requires write permission. The Windows artifacts are **unsigned** unless you add a separate signing process.

## Download warning and verification

The supplied ZIP contains source only, not the executable that triggered the warning. Its actual cause cannot be determined or scanned from this archive. PyInstaller one-file packaging, an unsigned executable or installer, low reputation, the app's screen capture/input features, and admin/firewall changes are possible causes, not diagnoses.

For the **actual** generated EXE and installer, record the file hashes with `Get-FileHash`, inspect signatures with `Get-AuthenticodeSignature`, scan the files using current Microsoft Defender and your organization's approved scanners, and capture the exact browser/SmartScreen warning and its publisher/reputation details. Do not bypass the warning. Review any detection with the vendor and fix genuine findings. Signing with a legitimate trusted code-signing certificate can help establish publisher identity and reputation, but does not guarantee a warning disappears. No antivirus or reputation result has been verified here.

## Testing performed and remaining

Run `python tests/test_tls_integration.py` and `python tests/test_project_static.py` from the project root. The first uses mocked Windows screen/input calls and real loopback TLS to exercise certificate mismatch, PIN, lockout, approval, input gating, stream revocation and shutdown. The second checks Android XML/resources, security wiring and font hashes statically. These do **not** compile Android or test actual capture, real input, Android Keystore, UI, accessibility, performance or cross-platform behavior.

Before release, test Windows-to-Windows, Windows-to-Android, Android-to-Windows and Android-to-Android on real devices. For each, check frame display, wrong/full/changed fingerprint, wrong PIN and lockout, stop/revoke, network interruption and reconnect, orientation, permissions and long-running sessions. Specifically verify Android host TLS handshakes on each Android version and manufacturer. Verify Windows remote input only after explicit host and viewer opt-in.

## Troubleshooting

- **Cannot connect:** check the host is sharing, target type, IPv4 address, private-network firewall, same LAN, Wi-Fi client isolation and ports 8765/8766. Automatic IP selection can be wrong with VPNs or multiple adapters.
- **Fingerprint or TLS error:** recheck every hex digit independently. A changed identity is a security event until explained. The self-signed identity will not be trusted by a normal browser; do not disable checks.
- **Too many attempts:** wait at least 60 seconds and retry the correct PIN. Some networks share an IP across clients.
- **Android sharing stops or is blank:** check capture permission, foreground notification, OS battery restrictions and protected content. Some Android versions block capture of protected windows. Rotation may require restarting sharing.
- **Control unavailable:** it only works for Windows hosts and must be enabled on the host and viewer; not all keyboard shortcuts or dragging are implemented.

## What is missing

This is not an AnyDesk replacement: no relay/NAT traversal, internet connection broker, accounts, unattended access, mobile remote control, file transfer, audio, clipboard synchronization, robust identity management, full keyboard/drag support or multi-monitor selection. Capture is MJPEG and can consume substantial CPU and bandwidth. Windows captures the primary monitor; Android capture is limited to a scaled display and may not adapt to rotation.

## Fonts

Manrope was downloaded from the [official Google Fonts repository](https://github.com/google/fonts/tree/main/ofl/manrope). The original variable TTF is bundled for Android and retained in `windows/Manrope.ttf`; static Regular/Bold Windows instances were generated from it using fontTools. The upstream file SHA-256 is `3ae11c49db0455a3cc33e37d380f20fdb8c7f8b41dc07625c177e3d87a9d6ae6`. See `licenses/Manrope-OFL.txt` for the SIL Open Font License. DejaVu files retained from the original source are no longer used by the updated UI; their original license remains in `licenses/`.
