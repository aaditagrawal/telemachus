package com.sidescreen.app

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class AuthHandshakeTest {
    @Test
    fun encodesGoldenBytes() {
        val token = ByteArray(32) { it.toByte() }
        val bytes = AuthHandshake.encodeRequest(token, "iPad Air")
        val expected = byteArrayOf(0x53, 0x53, 0x57, 0x41) +
            ByteArray(32) { it.toByte() } +
            byteArrayOf(8) +
            "iPad Air".toByteArray()
        assertArrayEquals(expected, bytes)
    }

    @Test
    fun rejectsNameLongerThan64() {
        val longName = "x".repeat(65)
        try {
            AuthHandshake.encodeRequest(ByteArray(32), longName)
            error("expected IllegalArgumentException")
        } catch (e: IllegalArgumentException) {
            // OK
        }
    }

    @Test
    fun rejectsTokenWrongSize() {
        try {
            AuthHandshake.encodeRequest(ByteArray(31), "x")
            error("expected IllegalArgumentException")
        } catch (e: IllegalArgumentException) {
            // OK
        }
    }

    @Test
    fun parseOKResponse() {
        val r = AuthHandshake.parseResponse(byteArrayOf(0x53, 0x53, 0x57, 0x52, 0x00))
        assertEquals(AuthHandshake.ResponseStatus.OK, r)
    }

    @Test
    fun parseInvalidTokenResponse() {
        val r = AuthHandshake.parseResponse(byteArrayOf(0x53, 0x53, 0x57, 0x52, 0x01))
        assertEquals(AuthHandshake.ResponseStatus.INVALID_TOKEN, r)
    }

    @Test
    fun parseInvalidMagicResponseReturnsNull() {
        val r = AuthHandshake.parseResponse(byteArrayOf(0x58, 0x58, 0x58, 0x58, 0x00))
        assertNull(r)
    }
}
