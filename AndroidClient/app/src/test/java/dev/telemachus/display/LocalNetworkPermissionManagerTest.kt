package dev.telemachus.display

import android.Manifest
import android.app.Activity
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [37])
class LocalNetworkPermissionManagerTest {
    @Test
    fun `wireless connection waits for local network permission and resumes only once`() {
        val controller = Robolectric.buildActivity(Activity::class.java).setup()
        try {
            val activity = controller.get()
            val manager = LocalNetworkPermissionManager(activity)
            var connections = 0
            assertTrue(manager.requestIfNeeded { connections++ })
            val request = requireNotNull(shadowOf(activity).lastRequestedPermission)
            assertEquals(LocalNetworkPermissionManager.REQUEST_CODE, request.requestCode)
            assertArrayEquals(arrayOf(Manifest.permission.ACCESS_LOCAL_NETWORK), request.requestedPermissions)
            assertEquals(0, connections)
            manager.onPermissionResult(true)
            manager.onPermissionResult(true)
            assertEquals(1, connections)
        } finally {
            controller.pause().stop().destroy()
        }
    }

    @Test
    fun `denial discards the pending connection and permits a later retry`() {
        val controller = Robolectric.buildActivity(Activity::class.java).setup()
        try {
            val manager = LocalNetworkPermissionManager(controller.get())
            var connections = 0
            assertTrue(manager.requestIfNeeded { connections++ })
            manager.onPermissionResult(false)
            manager.onPermissionResult(true)
            assertEquals(0, connections)
            assertTrue(manager.requestIfNeeded { connections++ })
            manager.onPermissionResult(true)
            assertEquals(1, connections)
        } finally {
            controller.pause().stop().destroy()
        }
    }

    @Test
    @Config(sdk = [35])
    fun `older Android versions connect without a new permission request`() {
        val controller = Robolectric.buildActivity(Activity::class.java).setup()
        try {
            val activity = controller.get()
            assertFalse(LocalNetworkPermissionManager(activity).requestIfNeeded { error("No deferred connection") })
            assertNull(shadowOf(activity).lastRequestedPermission)
        } finally {
            controller.pause().stop().destroy()
        }
    }
}
