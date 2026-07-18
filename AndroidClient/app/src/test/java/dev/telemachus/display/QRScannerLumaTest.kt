package dev.telemachus.display

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test
import java.nio.ByteBuffer

class QRScannerLumaTest {
    @Test
    fun packsPaddedPlaneAndRotatesClockwise() {
        val source =
            ByteBuffer.wrap(
                byteArrayOf(
                    1,
                    2,
                    3,
                    99,
                    4,
                    5,
                    6,
                    99,
                ),
            )

        val image =
            QRScannerActivity.packAndRotateLuma(
                source = source,
                width = 3,
                height = 2,
                rowStride = 4,
                pixelStride = 1,
                rotationDegrees = 90,
            )

        assertEquals(2, image.width)
        assertEquals(3, image.height)
        assertArrayEquals(byteArrayOf(4, 1, 5, 2, 6, 3), image.bytes)
    }

    @Test
    fun rotatesCounterClockwise() {
        val image =
            QRScannerActivity.rotateLuma(
                source = byteArrayOf(1, 2, 3, 4, 5, 6),
                width = 3,
                height = 2,
                rotationDegrees = 270,
            )

        assertEquals(2, image.width)
        assertEquals(3, image.height)
        assertArrayEquals(byteArrayOf(3, 6, 2, 5, 1, 4), image.bytes)
    }
}
