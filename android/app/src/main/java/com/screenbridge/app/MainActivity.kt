package com.screenbridge.app

import android.Manifest
import android.content.ContentValues
import android.content.Intent
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionManager
import android.net.ConnectivityManager
import android.os.Build
import androidx.activity.result.contract.ActivityResultContracts
import java.net.Inet4Address
import java.net.NetworkInterface
import java.security.SecureRandom
import android.content.pm.ActivityInfo
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Bundle
import android.provider.Settings
import android.os.Environment
import android.os.SystemClock
import android.provider.MediaStore
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.RadioGroup
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.URL
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLException
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import java.util.Locale
import kotlin.concurrent.thread
import kotlin.math.abs
import kotlin.math.min

class MainActivity : AppCompatActivity() {
    private var port = 8765
    private val qNames = arrayOf("Low", "Med", "High")
    private val qVals = intArrayOf(35, 60, 80)
    private val wVals = intArrayOf(720, 1280, 1920)
    private val fpsVals = intArrayOf(15, 24, 30)

    private lateinit var connectPanel: View
    private lateinit var viewerPanel: View
    private lateinit var toolbar: View
    private lateinit var screen: ImageView
    private lateinit var status: TextView
    private lateinit var fpsText: TextView
    private lateinit var ipInput: EditText
    private lateinit var pinInput: EditText
    private lateinit var connectBtn: Button
    private lateinit var shareBtn: Button
    private lateinit var shareInfo: TextView

