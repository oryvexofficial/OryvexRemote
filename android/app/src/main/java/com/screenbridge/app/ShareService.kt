package com.screenbridge.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.SystemClock
import android.util.Log
import java.io.ByteArrayOutputStream
import org.json.JSONObject
import java.net.ServerSocket
import java.net.Socket
import java.security.MessageDigest
import java.util.Collections
import java.util.ArrayDeque
import java.util.LinkedHashMap
import java.util.concurrent.Semaphore
import javax.net.ssl.SSLServerSocket
import kotlin.concurrent.thread

/** Captures a consented screen and serves MJPEG over LAN-only TLS. */
class ShareService : Service() {
    companion object {
        @Volatile var running = false
        @Volatile var pin = ""
        @Volatile var lastError = ""
        @Volatile var allowControl = false
        const val PORT = 8766
        const val TAG = "ScreenBridgeShare"
    }

    private var projection: MediaProjection? = null
    private var display: VirtualDisplay? = null
    private var reader: ImageReader? = null
    private var server: ServerSocket? = null
    private val capThread = HandlerThread("capture")
    @Volatile private var frame: ByteArray? = null
    private val clients = Collections.synchronizedSet(mutableSetOf<Socket>())
    private val slots = Semaphore(16)
    private val failures = LinkedHashMap<String, ArrayDeque<Long>>()

    @Synchronized private fun allowed(ip: String, correct: Boolean): Boolean {
        val now = SystemClock.elapsedRealtime()
        val q = failures.remove(ip) ?: ArrayDeque()
        while (q.isNotEmpty() && q.peekFirst() <= now - 60000) q.removeFirst()
        val blocked = q.size >= 5
        if (!blocked && !correct) q.addLast(now)
        if (q.isNotEmpty()) failures[ip] = q
        while (failures.size > 1024) failures.remove(failures.keys.first())
        return !blocked
    }

    private fun boundedLine(ins: java.io.InputStream): String? {
        val line = StringBuilder()
        while (line.length < 8192) {
            val c = ins.read()
            if (c < 0) return if (line.isEmpty()) null else line.toString()
            if (c == '\n'.code) return line.toString().trimEnd('\r')
            line.append(c.toChar())
        }
        throw IllegalArgumentException("Header too long")
    }

    private fun reply(out: java.io.OutputStream, code: Int, reason: String, body: String = "", ctype: String = "text/plain", close: Boolean = true) {
        val b = body.toByteArray(Charsets.UTF_8)
        out.write(("HTTP/1.1 $code $reason\r\nContent-Type: $ctype\r\nContent-Length: ${b.size}\r\nConnection: ${if (close) "close" else "keep-alive"}\r\n\r\n").toByteArray())
        out.write(b); out.flush()
    }

    /** Applies one normalized remote-input command with the accessibility service. */
    private fun applyInput(d: JSONObject) {
        val cs = ControlService.instance ?: throw IllegalStateException("accessibility off")
        val (sw, sh) = ScreenSize.get(this)
        fun px(k: String, extent: Int): Float {
            val v = d.getDouble(k)
            if (v.isNaN() || v < 0.0 || v > 1.0) throw IllegalArgumentException("bad coordinate")
            return (v * (extent - 1)).toFloat()
        }
        when (d.getString("t")) {
            "move" -> { }
            "click" -> { val x = px("x", sw); val y = px("y", sh); cs.post { cs.tap(x, y) } }
            "dbl" -> { val x = px("x", sw); val y = px("y", sh); cs.post { cs.doubleTap(x, y) } }
            "rclick" -> { val x = px("x", sw); val y = px("y", sh); cs.post { cs.longPress(x, y) } }
            "drag" -> {
                val x1 = px("x", sw); val y1 = px("y", sh); val x2 = px("x2", sw); val y2 = px("y2", sh)
                val ms = d.optLong("ms", 350L).coerceIn(80L, 2000L)
                cs.post { cs.stroke(x1, y1, x2, y2, ms) }
            }
            "scroll" -> {
                val dy = d.getInt("dy")
                if (dy < -10 || dy > 10) throw IllegalArgumentException("bad scroll")
                val half = (sh * 0.06f * kotlin.math.abs(dy)).coerceAtMost(sh * 0.4f)
                val cx = sw / 2f; val cy = sh / 2f
                val from = if (dy > 0) cy - half else cy + half
                val to = if (dy > 0) cy + half else cy - half
                cs.post { cs.stroke(cx, from, cx, to, 250) }
            }
            "text" -> {
                val t = d.getString("s")
                if (t.length > 2000) throw IllegalArgumentException("text too long")
                cs.post { cs.typeText(t) }
            }
            "key" -> { val k = d.getString("k"); cs.post { cs.key(k) } }
            "nav" -> { val k = d.getString("k"); cs.post { cs.nav(k) } }
            else -> throw IllegalArgumentException("unknown action")
        }
    }

