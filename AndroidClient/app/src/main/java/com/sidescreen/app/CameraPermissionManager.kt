package com.sidescreen.app

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

class CameraPermissionManager(private val activity: Activity) {

    fun isGranted(): Boolean =
        ContextCompat.checkSelfPermission(activity, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED

    /**
     * True when the user has selected "Don't ask again". In that state,
     * `requestPermissions()` returns immediately without prompting.
     */
    fun isPermanentlyDenied(): Boolean =
        !isGranted() &&
            !ActivityCompat.shouldShowRequestPermissionRationale(activity, Manifest.permission.CAMERA) &&
            hasBeenRequestedAtLeastOnce(activity)

    fun request(requestCode: Int) {
        ActivityCompat.requestPermissions(activity, arrayOf(Manifest.permission.CAMERA), requestCode)
        markRequested(activity)
    }

    fun openAppSettings() {
        val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
            data = Uri.fromParts("package", activity.packageName, null)
        }
        activity.startActivity(intent)
    }

    companion object {
        private const val PREF = "camera_perm"
        private const val KEY_REQUESTED = "requested_once"

        private fun hasBeenRequestedAtLeastOnce(ctx: Context): Boolean =
            ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE).getBoolean(KEY_REQUESTED, false)

        private fun markRequested(ctx: Context) {
            ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE).edit().putBoolean(KEY_REQUESTED, true).apply()
        }
    }
}
