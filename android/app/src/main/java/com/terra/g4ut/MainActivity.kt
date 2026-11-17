package com.terra.g4ut

import android.annotation.SuppressLint
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Terra G4UT — Android-schil.
 * De meetlogica draait in Python (Chaquopy, package g4ut_core); de UI is de web-app in een WebView.
 * Koppeling: JS -> Kotlin -> Python via AndroidBridge.call(method, argsJson),
 *            Python -> JS via G4UT.onFrame(json) op ~30 Hz.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var web: WebView
    private lateinit var api: PyObject
    private val frameExec = Executors.newSingleThreadExecutor()
    private val handler = Handler(Looper.getMainLooper())
    private val busy = AtomicBoolean(false)
    private var running = false

    private val frameTick = object : Runnable {
        override fun run() {
            if (!running) return
            if (busy.compareAndSet(false, true)) {
                frameExec.execute {
                    val json = try { api.callAttr("frame").toString() } catch (e: Exception) { "{\"error\":\"${e.message}\"}" }
                    handler.post {
                        // als string-literal doorgeven (veilig escapen via JSON.stringify-equivalent)
                        web.evaluateJavascript("G4UT.onFrame(${jsString(json)})", null)
                        busy.set(false)
                    }
                }
            }
            handler.postDelayed(this, 40)
        }
    }

    /** Android stuurt verkeer standaard via wifi/4G. Zoek het netwerk met een 192.168.144.x-adres
     *  (Herelink via USB-C) en bind het hele proces eraan, zodat ook de Python-sockets dat gebruiken. */
    private var boundNetwork: Network? = null
    private var lastNetDesc = ""
    private fun hostLog(msg: String) {
        try { api.callAttr("call", "host_log", "{\"msg\":${jsString(msg)}}") } catch (_: Exception) {}
    }
    private val netTick = object : Runnable {
        override fun run() {
            if (!running) return
            try {
                val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
                var found: Network? = null
                val desc = StringBuilder()
                for (n in cm.allNetworks) {
                    val lp = cm.getLinkProperties(n) ?: continue
                    val caps = cm.getNetworkCapabilities(n)
                    val kind = when {
                        caps == null -> "?"
                        caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
                        caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
                        caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_CELLULAR) -> "mobiel"
                        else -> "overig"
                    }
                    val addrs = lp.linkAddresses.joinToString(",") { it.address.hostAddress ?: "" }
                    desc.append("${lp.interfaceName ?: "?"}($kind): $addrs; ")
                    if (lp.linkAddresses.any { it.address.hostAddress?.startsWith("192.168.144.") == true }) found = n
                }
                val d = desc.toString()
                if (d != lastNetDesc) { lastNetDesc = d; hostLog("netwerken: " + (if (d.isEmpty()) "geen" else d)) }
                if (found != null && found != boundNetwork) {
                    cm.bindProcessToNetwork(found); boundNetwork = found
                    hostLog("proces gebonden aan drone-netwerk")
                    web.evaluateJavascript("G4UT.onNet && G4UT.onNet(true)", null)
                } else if (found == null && boundNetwork != null) {
                    cm.bindProcessToNetwork(null); boundNetwork = null
                    web.evaluateJavascript("G4UT.onNet && G4UT.onNet(false)", null)
                }
            } catch (_: Exception) {}
            handler.postDelayed(this, 3000)
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        hideSystemBars()

        // ---- Python starten ----
        if (!Python.isStarted()) Python.start(AndroidPlatform(this))
        api = Python.getInstance().getModule("g4ut_core.api")
        // data buiten de app-sandbox (zichtbaar via USB: Android/data/com.terra.g4ut/files/g4ut)
        val base = File(getExternalFilesDir(null) ?: filesDir, "g4ut").apply { mkdirs() }
        api.callAttr("init", base.absolutePath)
        hostLog("Android ${android.os.Build.VERSION.RELEASE} (SDK ${android.os.Build.VERSION.SDK_INT}), ${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}")

        // ---- WebView ----
        web = WebView(this)
        setContentView(web)
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = true
            allowContentAccess = true
            mediaPlaybackRequiresUserGesture = false
            setSupportZoom(false)
            builtInZoomControls = false
            displayZoomControls = false
            textZoom = 100
            cacheMode = android.webkit.WebSettings.LOAD_NO_CACHE
        }
        web.setBackgroundColor(0xFF1B1B1B.toInt())
        web.webViewClient = WebViewClient()
        web.webChromeClient = WebChromeClient()
        web.addJavascriptInterface(Bridge(), "AndroidBridge")
        web.isHapticFeedbackEnabled = false
        WebView.setWebContentsDebuggingEnabled(true)   // chrome://inspect op een pc
        web.loadUrl("file:///android_asset/index.html")
    }

    override fun onResume() {
        super.onResume()
        hideSystemBars()
        running = true
        handler.post(frameTick)
        handler.post(netTick)
    }

    override fun onPause() {
        running = false
        handler.removeCallbacks(frameTick)
        handler.removeCallbacks(netTick)
        try { api.callAttr("call", "flush", "{}") } catch (_: Exception) {}
        super.onPause()
    }

    override fun onDestroy() {
        running = false
        try { api.callAttr("shutdown") } catch (_: Exception) {}
        frameExec.shutdown()
        super.onDestroy()
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        // geen onbedoeld afsluiten tijdens een inspectie
    }

    private fun hideSystemBars() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        val c = WindowInsetsControllerCompat(window, window.decorView)
        c.hide(WindowInsetsCompat.Type.systemBars())
        c.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_LAYOUT_STABLE or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
    }

    /** JS-bridge: synchroon, geeft JSON terug. Draait op de JavaBridge-thread van de WebView. */
    inner class Bridge {
        @JavascriptInterface
        fun call(method: String, argsJson: String?): String =
            try { api.callAttr("call", method, argsJson ?: "{}").toString() }
            catch (e: Exception) { "{\"ok\":false,\"error\":\"${(e.message ?: "fout").replace("\"", "'")}\"}" }
    }

    companion object {
        /** JSON-string als JS-stringliteral (evaluateJavascript heeft een expressie nodig). */
        fun jsString(s: String): String {
            val sb = StringBuilder(s.length + 16).append('"')
            for (ch in s) when (ch) {
                '"' -> sb.append("\\\"")
                '\\' -> sb.append("\\\\")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                ' ' -> sb.append("\\u2028")
                ' ' -> sb.append("\\u2029")
                else -> sb.append(ch)
            }
            return sb.append('"').toString()
        }
    }
}
