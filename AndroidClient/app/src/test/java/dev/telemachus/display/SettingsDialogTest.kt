package dev.telemachus.display

import android.app.Dialog
import android.graphics.Rect
import android.os.Looper
import android.os.SystemClock
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.ScrollView
import com.google.android.material.slider.Slider
import com.google.android.material.switchmaterial.SwitchMaterial
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.android.controller.ActivityController
import org.robolectric.annotation.Config
import org.robolectric.annotation.LooperMode
import org.robolectric.shadows.ShadowDialog

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28, 35], qualifiers = "w800dp-h1280dp-xhdpi")
@LooperMode(LooperMode.Mode.PAUSED)
class SettingsDialogTest {
    private lateinit var controller: ActivityController<MainActivity>
    private lateinit var activity: MainActivity
    private lateinit var dialog: Dialog
    private lateinit var decor: View
    private var downTime = 0L

    @Before
    fun setUp() {
        controller = Robolectric.buildActivity(MainActivity::class.java).setup()
        activity = controller.get()
        openDialog()
    }

    @After
    fun tearDown() {
        dialog.dismiss()
        controller.pause().stop().destroy()
    }

    @Test
    fun `outside taps on every side dismiss after consuming the entire tap`() {
        val card = bounds(dialog.findViewById(R.id.settingsCard))
        val points =
            listOf(
                card.centerX() to card.top - 1,
                card.centerX() to card.bottom + 1,
                card.left - 1 to card.centerY(),
                card.right + 1 to card.centerY(),
                decor.width / 2 to 1,
                decor.width / 2 to decor.height - 2,
            )

        for ((x, y) in points) {
            assertTrue(dispatch(MotionEvent.ACTION_DOWN, x, y))
            assertTrue(dialog.isShowing)
            assertTrue(dispatch(MotionEvent.ACTION_UP, x, y))
            assertFalse("Outside tap at $x,$y should dismiss", dialog.isShowing)
            openDialog()
        }
    }

    @Test
    fun `dialog covers the full screen and keeps touches modal`() {
        val window = requireNotNull(dialog.window)
        assertEquals(WindowManager.LayoutParams.MATCH_PARENT, window.attributes.width)
        assertEquals(WindowManager.LayoutParams.MATCH_PARENT, window.attributes.height)
        assertEquals(0, window.attributes.flags and WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL)
        assertEquals(decor.width, dialog.findViewById<View>(R.id.settingsScrim).width)
        assertEquals(decor.height, dialog.findViewById<View>(R.id.settingsScrim).height)
    }

    @Test
    fun `blank card space and the heading do not dismiss`() {
        val card = bounds(dialog.findViewById(R.id.settingsCard))
        tap(card.left + 2, card.centerY())
        assertTrue(dialog.isShowing)
        tap(card.centerX(), card.top + 80)
        assertTrue(dialog.isShowing)
    }

    @Test
    fun `toggle position buttons and slider still work without dismissing`() {
        val toggle = dialog.findViewById<SwitchMaterial>(R.id.showStatsSwitch)
        val checked = toggle.isChecked
        tap(toggle)
        assertEquals(!checked, toggle.isChecked)
        assertEquals(!checked, PreferencesManager(activity).showStatsOverlay)

        tap(dialog.findViewById(R.id.cornerTopLeft))
        assertEquals(3, PreferencesManager(activity).settingsButtonCorner)

        val slider = dialog.findViewById<Slider>(R.id.opacitySlider)
        val sliderBounds = bounds(slider)
        val originalValue = slider.value
        tap(sliderBounds.left + sliderBounds.width() / 3, sliderBounds.centerY())
        assertTrue(slider.value < originalValue)
        assertEquals(slider.value, PreferencesManager(activity).overlayOpacity, 0.0001f)
        assertTrue(dialog.isShowing)
    }

    @Test
    @Config(qualifiers = "w1000dp-h600dp-land-xhdpi")
    fun `landscape content scrolls and a drag leaving the card does not dismiss`() {
        val card = dialog.findViewById<androidx.cardview.widget.CardView>(R.id.settingsCard)
        val scroll = card.getChildAt(0) as ScrollView
        val cardBounds = bounds(card)
        val x = cardBounds.centerX()
        val startY = cardBounds.bottom - 80
        assertTrue(dispatch(MotionEvent.ACTION_DOWN, x, startY))
        assertTrue(dispatch(MotionEvent.ACTION_MOVE, x, cardBounds.top + 80))
        assertTrue(dispatch(MotionEvent.ACTION_MOVE, x, cardBounds.top - 10))
        assertTrue(dispatch(MotionEvent.ACTION_UP, x, cardBounds.top - 10))
        assertTrue(scroll.scrollY > 0)
        assertTrue(dialog.isShowing)
    }

    @Test
    fun `panel stays within the screen when an open dialog is resized`() {
        layoutDialog(decor.height, decor.width)
        val container = dialog.findViewById<View>(R.id.settingsPanelContainer)
        val card = bounds(dialog.findViewById(R.id.settingsCard))
        assertEquals(decor.height * 0.85f, container.height.toFloat(), 1f)
        assertTrue(card.top > 0)
        assertTrue(card.bottom < decor.height)
        assertTrue(dialog.isShowing)

        tap(card.centerX(), card.top - 1)
        assertFalse(dialog.isShowing)
    }

    @Test
    fun `done and back still dismiss`() {
        tap(dialog.findViewById(R.id.closeButton))
        assertFalse(dialog.isShowing)
        openDialog()
        @Suppress("DEPRECATION")
        dialog.onBackPressed()
        assertFalse(dialog.isShowing)
    }

    private fun openDialog() {
        activity.findViewById<View>(R.id.settingsButton).performClick()
        dialog = requireNotNull(ShadowDialog.getLatestDialog())
        decor = requireNotNull(dialog.window).decorView
        val metrics = activity.resources.displayMetrics
        layoutDialog(metrics.widthPixels, metrics.heightPixels)
    }

    private fun layoutDialog(
        width: Int,
        height: Int,
    ) {
        decor.measure(
            View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(height, View.MeasureSpec.EXACTLY),
        )
        decor.layout(0, 0, decor.measuredWidth, decor.measuredHeight)
    }

    private fun bounds(view: View): Rect {
        val rect = Rect(0, 0, view.width, view.height)
        (decor as android.view.ViewGroup).offsetDescendantRectToMyCoords(view, rect)
        return rect
    }

    private fun tap(view: View) {
        val rect = bounds(view)
        tap(rect.centerX(), rect.centerY())
    }

    private fun tap(
        x: Int,
        y: Int,
    ) {
        assertTrue(dispatch(MotionEvent.ACTION_DOWN, x, y))
        assertTrue(dispatch(MotionEvent.ACTION_UP, x, y))
    }

    private fun dispatch(
        action: Int,
        x: Int,
        y: Int,
    ): Boolean {
        val time = SystemClock.uptimeMillis()
        if (action == MotionEvent.ACTION_DOWN) downTime = time
        val event = MotionEvent.obtain(downTime, time, action, x.toFloat(), y.toFloat(), 0)
        return try {
            val consumed = dialog.dispatchTouchEvent(event)
            shadowOf(Looper.getMainLooper()).idle()
            consumed
        } finally {
            event.recycle()
        }
    }
}
