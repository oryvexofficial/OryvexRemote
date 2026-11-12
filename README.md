# ScreenBridge 2.1 development source

**Prototype, not a secure or production-ready product.** At the user's request this version removes the certificate fingerprint from pairing and no longer verifies the host identity. TLS encrypts traffic only against passive listeners: an active attacker on the LAN can impersonate the host, steal the PIN, and intercept the screen or remote input. Use only on a trusted private network, never public Wi-Fi or the internet. Version 2.0 was unencrypted HTTP and is also insecure. Update both devices to this version; older versions are not compatible.

No Windows EXE, installer or Android APK is included in this ZIP. An earlier Android revision built in GitHub Actions, but this updated revision has not been compiled or tested on real devices. The Windows test encoding issue from the supplied logs is fixed, but this workflow has not been rerun. Do not rely on this prototype for sensitive content until builds, all four device pairings, and security behavior have been independently tested.

## This update: control and phone-to-PC

- The Android viewer now rechecks the Windows host's current control permission when Control is tapped. On the PC check **Allow remote mouse and keyboard**, then on Android tap **Control: OFF**. There is no need to reconnect just because the checkbox changed. Control still starts **off** on the host and viewer.
- When viewing a Windows host on Android, move a finger to move the Windows cursor; tap, double-tap, or long-press to left-click, double-click, or right-click; use two fingers to scroll. Tap **Drag: OFF** to enable drag mode, touch the starting point, slide to the end and lift. The host performs a short straight-line drag after release; it is not live freehand dragging. Turn drag mode off for normal pointer use. The Shortcuts menu provides Copy, Paste, Cut, Select All, Undo, Alt+Tab and Windows+D. Type opens a text dialog. The toolbar scrolls horizontally. Actual responsiveness depends on network and devices.
- The Windows viewer supports pointer movement, click, double-click, right-click, scrolling, text entry and a similar straight-line drag mode. Pointer rendering is overlaid on Windows screen frames as a simple arrow, not an exact representation of the native cursor shape.
- For Android-to-Windows viewing, the Windows viewer now requests `/ping` first, reports common connection errors, and bounds MJPEG parsing. The Android host has an adjusted Android Keystore TLS configuration and key manager, sends stream headers promptly, repeats static frames as keepalives, and reports some startup failures on the phone. These are **attempted compatibility fixes**, not a confirmed diagnosis: the original device error was generic, and the changes have not been tested on real devices. Android sharing remains view-only.

## 2.2 changes: two-way control

- **Phone controls Windows** (already there, now faster): HTTP keep-alive removes a TLS handshake per mouse move. Windows firewall rule now covers Public networks too; use **Diagnose / repair connection** in the PC app.
- **Windows controls the phone** (new): on the phone tap **Remote control: OFF**, enable the *ScreenBridge remote control* accessibility service in Settings, come back and tap again (needs phone sharing running). Click Control in the Windows viewer. Click = tap, right-click = long press, drag = swipe, wheel = scroll, Back/Home/Recents and Type buttons.
- Phone-to-phone control uses the same path (Android host, Android viewer).
- Limits: Android blocks input on the lock screen, some banking apps and secure screens. Windows cannot be controlled on UAC/admin windows unless ScreenBridge runs as administrator. Not compiled or tested on devices here.

## Supported paths in the source

| Sharing host | Viewer | Intended behavior, not yet device-verified |
|---|---|---|
| Windows | Windows or Android | View and screenshot; optional mouse/keyboard control |
| Android | Windows or Android | View and screenshot only |

Windows control starts off and must be explicitly enabled on the host and viewer. Windows asks for approval on the first valid connection from each IP. Android uses the OS screen-capture consent and a foreground stop notification, but has **no separate approval prompt for individual viewers**.

## Pairing and use

1. Put both devices on the same trusted private LAN. Start the updated app on each device.
2. On the sharing host, start sharing. Android requires the system capture consent; Windows uses port 8765, Android port 8766.
3. The host displays its current local IPv4 address and an eight-digit PIN. The devices must be on the same LAN, usually connected to the same router or hotspot, without client isolation.
4. On the viewer choose the host type, enter the host IP and PIN, then connect. Approve the request on the Windows host. Android has no per-viewer approval. This simple pairing cannot detect an impersonated host.
5. On Windows, use **New PIN** or **Revoke all** to invalidate existing streams; **Stop sharing** ends sharing. On Android, stop using the app or persistent notification; restarting sharing generates a new PIN.
6. Control Windows only when the host explicitly permits it: enable **Allow remote mouse and keyboard** on the PC, then tap **Control** on the viewer. Tap it again to turn it off. If the host disables control, input is rejected. Android sharing is view-only.

The PIN is a secret. Copying host details puts it on the clipboard: clear it afterward and send it only through a trusted channel. A PIN can also be visible on a shared screen. Screenshots are saved under Pictures/ScreenBridge.

### Keep the IP fixed

The displayed address is the address assigned by your network, not a guaranteed permanent address. On Windows use **Refresh IP** if the network changes; on Android return to the app and check the displayed address. To keep it fixed, open your router's settings and create a DHCP reservation for each device's Wi-Fi or Ethernet MAC address, assigning an unused IP in the correct subnet. Reconnect and check the displayed addresses match the router. Android may use a randomized Wi-Fi MAC address: reserve the MAC actually used for that network and avoid changing its randomization setting afterward. Router menus vary. The app cannot force your router to assign a fixed address. If you use a different router or hotspot, addresses can change. VPNs, multiple adapters, guest networks, and wireless client isolation can make the shown IP unreachable.

## Security details and limits

- TLS uses self-signed certificates, but viewers accept any server certificate and hostname. There is **no server authentication** and no protection against an active man-in-the-middle. An attacker impersonating the host can read the PIN and screen or alter input, even though the connection uses HTTPS. No cleartext fallback exists.
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

