package com.screenbridge.app

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.graphics.Path
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Lets a connected viewer operate this phone. Android only allows this through an
 * AccessibilityService the user enables by hand in Settings.
 */
class ControlService : AccessibilityService() {
    companion object {
        @Volatile var instance: ControlService? = null
    }

    private val main = Handler(Looper.getMainLooper())

    override fun onServiceConnected() { instance = this }
    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
    override fun onInterrupt() {}
    override fun onUnbind(intent: Intent?): Boolean { instance = null; return super.onUnbind(intent) }
    override fun onDestroy() { instance = null; super.onDestroy() }

    /** All injection runs on the main thread. */
    fun post(block: () -> Unit) { main.post { try { block() } catch (_: Exception) { } } }
    fun postDelayed(ms: Long, block: () -> Unit) { main.postDelayed({ try { block() } catch (_: Exception) { } }, ms) }

    fun stroke(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long): Boolean {
        val path = Path().apply {
            moveTo(x1, y1)
            if (x1 != x2 || y1 != y2) lineTo(x2, y2)
        }
        val g = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, durationMs.coerceAtLeast(1)))
            .build()
        return dispatchGesture(g, null, null)
    }

    fun tap(x: Float, y: Float) = stroke(x, y, x, y, 40)
    fun longPress(x: Float, y: Float) = stroke(x, y, x, y, 700)
    fun doubleTap(x: Float, y: Float) { tap(x, y); postDelayed(140) { tap(x, y) } }

    fun nav(k: String): Boolean = when (k) {
        "back", "esc" -> performGlobalAction(GLOBAL_ACTION_BACK)
        "home", "win" -> performGlobalAction(GLOBAL_ACTION_HOME)
        "recents" -> performGlobalAction(GLOBAL_ACTION_RECENTS)
        "notifications" -> performGlobalAction(GLOBAL_ACTION_NOTIFICATIONS)
        else -> false
    }

    private fun focusedInput(): AccessibilityNodeInfo? =
        rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)

    private fun setText(node: AccessibilityNodeInfo, value: String): Boolean {
        val args = Bundle().putCharSequenceCompat(value)
        return node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
    }

    private fun Bundle.putCharSequenceCompat(v: String): Bundle {
        putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, v)
        return this
    }

    private fun currentText(n: AccessibilityNodeInfo): String =
        if (n.isShowingHintText) "" else (n.text?.toString() ?: "")

    fun typeText(s: String): Boolean {
        val node = focusedInput() ?: return false
        return setText(node, currentText(node) + s)
    }

    fun key(k: String): Boolean {
        return when (k) {
            "enter" -> {
                val node = focusedInput() ?: return false
                if (Build.VERSION.SDK_INT >= 30)
                    node.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)
                else setText(node, currentText(node) + "\n")
            }
            "backspace" -> {
                val node = focusedInput() ?: return false
                setText(node, currentText(node).dropLast(1))
            }
            else -> nav(k)
        }
    }
}
