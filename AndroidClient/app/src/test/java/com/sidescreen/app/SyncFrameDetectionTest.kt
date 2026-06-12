package com.sidescreen.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SyncFrameDetectionTest {
    /** Annex-B frame: each NAL gets a 4-byte start code + header byte + dummy payload. */
    private fun annexB(vararg nalHeaderBytes: Int): ByteArray {
        val out = mutableListOf<Byte>()
        for (header in nalHeaderBytes) {
            out.addAll(listOf<Byte>(0, 0, 0, 1))
            out.add(header.toByte())
            out.addAll(listOf<Byte>(0x11, 0x22, 0x33)) // dummy payload
        }
        return out.toByteArray()
    }

    @Test
    fun hevcIdrIsSync() {
        // HEVC NAL header 0x26: type = (0x26 and 0x7E) shr 1 = 19 (IDR_W_RADL)
        val frame = annexB(0x26)
        assertTrue(StreamClient.isSyncFrame(frame, frame.size, isHevc = true))
    }

    @Test
    fun hevcTrailFrameIsNotSync() {
        // HEVC NAL header 0x02: type 1 (TRAIL_R)
        val frame = annexB(0x02)
        assertFalse(StreamClient.isSyncFrame(frame, frame.size, isHevc = true))
    }

    @Test
    fun h264IdrIsSync() {
        // H.264 NAL header 0x65: type = 0x65 and 0x1F = 5 (IDR slice)
        val frame = annexB(0x65)
        assertTrue(StreamClient.isSyncFrame(frame, frame.size, isHevc = false))
    }

    @Test
    fun h264NonIdrIsNotSync() {
        // H.264 NAL header 0x41: type 1 (non-IDR slice)
        val frame = annexB(0x41)
        assertFalse(StreamClient.isSyncFrame(frame, frame.size, isHevc = false))
    }

    @Test
    fun h264IdrAfterSpsPpsIsSync() {
        // Typical keyframe layout: SPS (0x67), PPS (0x68), then IDR (0x65)
        val frame = annexB(0x67, 0x68, 0x65)
        assertTrue(StreamClient.isSyncFrame(frame, frame.size, isHevc = false))
    }

    @Test
    fun h264IdrIsNotMistakenForHevcSync() {
        // 0x65 parsed as HEVC: (0x65 and 0x7E) shr 1 = 50 — outside 16..21.
        // This is exactly the bug that would freeze H.264 streams if the
        // detection stayed HEVC-only.
        val frame = annexB(0x65)
        assertFalse(StreamClient.isSyncFrame(frame, frame.size, isHevc = true))
    }
}
