package dev.telemachus.display

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class PairedHostStorage(
    context: Context,
) {
    private val prefs: SharedPreferences =
        context.getSharedPreferences("paired_host", Context.MODE_PRIVATE)

    data class Entry(
        val host: String,
        val port: Int,
        val token: ByteArray,
        val macName: String,
    ) {
        override fun equals(other: Any?): Boolean {
            if (other !is Entry) return false
            return host == other.host && port == other.port && macName == other.macName &&
                token.contentEquals(other.token)
        }

        override fun hashCode(): Int =
            ((host.hashCode() * 31 + port) * 31 + macName.hashCode()) * 31 + token.contentHashCode()
    }

    fun save(entry: Entry) {
        require(entry.token.size == TOKEN_SIZE) { "Pairing token must be $TOKEN_SIZE bytes" }
        val encryptedToken = encryptToken(entry.token)
        prefs
            .edit()
            .putString("host", entry.host)
            .putInt("port", entry.port)
            .putString("token_ciphertext", encryptedToken.ciphertext.toBase64())
            .putString("token_iv", encryptedToken.iv.toBase64())
            .putString("mac_name", entry.macName)
            .remove("token_b64")
            .apply()
    }

    fun load(): Entry? {
        val host = prefs.getString("host", null) ?: return null
        val port = prefs.getInt("port", -1).takeIf { it > 0 } ?: return null
        val macName = prefs.getString("mac_name", null) ?: "Mac"
        val ciphertextValue = prefs.getString("token_ciphertext", null)
        val ivValue = prefs.getString("token_iv", null)
        if (ciphertextValue == null || ivValue == null) {
            return migrateLegacyEntry(host, port, macName)
        }
        val ciphertext = ciphertextValue.fromBase64() ?: return null
        val iv = ivValue.fromBase64() ?: return null
        val token =
            try {
                decryptToken(EncryptedToken(ciphertext, iv))
            } catch (_: Exception) {
                // Restored preferences do not have the device-bound Keystore key.
                // Clear the unusable pairing instead of repeatedly failing.
                clear()
                return null
            }
        if (token.size != TOKEN_SIZE) {
            clear()
            return null
        }
        return Entry(host, port, token, macName)
    }

    fun clear() {
        prefs.edit().clear().apply()
    }

    private fun encryptToken(token: ByteArray): EncryptedToken {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey())
        return EncryptedToken(cipher.doFinal(token), cipher.iv)
    }

    private fun decryptToken(encrypted: EncryptedToken): ByteArray {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, getOrCreateKey(), GCMParameterSpec(GCM_TAG_BITS, encrypted.iv))
        return cipher.doFinal(encrypted.ciphertext)
    }

    private fun migrateLegacyEntry(
        host: String,
        port: Int,
        macName: String,
    ): Entry? {
        val token = prefs.getString("token_b64", null)?.fromBase64() ?: return null
        if (token.size != TOKEN_SIZE) {
            clear()
            return null
        }
        val entry = Entry(host, port, token, macName)
        return try {
            save(entry)
            entry
        } catch (_: Exception) {
            clear()
            null
        }
    }

    private fun getOrCreateKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        return KeyGenerator
            .getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
            .apply {
                init(
                    KeyGenParameterSpec
                        .Builder(
                            KEY_ALIAS,
                            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                        ).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                        .setRandomizedEncryptionRequired(true)
                        .build(),
                )
            }.generateKey()
    }

    private fun ByteArray.toBase64(): String = Base64.encodeToString(this, Base64.NO_WRAP or Base64.NO_PADDING)

    private fun String.fromBase64(): ByteArray? =
        try {
            Base64.decode(this, Base64.NO_WRAP or Base64.NO_PADDING)
        } catch (_: IllegalArgumentException) {
            null
        }

    private data class EncryptedToken(
        val ciphertext: ByteArray,
        val iv: ByteArray,
    )

    companion object {
        private const val TOKEN_SIZE = 32
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val KEY_ALIAS = "telemachus.pairing-token.v1"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val GCM_TAG_BITS = 128
    }
}
