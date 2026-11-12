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
import java.io.BufferedReader
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

    private fun boundedLine(reader: BufferedReader): String? {
        val line = StringBuilder()
        while (line.length < 8192) {
            val c = reader.read()
            if (c < 0) return if (line.isEmpty()) null else line.toString()
            if (c == '\n'.code) return line.toString().trimEnd('\r')
            line.append(c.toChar())
        }
        throw IllegalArgumentException("Header too long")
    }

    override fun onBind(i: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent == null || intent.action == "stop") { stopSelf(); return START_NOT_STICKY }
        lastError = ""
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
        val w = minOf(720, dm.widthPixels)
        val h = dm.heightPixels * w / dm.widthPixels
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
                val ins = it.getInputStream().bufferedReader()
                val first = boundedLine(ins) ?: return
                val parts = first.split(" ")
                if (parts.size != 3 || parts[0] != "GET") return
                val path = parts[1].substringBefore("?")
                var auth = ""
                var count = 0
                while (true) {
                    val l = boundedLine(ins) ?: return
                    if (l.isEmpty()) break
                    if (++count > 50) return
                    if (l.startsWith("Authorization:", true)) auth = l.substringAfter(":").trim()
                }
                val out = it.getOutputStream()
                if (path != "/ping" && path != "/stream") {
                    out.write("HTTP/1.0 404 Not Found\r\nContent-Length: 0\r\n\r\n".toByteArray()); return
                }
                if (!running || !pin.matches(Regex("[0-9]{8}"))) {
                    out.write("HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n".toByteArray()); return
                }
                val candidate = if (auth.startsWith("Bearer ")) auth.substring(7) else ""
                val correct = MessageDigest.isEqual(candidate.toByteArray(Charsets.UTF_8), pin.toByteArray(Charsets.UTF_8))
                if (!allowed(it.inetAddress.hostAddress ?: "", correct)) {
                    out.write("HTTP/1.0 429 Too Many Requests\r\nContent-Length: 0\r\n\r\n".toByteArray()); return
                }
                if (!correct) {
                    out.write("HTTP/1.0 401 Unauthorized\r\nContent-Length: 0\r\n\r\n".toByteArray()); return
                }
                if (path == "/ping") {
                    out.write("HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: 17\r\n\r\n{\"control\":false}".toByteArray()); return
                }
                out.write("HTTP/1.0 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=frame\r\nCache-Control: no-store\r\n\r\n".toByteArray())
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
            }
        } catch (e: Exception) {
            if (running) Log.w(TAG, "Viewer connection ended (${e.javaClass.simpleName})")
        }
    }

    override fun onDestroy() {
        running = false
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
