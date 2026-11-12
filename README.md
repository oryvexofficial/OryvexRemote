# ScreenBridge 2.1 development source

**Prototype, not a secure or production-ready product.** At the user's request this version removes the certificate fingerprint from pairing and no longer verifies the host identity. TLS encrypts traffic only against passive listeners: an active attacker on the LAN can impersonate the host, steal the PIN, and intercept the screen or remote input. Use only on a trusted private network, never public Wi-Fi or the internet. Version 2.0 was unencrypted HTTP and is also insecure. Update both devices to this version; older versions are not compatible.

No Windows EXE, installer or Android APK is included in this ZIP. The supplied GitHub Actions logs show an Android debug APK built and uploaded successfully for the previous source revision. The Windows job passed TLS checks but stopped before building when two static tests used Windows' default text encoding; this ZIP fixes those reads to use UTF-8. The corrected Windows workflow has not been rerun, so its EXE and installer remain unverified. Neither app has been tested on real devices. Do not rely on this prototype for sensitive content until builds, all four device pairings, and security behavior have been independently tested.

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
6. Control Windows only when the host explicitly permits it, then switch on Control in the viewer. Disable it again when finished. Android sharing is view-only.

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

Push to `main` or manually run **Actions > Build ScreenBridge > Run workflow** on `main`. After both build jobs succeed, the workflow automatically creates a unique tag `v0.0.0-auto.RUN_NUMBER.RUN_ATTEMPT` pointing to that commit and publishes a GitHub **prerelease** with the Windows EXE, installer and Android debug APK. Pull requests build but never publish. Manual runs on other branches do not publish. Failed builds do not release. Re-running creates another unique prerelease tag. Enable Actions and allow workflow contents write permissions in repository settings. Confirm the run and all release assets exist; this changed workflow has not yet been run here. Releases are public for public repositories. The Windows files are **unsigned** and the Android APK uses a debug key; neither is a production-signed release. The earlier Android debug artifact is at https://github.com/oryvexofficial/OryvexRemote/actions/runs/43277386041/artifacts/13970582683 and does not contain this update.

To update an existing checkout, copy this ZIP's project files into the checkout, preserving the existing `.git` directory, then:

```powershell
git status
git add .
git commit -m "Simplify LAN pairing and automate releases"
git push
```

Review `git status` before adding files. For a new empty repository, run `git init`, configure `git config user.name "oryvexofficial"` and `git config user.email "oryvex_official@proton.me"`, commit the files, set the `main` branch and add the remote `https://github.com/oryvexofficial/OryvexRemote.git` before pushing. GitHub authentication is separate from the commit author settings.

## Download warning and verification

The supplied ZIP contains source only, not the executable that triggered the warning. Its actual cause cannot be determined or scanned from this archive. PyInstaller one-file packaging, an unsigned executable or installer, low reputation, the app's screen capture/input features, and admin/firewall changes are possible causes, not diagnoses.

For the **actual** generated EXE and installer, record the file hashes with `Get-FileHash`, inspect signatures with `Get-AuthenticodeSignature`, scan the files using current Microsoft Defender and your organization's approved scanners, and capture the exact browser/SmartScreen warning and its publisher/reputation details. Do not bypass the warning. Review any detection with the vendor and fix genuine findings. Signing with a legitimate trusted code-signing certificate can help establish publisher identity and reputation, but does not guarantee a warning disappears. No antivirus or reputation result has been verified here.

## Testing performed and remaining

Run `python tests/test_tls_integration.py` and `python tests/test_project_static.py` from the project root. The first uses mocked Windows screen/input calls and real loopback TLS to exercise PIN, lockout, approval, input gating, stream revocation and shutdown. It also demonstrates that an impersonating TLS server is accepted and receives the PIN. The second checks Android XML/resources, security wiring and font hashes statically. The encoding fix was tested with a simulated Windows cp1252 default, not on a Windows runner. GitHub's earlier Android debug build compiled successfully before these latest changes but has not been device-tested. None of these checks test actual capture, real input, Android Keystore, UI, accessibility, performance or cross-platform behavior.

Before release, test Windows-to-Windows, Windows-to-Android, Android-to-Windows and Android-to-Android on real devices. For each, check frame display, wrong PIN and lockout, stop/revoke, network interruption and reconnect, orientation, permissions and long-running sessions. Specifically verify Android host TLS handshakes on each Android version and manufacturer. Verify Windows remote input only after explicit host and viewer opt-in.

## Troubleshooting

- **Cannot connect:** check the host is sharing, target type, IPv4 address, private-network firewall, same LAN, Wi-Fi client isolation and ports 8765/8766. Automatic IP selection can be wrong with VPNs or multiple adapters.
- **TLS error:** confirm both devices run this updated version. The self-signed certificate is not trusted by a normal browser. Simplified app pairing deliberately does not verify identity.
- **Too many attempts:** wait at least 60 seconds and retry the correct PIN. Some networks share an IP across clients.
- **Android sharing stops or is blank:** check capture permission, foreground notification, OS battery restrictions and protected content. Some Android versions block capture of protected windows. Rotation may require restarting sharing.
- **Control unavailable:** it only works for Windows hosts and must be enabled on the host and viewer; not all keyboard shortcuts or dragging are implemented.

## What is missing

This is not an AnyDesk replacement: no relay/NAT traversal, internet connection broker, accounts, unattended access, mobile remote control, file transfer, audio, clipboard synchronization, robust identity management, full keyboard/drag support or multi-monitor selection. Capture is MJPEG and can consume substantial CPU and bandwidth. Windows captures the primary monitor; Android capture is limited to a scaled display and may not adapt to rotation.

## Fonts

Manrope was downloaded from the [official Google Fonts repository](https://github.com/google/fonts/tree/main/ofl/manrope). The original variable TTF is bundled for Android and retained in `windows/Manrope.ttf`; static Regular/Bold Windows instances were generated from it using fontTools. The upstream file SHA-256 is `3ae11c49db0455a3cc33e37d380f20fdb8c7f8b41dc07625c177e3d87a9d6ae6`. See `licenses/Manrope-OFL.txt` for the SIL Open Font License. DejaVu files retained from the original source are no longer used by the updated UI; their original license remains in `licenses/`.
