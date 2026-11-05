package dev.telemachus.display

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.ServerSocket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class StreamClientCancellationTest {
    @Test
    fun disconnectCancelsPendingWirelessHandshake() {
        ServerSocket(0).use { server ->
            val executor = Executors.newSingleThreadExecutor()
            val client = StreamClient("127.0.0.1", server.localPort)
            try {
                val connect = executor.submit { runCatching { client.connectWireless(ByteArray(32), "test-device") } }
                server.accept().use { peer ->
                    peer.soTimeout = 1_000
                    assertTrue(peer.getInputStream().read(ByteArray(256)) > 0)
                    client.disconnect()
                    connect.get(1, TimeUnit.SECONDS)
                    assertEquals(-1, peer.getInputStream().read())
                }
            } finally {
                client.disconnect()
                executor.shutdownNow()
            }
        }
    }

    @Test
    fun disconnectCancelsReceiveAndRejectsSubsequentConnectAndWrites() {
        ServerSocket(0).use { server ->
            val executor = Executors.newSingleThreadExecutor()
            val connected = CountDownLatch(1)
            val client = StreamClient("127.0.0.1", server.localPort)
            client.onConnectionStatus = { if (it) connected.countDown() }
            try {
                val connect = executor.submit { client.connect() }
                server.accept().use { peer ->
                    peer.soTimeout = 1_000
                    assertTrue(connected.await(1, TimeUnit.SECONDS))
                    // Drain startup capability bytes before checking EOF.
                    assertTrue(peer.getInputStream().read(ByteArray(3)) > 0)
                    client.disconnect()
                    connect.get(1, TimeUnit.SECONDS)
                    while (peer.getInputStream().read() >= 0) { }
                    client.sendPing()
                    client.sendTouch(0.5f, 0.5f, 0)
                    client.requestKeyframe(force = true)
                }
                var reconnected = false
                client.onConnectionStatus = { if (it) reconnected = true }
                client.connect()
                assertFalse(reconnected)
            } finally {
                client.disconnect()
                executor.shutdownNow()
            }
        }
    }
}
