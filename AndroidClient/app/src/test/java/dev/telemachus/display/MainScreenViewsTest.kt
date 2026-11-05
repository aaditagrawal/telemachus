package dev.telemachus.display

import android.view.ViewGroup
import android.widget.TextView
import androidx.constraintlayout.widget.ConstraintLayout
import org.junit.Assert.assertSame
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28, 35])
class MainScreenViewsTest {
    @Test
    fun duplicateIdsUseTheFirstDepthFirstMatch() {
        val controller = Robolectric.buildActivity(MainActivity::class.java).setup()
        val activity = controller.get()
        try {
            val root = activity.findViewById<ViewGroup>(android.R.id.content).getChildAt(0) as ConstraintLayout
            val first = TextView(activity).apply { id = R.id.fpsText }
            root.addView(first, 0)
            assertSame(first, root.findViewById<TextView>(R.id.fpsText))
            assertSame(first, MainScreenViews(root).fpsText)
        } finally {
            controller.pause().stop().destroy()
        }
    }
}