    override fun onBind(i: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent == null || intent.action == "stop") { stopSelf(); return START_NOT_STICKY }
        lastError = ""
        allowControl = false
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel("share", "Screen sharing", NotificationManager.IMPORTANCE_LOW))
        val stop = PendingIntent.getService(this, 0, Intent(this, ShareService::class.java).setAction("stop"), PendingIntent.FLAG_IMMUTABLE)
        val n = Notification.Builder(this, "share")
            .setContentTitle("ScreenBridge is sharing this phone")
            .setContentText("Your screen is visible to devices with the PIN")
            .setSmallIcon(android.R.drawable.ic_menu_share)
            .addAction(Notification.Action.Builder(android.R.drawable.ic_delete, "Stop", stop).build())
            .build()
        startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)

        val code = intent.getIntExtra("code", 0)
        @Suppress("DEPRECATION")
        val data: Intent = (if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra("data", Intent::class.java)
        else intent.getParcelableExtra("data")) ?: run { stopSelf(); return START_NOT_STICKY }

        try {
        val mp = getSystemService(MediaProjectionManager::class.java).getMediaProjection(code, data)
        projection = mp
        mp.registerCallback(object : MediaProjection.Callback() { override fun onStop() { stopSelf() } }, null)

        val dm = resources.displayMetrics
        val (realW, realH) = ScreenSize.get(this)
        val w = minOf(720, realW)
        val h = realH * w / realW
        reader = ImageReader.newInstance(w, h, PixelFormat.RGBA_8888, 2)
        capThread.start()
        display = mp.createVirtualDisplay("sb", w, h, dm.densityDpi, DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            reader!!.surface, null, null)
        reader!!.setOnImageAvailableListener({ r ->
            val img = r.acquireLatestImage() ?: return@setOnImageAvailableListener
            try {
                val p = img.planes[0]
                val pad = (p.rowStride - p.pixelStride * w) / p.pixelStride
                val full = Bitmap.createBitmap(w + pad, h, Bitmap.Config.ARGB_8888)
                full.copyPixelsFromBuffer(p.buffer)
                val bmp = Bitmap.createBitmap(full, 0, 0, w, h)
                val out = ByteArrayOutputStream()
                bmp.compress(Bitmap.CompressFormat.JPEG, 60, out)
                frame = out.toByteArray()
                if (bmp !== full) bmp.recycle()
                full.recycle()
            } catch (e: Exception) {
                Log.e(TAG, "Screen capture failed", e)
                lastError = "Screen capture failed (${e.javaClass.simpleName}). Stop and start sharing again."
                stopSelf()
            } finally { img.close() }
        }, Handler(capThread.looper))

        server = try {
            val ctx = TlsIdentity.host()
            (ctx.serverSocketFactory.createServerSocket(PORT) as SSLServerSocket).apply {
                enabledProtocols = arrayOf("TLSv1.2")
            }
        } catch (e: Exception) {
            Log.e(TAG, "TLS server startup failed", e)
            lastError = "Could not start secure server (${e.javaClass.simpleName}). Check port 8766 and restart sharing."
            stopSelf()
            return START_NOT_STICKY
        }
        running = true
        thread {
            try {
                while (running) {
                    val s = server!!.accept()
                    if (!slots.tryAcquire()) { s.close(); continue }
                    clients.add(s)
                    thread { try { handle(s) } finally { clients.remove(s); slots.release() } }
                }
            } catch (_: Exception) { }
        }
        return START_NOT_STICKY
        } catch (e: Exception) {
            Log.e(TAG, "Screen sharing startup failed", e)
            lastError = "Could not start phone sharing (${e.javaClass.simpleName}). Check screen capture permission."
            stopSelf()
            return START_NOT_STICKY
        }
    }

    private fun handle(s: Socket) {
        try {
            s.use {
                it.soTimeout = 10000
                val ins = java.io.BufferedInputStream(it.getInputStream())
                val out = it.getOutputStream()
                while (running) {
                    val first = boundedLine(ins) ?: return
                    val parts = first.split(" ")
                    if (parts.size != 3) return
                    val method = parts[0]
                    val path = parts[1].substringBefore("?")
                    var auth = ""
                    var length = 0
                    var close = false
                    var count = 0
                    while (true) {
                        val l = boundedLine(ins) ?: return
                        if (l.isEmpty()) break
                        if (++count > 50) return
                        if (l.startsWith("Authorization:", true)) auth = l.substringAfter(":").trim()
                        else if (l.startsWith("Content-Length:", true)) length = l.substringAfter(":").trim().toIntOrNull() ?: -1
                        else if (l.startsWith("Connection:", true) && l.contains("close", true)) close = true
                    }
                    if (length < 0 || length > 8192) { reply(out, 413, "Too Large"); return }
                    val body = ByteArray(length)
                    var off = 0
                    while (off < length) {
                        val n = ins.read(body, off, length - off)
                        if (n < 0) return
                        off += n
                    }
                    if (path != "/ping" && path != "/stream" && path != "/input") { reply(out, 404, "Not Found"); return }
                    if ((path == "/input") != (method == "POST") || (method != "GET" && method != "POST")) {
                        reply(out, 405, "Method Not Allowed"); return
                    }
                    if (!running || !pin.matches(Regex("[0-9]{8}"))) { reply(out, 503, "Service Unavailable"); return }
                    val candidate = if (auth.startsWith("Bearer ")) auth.substring(7) else ""
                    val correct = MessageDigest.isEqual(candidate.toByteArray(Charsets.UTF_8), pin.toByteArray(Charsets.UTF_8))
                    if (!allowed(it.inetAddress.hostAddress ?: "", correct)) { reply(out, 429, "Too Many Requests"); return }
                    if (!correct) { reply(out, 401, "Unauthorized"); return }
                    val controlOn = allowControl && ControlService.instance != null
                    if (path == "/ping") {
                        val (sw, sh) = ScreenSize.get(this)
                        val info = JSONObject().put("name", Build.MODEL).put("w", sw).put("h", sh).put("control", controlOn)
                        reply(out, 200, "OK", info.toString(), "application/json", close)
                        if (close) return
                        continue
                    }
                    if (path == "/input") {
                        if (!controlOn) { reply(out, 403, "Forbidden", "control disabled"); return }
                        try {
                            applyInput(JSONObject(String(body, Charsets.UTF_8)))
                            reply(out, 200, "OK", "ok", "text/plain", close)
                        } catch (e: Exception) {
                            reply(out, 400, "Bad Request", "bad input"); return
                        }
                        if (close) return
                        continue
                    }
                    out.write("HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=frame\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n".toByteArray())
                    out.flush()
                    var last: ByteArray? = null
                    var sentAt = 0L
                    while (running) {
                        val f = frame
                        val now = SystemClock.elapsedRealtime()
                        if (f != null && (f !== last || now - sentAt >= 1000)) {
                            out.write("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${f.size}\r\n\r\n".toByteArray())
                            out.write(f); out.write("\r\n".toByteArray()); out.flush()
                            last = f
                            sentAt = now
                        }
                        Thread.sleep(50)
                    }
                    return
                }
            }
        } catch (e: Exception) {
            if (running) Log.w(TAG, "Viewer connection ended (${e.javaClass.simpleName})")
        }
    }

    override fun onDestroy() {
        running = false
        allowControl = false
        try { server?.close() } catch (_: Exception) { }
        synchronized(clients) { clients.toList().forEach { try { it.close() } catch (_: Exception) {} }; clients.clear() }
        pin = ""
        display?.release()
        reader?.close()
        projection?.stop()
        capThread.quitSafely()
        super.onDestroy()
    }
}
