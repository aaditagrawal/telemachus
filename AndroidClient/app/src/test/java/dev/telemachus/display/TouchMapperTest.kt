package dev.telemachus.display

import org.junit.Assert.assertEquals
import org.junit.Test

class TouchMapperTest {
    @Test
    fun `maps center through horizontal letterbox`() {
        val point =
            TouchMapper.map(
                x = 1000f,
                y = 600f,
                viewWidth = 2000,
                viewHeight = 1200,
                videoWidth = 2000,
                videoHeight = 1124,
            )

        assertEquals(0.5f, point.x, 0.0001f)
        assertEquals(0.5f, point.y, 0.0001f)
    }

    @Test
    fun `maps picture edges rather than black bars`() {
        val contentTop = (1200f - 1124f) / 2f
        val top = TouchMapper.map(1000f, contentTop, 2000, 1200, 2000, 1124)
        val bottom = TouchMapper.map(1000f, contentTop + 1124f, 2000, 1200, 2000, 1124)

        assertEquals(0f, top.y, 0.0001f)
        assertEquals(1f, bottom.y, 0.0001f)
    }

    @Test
    fun `clamps touches in black bars to video bounds`() {
        val above = TouchMapper.map(1000f, 0f, 2000, 1200, 2000, 1124)
        val below = TouchMapper.map(1000f, 1200f, 2000, 1200, 2000, 1124)

        assertEquals(0f, above.y, 0.0001f)
        assertEquals(1f, below.y, 0.0001f)
    }

    @Test
    fun `handles pillarboxing`() {
        val point = TouchMapper.map(500f, 500f, 2000, 1000, 1000, 1000)

        assertEquals(0f, point.x, 0.0001f)
        assertEquals(0.5f, point.y, 0.0001f)
    }
}