    private val projLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        val d = r.data
        if (r.resultCode == RESULT_OK && d != null) {
            ShareService.lastError = ""
            ShareService.pin = String.format(Locale.US, "%08d", SecureRandom().nextInt(100000000))
            startForegroundService(Intent(this, ShareService::class.java).putExtra("code", r.resultCode).putExtra("data", d))
            shareInfo.text = "Starting phone sharing…"
            for (delay in listOf(700L, 2000L, 5000L)) shareBtn.postDelayed({ updateShare() }, delay)
        } else toast("Screen capture permission denied")
    }
    private val notifLauncher = registerForActivityResult(ActivityResultContracts.RequestPermission()) { launchProjection() }

    private val io = Executors.newSingleThreadExecutor()
    private val pendingMove = AtomicReference<JSONObject?>(null)
    private val moveScheduled = AtomicBoolean(false)
    private var ip = ""
    private var pin = ""
    private var quality = 1
    private var control = false
    private var controlAllowed = false
    private var dragMode = false
    private var dragStart: Pair<Double, Double>? = null
    private var landscape = false
    private var immersive = false
    @Volatile private var running = false
    @Volatile private var gen = 0
    @Volatile private var lastBmp: Bitmap? = null
    private var lastMove = 0L
    private var lastScrollY = 0f
    private var swipeStart: Pair<Double, Double>? = null
    private var swipePx = Pair(0f, 0f)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        connectPanel = findViewById(R.id.connectPanel)
        viewerPanel = findViewById(R.id.viewerPanel)
        toolbar = findViewById(R.id.toolbar)
        screen = findViewById(R.id.screenView)
        status = findViewById(R.id.statusText)
        fpsText = findViewById(R.id.fpsText)
        ipInput = findViewById(R.id.ipInput)
        pinInput = findViewById(R.id.pinInput)
        connectBtn = findViewById(R.id.connectBtn)

        val prefs = getSharedPreferences("sb", MODE_PRIVATE)
        ipInput.setText(prefs.getString("ip", ""))
        prefs.edit().remove("pin").apply()

        connectBtn.setOnClickListener {
            port = if (findViewById<RadioGroup>(R.id.targetType).checkedRadioButtonId == R.id.targetAndroid) 8766 else 8765
            ip = ipInput.text.toString().trim()
            pin = pinInput.text.toString().trim()
            if (ip.isEmpty() || pin.isEmpty()) { status.text = "Enter the IP and PIN shown on the other device"; return@setOnClickListener }
            if (!ip.matches(Regex("""[0-9]{1,3}(\.[0-9]{1,3}){3}""")) ||
                ip.split(".").any { part -> part.toIntOrNull()?.let { it in 0..255 } != true }) {
                status.text = "Enter a valid local IPv4 address"; return@setOnClickListener
            }
            if (!pin.matches(Regex("""[0-9]{8}"""))) {
                status.text = "Enter the eight-digit PIN shown on the other device"; return@setOnClickListener
            }
            prefs.edit().putString("ip", ip).apply()
            connect()
        }

        findViewById<Button>(R.id.btnControl).setOnClickListener {
            if (control) {
                control = false
                (it as Button).text = "Control: OFF"
            } else checkControl()
        }
        findViewById<Button>(R.id.btnDrag).setOnClickListener {
            dragMode = !dragMode
            dragStart = null
            (it as Button).text = if (dragMode) "Drag: ON" else "Drag: OFF"
            toast(if (dragMode) "Touch the start, slide, then lift to drag" else "Normal pointer mode")
        }
        findViewById<Button>(R.id.btnShortcuts).setOnClickListener {
            if (!control) { toast("Turn Control ON first"); return@setOnClickListener }
            val labels = arrayOf("Copy", "Paste", "Cut", "Select all", "Undo", "Alt + Tab", "Windows + D")
            val keys = arrayOf("copy", "paste", "cut", "select_all", "undo", "alt_tab", "win_d")
            AlertDialog.Builder(this).setTitle("Windows shortcuts")
                .setItems(labels) { _, which -> send(JSONObject().put("t", "shortcut").put("k", keys[which])) }
                .setNegativeButton("Cancel", null).show()
        }
        for ((id, key) in listOf(R.id.btnBack to "back", R.id.btnHome to "home", R.id.btnRecents to "recents")) {
            findViewById<Button>(id).setOnClickListener {
                if (port != 8766) { toast("Back, Home and Recents work on Android hosts only"); return@setOnClickListener }
                if (!control) { toast("Turn Control ON first"); return@setOnClickListener }
                send(JSONObject().put("t", "nav").put("k", key))
            }
        }
        findViewById<Button>(R.id.btnKeyboard).setOnClickListener { typeDialog() }
        findViewById<Button>(R.id.btnEnter).setOnClickListener { send(JSONObject().put("t", "key").put("k", "enter")) }
        findViewById<Button>(R.id.btnEsc).setOnClickListener { send(JSONObject().put("t", "key").put("k", "esc")) }
        findViewById<Button>(R.id.btnWin).setOnClickListener { send(JSONObject().put("t", "key").put("k", "win")) }
        findViewById<Button>(R.id.btnShot).setOnClickListener { saveShot() }
        findViewById<Button>(R.id.btnQuality).setOnClickListener {
            quality = (quality + 1) % 3
            (it as Button).text = "Quality: ${qNames[quality]}"
            startStream()
        }
        findViewById<Button>(R.id.btnRotate).setOnClickListener {
            landscape = !landscape
            requestedOrientation = if (landscape) ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
            else ActivityInfo.SCREEN_ORIENTATION_SENSOR_PORTRAIT
        }
        findViewById<Button>(R.id.btnDisconnect).setOnClickListener { disconnect() }
        findViewById<Button>(R.id.menuBtn).setOnClickListener { toggleImmersive() }

        setupTouch()
        shareBtn = findViewById(R.id.shareBtn)
        shareInfo = findViewById(R.id.shareInfo)
        shareBtn.setOnClickListener { toggleShare() }
        findViewById<Button>(R.id.hostCtlBtn).setOnClickListener { toggleHostControl() }
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (viewerPanel.visibility == View.VISIBLE) disconnect() else finish()
            }
        })
    }

    private fun url(path: String, extra: String = ""): HttpURLConnection {
        val conn = URL("https://$ip:$port$path$extra").openConnection() as HttpsURLConnection
        conn.sslSocketFactory = TlsIdentity.lanClient().socketFactory
        // Host identity is intentionally not verified in simplified pairing.
        conn.hostnameVerifier = javax.net.ssl.HostnameVerifier { _, _ -> true }
        conn.instanceFollowRedirects = false
        conn.setRequestProperty("Authorization", "Bearer $pin")
        return conn
    }

    private fun connect() {
        connectBtn.isEnabled = false
        status.text = "Connecting. Approve on the other device if prompted…"
        thread {
            var msg: String? = null
            try {
                val c = url("/ping"); c.connectTimeout = 5000; c.readTimeout = 70000
                when (c.responseCode) {
                    200 -> {
                        controlAllowed = JSONObject(c.inputStream.bufferedReader().readText()).optBoolean("control", false)
                    }
                    401 -> msg = "Wrong PIN"
                    403 -> msg = "The other device denied access"
                    429 -> msg = "Too many attempts. Wait 60 seconds."
                    else -> msg = "Unexpected response (${c.responseCode})"
                }
                c.disconnect()
            } catch (e: Exception) {
                msg = when (e) {
                    is SocketTimeoutException -> "Timed out. Check sharing, Wi-Fi and firewall."
                    is ConnectException -> "Connection refused. Check host IP, type and sharing."
                    is SSLException -> "TLS handshake failed. Update both apps and restart sharing."
                    else -> "Connection failed (${e.javaClass.simpleName}). Check IP and sharing."
                }
            }
            runOnUiThread {
                connectBtn.isEnabled = true
                if (msg != null) status.text = msg else showViewer()
            }
        }
    }

    private fun showViewer() {
        connectPanel.visibility = View.GONE
        viewerPanel.visibility = View.VISIBLE
        viewerPanel.alpha = 0f
        viewerPanel.animate().alpha(1f).setDuration(240).start()
        status.text = "Connected. Host identity is not verified."
        startStream()
    }

    private fun checkControl() {
        val checkGen = gen
        toast("Checking Windows control permission…")
        thread {
            var message: String? = null
            var allowed = false
            var c: HttpURLConnection? = null
            try {
                c = url("/ping"); c.connectTimeout = 5000; c.readTimeout = 7000
                if (c.responseCode == 200) allowed = JSONObject(c.inputStream.bufferedReader().readText()).optBoolean("control", false)
                else message = "Could not check permission (HTTP ${c.responseCode})"
            } catch (_: Exception) {
                message = "Cannot check permission. Reconnect and try again."
            } finally { c?.disconnect() }
            runOnUiThread {
                if (!running || checkGen != gen) return@runOnUiThread
                controlAllowed = allowed
                control = allowed
                findViewById<Button>(R.id.btnControl).text = if (allowed) "Control: ON" else "Control: OFF"
                toast(message ?: if (allowed) "Control enabled" else if (port == 8766) "On the other phone, open ScreenBridge and turn Remote control ON, then tap Control again." else "On the PC, enable Allow remote mouse and keyboard, then tap Control again.")
            }
        }
    }

    private fun disconnect() {
        running = false; gen++
        control = false
        controlAllowed = false
        pendingMove.set(null)
        dragMode = false; dragStart = null
        findViewById<Button>(R.id.btnDrag).text = "Drag: OFF"
        findViewById<Button>(R.id.btnControl).text = "Control: OFF"
        if (immersive) toggleImmersive()
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        landscape = false
        viewerPanel.visibility = View.GONE
        connectPanel.visibility = View.VISIBLE
        connectPanel.alpha = 0f
        connectPanel.animate().alpha(1f).setDuration(200).start()
    }

    private fun readLine(ins: InputStream): String? {
        val sb = StringBuilder()
        while (true) {
            val c = ins.read()
            if (c < 0) return null
            if (c == '\n'.code) break
            if (c != '\r'.code) sb.append(c.toChar())
            if (sb.length > 8192) throw IllegalArgumentException("Stream header too long")
        }
        return sb.toString()
    }

    private fun startStream() {
        running = true
        val myGen = ++gen
        thread {
            while (running && myGen == gen) {
                try {
                    val c = url("/stream", "?q=${qVals[quality]}&w=${wVals[quality]}&fps=${fpsVals[quality]}")
                    c.connectTimeout = 5000; c.readTimeout = 10000
                    val ins = BufferedInputStream(c.inputStream, 1 shl 16)
                    var frames = 0
                    var t0 = SystemClock.elapsedRealtime()
                    outer@ while (running && myGen == gen) {
                        var len = -1
                        while (true) {
                            val l = readLine(ins) ?: break@outer
                            if (l.startsWith("Content-Length:", true)) len = l.substringAfter(":").trim().toInt()
                            else if (l.isEmpty() && len > 0) break
                        }
                        if (len < 1 || len > 12000000) throw IllegalArgumentException("Invalid frame size")
                        val buf = ByteArray(len)
                        var off = 0
                        while (off < len) {
                            val n = ins.read(buf, off, len - off)
                            if (n < 0) break@outer
                            off += n
                        }
                        val bmp = BitmapFactory.decodeByteArray(buf, 0, len) ?: continue
                        frames++
                        val now = SystemClock.elapsedRealtime()
                        val fps = if (now - t0 >= 1000) { val f = frames; frames = 0; t0 = now; f } else -1
                        runOnUiThread {
                            lastBmp = bmp
                            screen.setImageBitmap(bmp)
                            if (fps >= 0) fpsText.text = "$fps FPS"
                        }
                    }
                    c.disconnect()
                } catch (e: Exception) {
                    if (running && myGen == gen) runOnUiThread { fpsText.text = "Reconnecting…" }
                }
                if (running && myGen == gen) Thread.sleep(1000)
            }
        }
    }

    // ----- share this phone to Windows -----
    override fun onResume() {
        super.onResume()
        if (::shareBtn.isInitialized) updateShare()
    }

    private fun toggleShare() {
        if (ShareService.running) {
            stopService(Intent(this, ShareService::class.java))
            shareBtn.postDelayed({ updateShare() }, 500)
        } else if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else launchProjection()
    }

    private fun launchProjection() {
        projLauncher.launch(getSystemService(MediaProjectionManager::class.java).createScreenCaptureIntent())
    }

    private fun toggleHostControl() {
        if (ShareService.allowControl) {
            ShareService.allowControl = false
        } else if (ControlService.instance == null) {
            AlertDialog.Builder(this).setTitle("Allow remote control")
                .setMessage("Android needs you to enable the ScreenBridge remote control accessibility service.\n\nSettings > Accessibility > Installed apps (or Downloaded apps) > ScreenBridge remote control > turn ON.\n\nIf the switch is greyed out: Settings > Apps > ScreenBridge > menu (three dots) > Allow restricted settings.")
                .setPositiveButton("Open settings") { _, _ -> startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
                .setNegativeButton("Cancel", null).show()
        } else if (!ShareService.running) {
            toast("Start phone sharing first")
        } else ShareService.allowControl = true
        updateShare()
    }

    private fun updateShare() {
        val ctlOn = ShareService.allowControl && ControlService.instance != null
        if (ShareService.allowControl && ControlService.instance == null) ShareService.allowControl = false
        findViewById<Button>(R.id.hostCtlBtn).text = if (ctlOn) "Remote control: ON (tap to turn off)" else "Remote control: OFF"
        if (ShareService.running) {
            shareInfo.text = "Sharing now. Choose Android on the viewer.\nIP: ${localIp()}   PIN: ${ShareService.pin}\n" + (if (ctlOn) "Viewers can operate this phone." else "View only until you turn Remote control on.")
            shareBtn.text = "Stop sharing"
        } else {
            shareInfo.text = if (ShareService.lastError.isNotEmpty()) ShareService.lastError
                else "Your local IP: ${localIp()}\nShare this screen on Windows or Android."
            shareBtn.text = "Share phone screen  →"
        }
    }

    private fun localIp(): String {
        return try {
            val addrs = NetworkInterface.getNetworkInterfaces().toList()
                .filter { it.isUp && !it.isLoopback }
                .flatMap { n -> n.inetAddresses.toList().map { n.name to it } }
                .filter { it.second is Inet4Address && !it.second.isLoopbackAddress && !it.second.isLinkLocalAddress }
            val wifi = addrs.firstOrNull { it.first.startsWith("wlan") || it.first.startsWith("ap") }
            val cm = getSystemService(ConnectivityManager::class.java)
            val active = cm.getLinkProperties(cm.activeNetwork)?.linkAddresses
                ?.map { it.address }?.firstOrNull { it is Inet4Address && !it.isLoopbackAddress && !it.isLinkLocalAddress }
            wifi?.second?.hostAddress ?: active?.hostAddress ?: addrs.firstOrNull()?.second?.hostAddress ?: "?"
        } catch (_: Exception) { "?" }
    }

    // ----- tools -----
    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()

    private fun saveShot() {
        val b = lastBmp ?: return toast("No frame yet")
        val v = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, "ScreenBridge_${System.currentTimeMillis()}.png")
            put(MediaStore.Images.Media.MIME_TYPE, "image/png")
            put(MediaStore.Images.Media.RELATIVE_PATH, Environment.DIRECTORY_PICTURES + "/ScreenBridge")
        }
        val uri = contentResolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, v)
        if (uri == null) { toast("Could not save"); return }
        contentResolver.openOutputStream(uri)?.use { b.compress(Bitmap.CompressFormat.PNG, 100, it) }
        toast("Saved to Pictures/ScreenBridge")
    }

    private fun typeDialog() {
        if (!control) { toast("Turn Control ON first"); return }
        val et = EditText(this).apply { hint = "Text to type on the PC" }
        AlertDialog.Builder(this).setTitle("Type on PC").setView(et)
            .setPositiveButton("Send") { _, _ -> send(JSONObject().put("t", "text").put("s", et.text.toString())) }
            .setNegativeButton("Cancel", null).show()
    }

    private fun toggleImmersive() {
        immersive = !immersive
        toolbar.visibility = if (immersive) View.GONE else View.VISIBLE
        val ctl = WindowInsetsControllerCompat(window, window.decorView)
        WindowCompat.setDecorFitsSystemWindows(window, !immersive)
        if (immersive) {
            ctl.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            ctl.hide(WindowInsetsCompat.Type.systemBars())
        } else ctl.show(WindowInsetsCompat.Type.systemBars())
    }

    // ----- remote control -----
    private fun send(j: JSONObject) {
        if (!control || !controlAllowed) return
        val myGen = gen
        if (j.optString("t") == "move") {
            pendingMove.set(j)
            scheduleMove(myGen)
        } else {
            // Do not deliver a stale pointer move after a click or completed drag.
            pendingMove.set(null)
            io.execute { sendNow(j, myGen) }
        }
    }

    private fun scheduleMove(myGen: Int) {
        if (!moveScheduled.compareAndSet(false, true)) return
        io.execute {
            try {
                pendingMove.getAndSet(null)?.let { sendNow(it, myGen) }
            } finally {
                moveScheduled.set(false)
                if (pendingMove.get() != null && running && control) scheduleMove(gen)
            }
        }
    }

    private fun sendNow(j: JSONObject, myGen: Int) {
        if (!running || gen != myGen || !control || !controlAllowed) return
        var c: HttpURLConnection? = null
        try {
            c = url("/input")
            c.requestMethod = "POST"; c.doOutput = true
            c.connectTimeout = 3000; c.readTimeout = 4000
            c.outputStream.use { it.write(j.toString().toByteArray()) }
            if (c.responseCode == 403) runOnUiThread {
                if (gen == myGen) {
                    control = false; controlAllowed = false
                    findViewById<Button>(R.id.btnControl).text = "Control: OFF"
                    toast("Windows control disabled. Enable it on the PC and tap Control again.")
                }
            }
        } catch (_: Exception) {
            if (j.optString("t") != "move") runOnUiThread {
                if (gen == myGen) toast("Could not send remote input. Check your connection.")
            }
        } finally { c?.disconnect() }
    }

    private fun norm(ev: MotionEvent): Pair<Double, Double>? {
        val b = lastBmp ?: return null
        val vw = screen.width.toFloat(); val vh = screen.height.toFloat()
        val s = min(vw / b.width, vh / b.height)
        val dw = b.width * s; val dh = b.height * s
        val x = ((ev.x - (vw - dw) / 2) / dw).coerceIn(0f, 1f)
        val y = ((ev.y - (vh - dh) / 2) / dh).coerceIn(0f, 1f)
        return Pair(x.toDouble(), y.toDouble())
    }

    private fun pt(t: String, ev: MotionEvent) {
        val p = norm(ev) ?: return
        send(JSONObject().put("t", t).put("x", p.first).put("y", p.second))
    }

    private fun setupTouch() {
        val gd = GestureDetector(this, object : GestureDetector.SimpleOnGestureListener() {
            override fun onDown(e: MotionEvent) = true
            override fun onSingleTapConfirmed(e: MotionEvent): Boolean { pt("click", e); return true }
            override fun onDoubleTap(e: MotionEvent): Boolean { pt("dbl", e); return true }
            override fun onLongPress(e: MotionEvent) { pt("rclick", e) }
        })
        screen.setOnTouchListener { _, ev ->
            if (!control) return@setOnTouchListener false
            if (dragMode) {
                when (ev.actionMasked) {
                    MotionEvent.ACTION_DOWN -> dragStart = norm(ev)
                    MotionEvent.ACTION_UP -> {
                        val start = dragStart
                        val end = norm(ev)
                        if (start != null && end != null) {
                            send(JSONObject().put("t", "drag").put("x", start.first).put("y", start.second)
                                .put("x2", end.first).put("y2", end.second))
                        }
                        dragStart = null
                    }
                    MotionEvent.ACTION_CANCEL, MotionEvent.ACTION_POINTER_DOWN -> dragStart = null
                }
                return@setOnTouchListener true
            }
            gd.onTouchEvent(ev)
            if (port == 8766) {
                when (ev.actionMasked) {
                    MotionEvent.ACTION_DOWN -> { swipeStart = norm(ev); swipePx = Pair(ev.x, ev.y) }
                    MotionEvent.ACTION_POINTER_DOWN -> swipeStart = null
                    MotionEvent.ACTION_UP -> {
                        val st = swipeStart; val en = norm(ev)
                        if (st != null && en != null && Math.hypot((ev.x - swipePx.first).toDouble(), (ev.y - swipePx.second).toDouble()) > 40) {
                            send(JSONObject().put("t", "drag").put("x", st.first).put("y", st.second).put("x2", en.first).put("y2", en.second))
                        }
                        swipeStart = null
                    }
                    MotionEvent.ACTION_MOVE -> if (ev.pointerCount == 2) {
                        val y = (ev.getY(0) + ev.getY(1)) / 2
                        val d = y - lastScrollY
                        if (abs(d) > 24) { send(JSONObject().put("t", "scroll").put("dy", if (d > 0) 3 else -3)); lastScrollY = y }
                    }
                }
                if (ev.actionMasked == MotionEvent.ACTION_POINTER_DOWN && ev.pointerCount == 2) lastScrollY = (ev.getY(0) + ev.getY(1)) / 2
                return@setOnTouchListener true
            }
            when (ev.actionMasked) {
                MotionEvent.ACTION_POINTER_DOWN -> lastScrollY = (ev.getY(0) + ev.getY(1)) / 2
                MotionEvent.ACTION_UP -> if (ev.eventTime - ev.downTime > 250) pt("move", ev)
                MotionEvent.ACTION_MOVE -> if (ev.pointerCount == 1) {
                    val now = SystemClock.uptimeMillis()
                    if (now - lastMove > 35) { lastMove = now; pt("move", ev) }
                } else if (ev.pointerCount == 2) {
                    val y = (ev.getY(0) + ev.getY(1)) / 2
                    val d = y - lastScrollY
                    if (abs(d) > 24) { send(JSONObject().put("t", "scroll").put("dy", if (d > 0) 3 else -3)); lastScrollY = y }
                }
            }
            true
        }
    }
}
