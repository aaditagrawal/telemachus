package dev.telemachus.display

import android.os.Looper
import android.view.WindowManager
import android.widget.TextView
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28, 35])
class ConnectionCallbacksTest {
    @Test
    fun manualUsbDisconnectRestartsChecklistPolling() {
        val controller = Robolectric.buildActivity(MainActivity::class.java).setup()
        val activity = controller.get()
        val client = StreamClient("127.0.0.1", 1)
        val checklist = MainActivity::class.java.getDeclaredField("checklistRunnable").apply { isAccessible = true }
        try {
            attach(activity, client)
            client.onConnectionStatus?.invoke(true)
            shadowOf(Looper.getMainLooper()).idle()
            assertNull(checklist.get(activity))
            MainActivity::class.java
                .getDeclaredMethod("disconnect")
                .apply { isAccessible = true }
                .invoke(activity)
            assertNotNull(checklist.get(activity))
        } finally {
            client.disconnect()
            controller.pause().stop().destroy()
        }
    }

    @Test
    fun replacedConnectionCannotChangeCurrentStatsOrStreamingSecurity() {
        val controller = Robolectric.buildActivity(MainActivity::class.java).setup()
        val activity = controller.get()
        val oldClient = StreamClient("127.0.0.1", 1)
        val currentClient = StreamClient("127.0.0.1", 1)
        try {
            attach(activity, oldClient)
            attach(activity, currentClient)
            currentClient.onConnectionStatus?.invoke(true)
            currentClient.onStats?.invoke(60.0, 5.0)
            oldClient.onConnectionStatus?.invoke(false)
            oldClient.onStats?.invoke(1.0, 1.0)
            oldClient.onServerShutdown?.invoke()
            shadowOf(Looper.getMainLooper()).idle()
            assertEquals("60.0", activity.findViewById<TextView>(R.id.fpsText).text.toString())
            assertTrue(activity.window.attributes.flags and WindowManager.LayoutParams.FLAG_SECURE != 0)
            assertTrue(!activity.isFinishing)
        } finally {
            oldClient.disconnect()
            currentClient.disconnect()
            controller.pause().stop().destroy()
        }
    }

    private fun attach(
        activity: MainActivity,
        client: StreamClient,
    ) {
        MainActivity::class.java
            .getDeclaredField("streamClient")
            .apply { isAccessible = true }
            .set(activity, client)
        MainActivity::class.java
            .getDeclaredMethod("setupStreamClientCallbacks", StreamClient::class.java)
            .apply { isAccessible = true }
            .invoke(activity, client)
    }
}