Push to `main` or manually run **Actions > Build ScreenBridge > Run workflow** on `main`. After both build jobs succeed, the workflow automatically creates a unique tag `v0.RUN_NUMBER.RUN_ATTEMPT` pointing to that commit and publishes a normal GitHub **release**, not a prerelease, with the Windows EXE, installer and Android debug APK. Pull requests build but never publish. Manual runs on other branches do not publish. Failed builds do not release. Re-running creates another unique release tag. Enable Actions and allow workflow contents write permissions in repository settings. Confirm the run and all release assets exist; this changed workflow has not yet been run here. Releases are public for public repositories. The Windows files are **unsigned** and the Android APK uses a debug key; neither is a production-signed release. The earlier Android debug artifact is at https://github.com/oryvexofficial/OryvexRemote/actions/runs/43277386041/artifacts/13970582683 and does not contain this update.

To update an existing checkout, copy this ZIP's project files into the checkout, preserving the existing `.git` directory, then:

```powershell
git status
git add .
git commit -m "Improve remote control and phone sharing"
git push
```

Review `git status` before adding files. For a new empty repository, run `git init`, configure `git config user.name "oryvexofficial"` and `git config user.email "oryvex_official@proton.me"`, commit the files, set the `main` branch and add the remote `https://github.com/oryvexofficial/OryvexRemote.git` before pushing. GitHub authentication is separate from the commit author settings.

## Download warning and verification

The supplied ZIP contains source only, not the executable that triggered the warning. Its actual cause cannot be determined or scanned from this archive. PyInstaller one-file packaging, an unsigned executable or installer, low reputation, the app's screen capture/input features, and admin/firewall changes are possible causes, not diagnoses.

For the **actual** generated EXE and installer, record the file hashes with `Get-FileHash`, inspect signatures with `Get-AuthenticodeSignature`, scan the files using current Microsoft Defender and your organization's approved scanners, and capture the exact browser/SmartScreen warning and its publisher/reputation details. Do not bypass the warning. Review any detection with the vendor and fix genuine findings. Signing with a legitimate trusted code-signing certificate can help establish publisher identity and reputation, but does not guarantee a warning disappears. No antivirus or reputation result has been verified here.

## Testing performed and remaining

Run `python tests/test_tls_integration.py`, `python tests/test_remote_input.py`, `python tests/test_phone_stream_mock.py`, and `python tests/test_project_static.py` from the project root. Tests use mocked Windows input, real loopback TLS and a simulated phone host; they exercise PIN, approval, input gating, drag button release, malformed inputs, simulated phone JPEG stream parsing, lockout and revocation. One test demonstrates that an impersonating TLS server receives the PIN. The checks do not compile Android or test actual Android Keystore, screen capture, physical input, UI, performance or real cross-device behavior.

Before release, test Windows-to-Windows, Windows-to-Android, Android-to-Windows and Android-to-Android on real devices. For each, check frame display, wrong PIN and lockout, stop/revoke, network interruption and reconnect, orientation, permissions and long-running sessions. Specifically verify Android host TLS handshakes on each Android version and manufacturer. Verify Windows remote input only after explicit host and viewer opt-in.

## Troubleshooting

- **Cannot connect:** check the host is sharing, target type, IPv4 address, private-network firewall, same LAN, Wi-Fi client isolation and ports 8765/8766. Automatic IP selection can be wrong with VPNs or multiple adapters.
- **TLS error:** confirm both devices run this updated version. The self-signed certificate is not trusted by a normal browser. Simplified app pairing deliberately does not verify identity.
- **Too many attempts:** wait at least 60 seconds and retry the correct PIN. Some networks share an IP across clients.
- **Android sharing stops or is blank:** check that the Android app says Sharing, the OS capture permission was accepted, and the foreground notification remains present. On Windows select Android and use the phone's displayed IP and PIN, not the PC's. Check TCP 8766, phone battery restrictions and protected content. Some Android versions block capture of protected windows. Rotation may require restarting sharing. If a startup error is shown on the phone, record the error class without sharing the PIN. With Android platform-tools and USB debugging enabled, use `adb logcat -s ScreenBridgeShare` for server startup or connection errors. Review logs for private information before sharing them.
- **Control unavailable:** it only works for Windows hosts and must be enabled on both the host and viewer. In the updated app, tap Control again after enabling the PC checkbox to recheck permission. Scroll the Android toolbar to see drag, shortcuts and other controls. Some elevated Windows windows, secure desktop, and UAC prompts cannot be controlled by a non-elevated app.

## What is missing

This is not an AnyDesk replacement: no relay/NAT traversal, internet connection broker, accounts, unattended access, mobile remote control, file transfer, audio, clipboard synchronization, robust identity management, full physical-keyboard support, live/freehand drag or multi-monitor selection. Copy and Paste shortcuts act on the Windows host's clipboard; they do not synchronize clipboards between devices. Windows cursor rendering is approximate. Capture is MJPEG and can consume substantial CPU and bandwidth. Windows captures the primary monitor; Android capture is limited to a scaled display and may not adapt to rotation.

## Fonts

Manrope was downloaded from the [official Google Fonts repository](https://github.com/google/fonts/tree/main/ofl/manrope). The original variable TTF is bundled for Android and retained in `windows/Manrope.ttf`; static Regular/Bold Windows instances were generated from it using fontTools. The upstream file SHA-256 is `3ae11c49db0455a3cc33e37d380f20fdb8c7f8b41dc07625c177e3d87a9d6ae6`. See `licenses/Manrope-OFL.txt` for the SIL Open Font License. DejaVu files retained from the original source are no longer used by the updated UI; their original license remains in `licenses/`.
