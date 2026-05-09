package com.sidescreen.app

import android.net.Uri
import android.util.Base64

object PairingURL {
    data class Parsed(val host: String, val port: Int, val token: ByteArray, val macName: String)

    fun parse(url: String): Parsed? {
        val uri =
            try {
                Uri.parse(url)
            } catch (e: Exception) {
                return null
            }
        if (uri.scheme != "sidescreen") return null
        val host = uri.host ?: return null
        val port = uri.port.takeIf { it in 1..65535 } ?: return null
        val tokenB64 = uri.getQueryParameter("t") ?: return null
        val token =
            try {
                Base64.decode(tokenB64, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
            } catch (e: IllegalArgumentException) {
                return null
            }
        if (token.size != 32) return null
        val name = uri.getQueryParameter("name") ?: "Mac"
        return Parsed(host, port, token, name)
    }
}
