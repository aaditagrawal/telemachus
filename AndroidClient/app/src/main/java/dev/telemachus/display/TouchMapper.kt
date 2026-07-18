package dev.telemachus.display

/**
 * Maps SurfaceView coordinates into the displayed video rectangle.
 *
 * MediaCodec's SCALE_TO_FIT mode letterboxes when the stream and tablet have
 * different aspect ratios. Normalizing against the whole SurfaceView would
 * offset every touch on the mirrored Mac display.
 */
internal object TouchMapper {
    data class Point(
        val x: Float,
        val y: Float,
    )

    fun map(
        x: Float,
        y: Float,
        viewWidth: Int,
        viewHeight: Int,
        videoWidth: Int,
        videoHeight: Int,
    ): Point {
        if (viewWidth <= 0 || viewHeight <= 0 || videoWidth <= 0 || videoHeight <= 0) {
            return Point(0f, 0f)
        }

        val surfaceWidth = viewWidth.toFloat()
        val surfaceHeight = viewHeight.toFloat()
        val videoAspect = videoWidth.toFloat() / videoHeight.toFloat()
        val surfaceAspect = surfaceWidth / surfaceHeight

        val contentWidth: Float
        val contentHeight: Float
        val offsetX: Float
        val offsetY: Float

        if (surfaceAspect > videoAspect) {
            contentHeight = surfaceHeight
            contentWidth = contentHeight * videoAspect
            offsetX = (surfaceWidth - contentWidth) / 2f
            offsetY = 0f
        } else {
            contentWidth = surfaceWidth
            contentHeight = contentWidth / videoAspect
            offsetX = 0f
            offsetY = (surfaceHeight - contentHeight) / 2f
        }

        return Point(
            x = ((x - offsetX) / contentWidth).coerceIn(0f, 1f),
            y = ((y - offsetY) / contentHeight).coerceIn(0f, 1f),
        )
    }
}
