package com.sidescreen.app

object AuthHandshake {
    private val REQ_MAGIC = byteArrayOf(0x53, 0x53, 0x57, 0x41) // "SSWA"
    private val RES_MAGIC = byteArrayOf(0x53, 0x53, 0x57, 0x52) // "SSWR"

    enum class ResponseStatus(val code: Byte) {
        OK(0x00),
        INVALID_TOKEN(0x01),
        INVALID_MAGIC(0x02),
        INVALID_NAME(0x03),
        ;

        companion object {
            fun forCode(code: Byte): ResponseStatus? = values().firstOrNull { it.code == code }
        }
    }

    /**
     * Build the wire format request:
     *   [magic 4][token 32][name_len 1][name N]
     */
    fun encodeRequest(
        token: ByteArray,
        deviceName: String,
    ): ByteArray {
        require(token.size == 32) { "token must be 32 bytes, got ${token.size}" }
        val nameBytes = deviceName.toByteArray(Charsets.UTF_8)
        require(nameBytes.size in 1..64) { "deviceName UTF-8 length must be 1..64, got ${nameBytes.size}" }
        return REQ_MAGIC + token + byteArrayOf(nameBytes.size.toByte()) + nameBytes
    }

    /**
     * Parse the 5-byte response. Returns null if magic is wrong or buffer is malformed.
     */
    fun parseResponse(bytes: ByteArray): ResponseStatus? {
        if (bytes.size < 5) return null
        for (i in 0..3) if (bytes[i] != RES_MAGIC[i]) return null
        return ResponseStatus.forCode(bytes[4])
    }
}
