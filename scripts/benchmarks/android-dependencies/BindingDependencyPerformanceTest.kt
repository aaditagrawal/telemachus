package dev.telemachus.display

import android.app.Activity
import android.view.LayoutInflater
import android.view.View
import androidx.constraintlayout.widget.ConstraintLayout
import dev.telemachus.display.databinding.ActivityMainBinding
import org.junit.Assert.assertSame
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28], qualifiers = "w800dp-h1280dp-xhdpi")
class BindingDependencyPerformanceTest {
    @Volatile private var sink: Any? = null

    @Test
    fun compareBindingAndInflation() {
        val controller = Robolectric.buildActivity(Activity::class.java)
        val activity = controller.get()
        activity.setTheme(R.style.AppTheme)
        controller.setup()
        try {
            val inflater = LayoutInflater.from(activity)
            val root = inflater.inflate(R.layout.activity_main, null) as ConstraintLayout
            assertSame(ActivityMainBinding.bind(root).surfaceView, MainScreenViews(root).surfaceView)
            val original = ActivityMainBinding.bind(root)
            val replacement = MainScreenViews(root)
            MainScreenViews::class.java.declaredFields
                .filter { it.name != "root" && android.view.View::class.java.isAssignableFrom(it.type) }
                .forEach { field ->
                    field.isAccessible = true
                    assertSame(original.javaClass.getField(field.name).get(original), field.get(replacement))
                }
            for (inflate in listOf(false, true)) {
                fun run(candidate: Boolean): Double {
                    val started = System.nanoTime()
                    repeat(if (inflate) 1 else 100) {
                        sink =
                            if (inflate) {
                                if (candidate) {
                                    MainScreenViews(View.inflate(activity, R.layout.activity_main, null) as ConstraintLayout)
                                } else {
                                    ActivityMainBinding.inflate(inflater)
                                }
                            } else if (candidate) {
                                MainScreenViews(root)
                            } else {
                                ActivityMainBinding.bind(root)
                            }
                    }
                    return (System.nanoTime() - started) / 1000.0 / if (inflate) 1 else 100
                }
                repeat(12) {
                    run(false)
                    run(true)
                }
                val baseline = ArrayList<Double>()
                val candidate = ArrayList<Double>()
                repeat(40) { index ->
                    if (index % 2 == 0) {
                        baseline.add(run(false))
                        candidate.add(run(true))
                    } else {
                        candidate.add(run(true))
                        baseline.add(run(false))
                    }
                }

                fun summary(samples: List<Double>): String {
                    val sorted = samples.sorted()
                    return "median=${(sorted[19] + sorted[20]) / 2},p90=${sorted[35]},min=${sorted.first()},max=${sorted.last()}"
                }
                println("PERF binding-inflate=$inflate us/op samples=40 baseline=${summary(baseline)} candidate=${summary(candidate)}")
            }
        } finally {
            sink = null
            controller.pause().stop().destroy()
        }
    }
}
