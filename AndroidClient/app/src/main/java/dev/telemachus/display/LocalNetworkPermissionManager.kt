package dev.telemachus.display

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.os.Build
import java.net.InetAddress

class LocalNetworkPermissionManager(
    private val activity: Activity,
) {
    private var pendingConnection: (() -> Unit)? = null

    fun requestIfNeeded(
        host: String,
        connect: () -> Unit,
    ): Boolean {
        if (isLoopbackHost(host) || Build.VERSION.SDK_INT < 37 ||
            activity.checkSelfPermission(Manifest.permission.ACCESS_LOCAL_NETWORK) == PackageManager.PERMISSION_GRANTED
        ) {
            return false
        }
        if (pendingConnection == null) {
            pendingConnection = connect
            activity.requestPermissions(arrayOf(Manifest.permission.ACCESS_LOCAL_NETWORK), REQUEST_CODE)
        }
        return true
    }

    fun onPermissionResult(granted: Boolean) {
        val connect = pendingConnection
        pendingConnection = null
        if (granted) connect?.invoke()
    }

    private fun isLoopbackHost(host: String): Boolean {
        if (host.equals("localhost", ignoreCase = true)) return true
        val octets = host.split('.').map { it.toIntOrNull() }
        if (octets.size == 4 && octets.all { it != null && it in 0..255 }) {
            return octets.first() == 127
        }
        // Numeric IPv6 literals are parsed locally, without a DNS lookup.
        if (host.contains(':') && host.all { it.lowercaseChar() in "0123456789abcdef:.[]" }) {
            return runCatching { InetAddress.getByName(host).isLoopbackAddress }.getOrDefault(false)
        }
        return false
    }

    companion object {
        const val REQUEST_CODE = 1003
    }
}
