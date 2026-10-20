package dev.telemachus.display

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.os.Build

class LocalNetworkPermissionManager(
    private val activity: Activity,
) {
    private var pendingConnection: (() -> Unit)? = null

    fun requestIfNeeded(connect: () -> Unit): Boolean {
        if (Build.VERSION.SDK_INT < 37 ||
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

    companion object {
        const val REQUEST_CODE = 1003
    }
}
