package com.sidescreen.app

import android.content.Context
import android.content.SharedPreferences
import android.util.Base64

class PairedHostStorage(context: Context) {
    private val prefs: SharedPreferences =
        context.getSharedPreferences("paired_host", Context.MODE_PRIVATE)

    data class Entry(val host: String, val port: Int, val token: ByteArray, val macName: String) {
        override fun equals(other: Any?): Boolean {
            if (other !is Entry) return false
            return host == other.host && port == other.port && macName == other.macName &&
                token.contentEquals(other.token)
        }

        override fun hashCode(): Int =
            ((host.hashCode() * 31 + port) * 31 + macName.hashCode()) * 31 + token.contentHashCode()
    }

    fun save(entry: Entry) {
        prefs.edit()
            .putString("host", entry.host)
            .putInt("port", entry.port)
            .putString("token_b64", Base64.encodeToString(entry.token, Base64.NO_WRAP or Base64.NO_PADDING))
            .putString("mac_name", entry.macName)
            .apply()
    }

    fun load(): Entry? {
        val host = prefs.getString("host", null) ?: return null
        val port = prefs.getInt("port", -1).takeIf { it > 0 } ?: return null
        val tokenB64 = prefs.getString("token_b64", null) ?: return null
        val token = try {
            Base64.decode(tokenB64, Base64.NO_WRAP or Base64.NO_PADDING)
        } catch (e: IllegalArgumentException) {
            return null
        }
        if (token.size != 32) return null
        val macName = prefs.getString("mac_name", null) ?: "Mac"
        return Entry(host, port, token, macName)
    }

    fun clear() {
        prefs.edit().clear().apply()
    }
}
