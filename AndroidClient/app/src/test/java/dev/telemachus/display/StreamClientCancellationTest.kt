package dev.telemachus.display

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.ServerSocket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class StreamClientCancellationTest {
    @Test
    fun disconnectCancelsPendingWirelessHandshake() =
        runBlocking {
            ServerSocket(0).use { server ->
                val accepted = CountDownLatch(1)
                val serverJob =
                    async(Dispatchers.IO) {
                        server.accept().use {
                            accepted.countDown()
                            it.getInputStream().read(ByteArray(256))
                            Thread.sleep(2_000)
                        }
                    }
                val client = StreamClient("127.0.0.1", server.localPort)
                val connectJob =
                    async(Dispatchers.IO) {
                        runCatching {
                            client.connectWireless(ByteArray(32), "test-device")
                        }
                    }

                assertTrue(withContext(Dispatchers.IO) { accepted.await(1, TimeUnit.SECONDS) })
                client.disconnect()
                withTimeout(1_000) {
                    connectJob.await()
                }
                serverJob.cancel()
            }
        }
}
