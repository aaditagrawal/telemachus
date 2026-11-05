package dev.telemachus.display

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class ConnectionWorkerTest {
    @Test
    fun closingOneOwnerDoesNotStopAnotherOwner() {
        val closed = ConnectionWorker<Int> { }
        val active = ConnectionWorker<Int> { }
        val started = CountDownLatch(1)
        try {
            closed.close()
            active.start(1) { started.countDown() }
            assertTrue(started.await(1, TimeUnit.SECONDS))
        } finally {
            closed.close()
            active.close()
        }
    }

    @Test
    fun replacingConnectionClosesOldClientBeforeStartingNextAndInterruptsItsWorker() {
        val firstStarted = CountDownLatch(1)
        val firstClosed = CountDownLatch(1)
        val secondStarted = CountDownLatch(1)
        val interrupted = CountDownLatch(1)
        val worker = ConnectionWorker<Int> { if (it == 1) firstClosed.countDown() }
        try {
            worker.start(1) {
                firstStarted.countDown()
                try {
                    CountDownLatch(1).await()
                } catch (_: InterruptedException) {
                    interrupted.countDown()
                }
            }
            assertTrue(firstStarted.await(1, TimeUnit.SECONDS))
            worker.start(2) {
                assertEquals(0L, firstClosed.count)
                secondStarted.countDown()
            }
            assertTrue(secondStarted.await(1, TimeUnit.SECONDS))
            assertTrue(interrupted.await(1, TimeUnit.SECONDS))
        } finally {
            worker.close()
        }
    }

    @Test
    fun closedWorkerClosesLateConnectionsWithoutStartingThem() {
        val closes = AtomicInteger()
        val starts = AtomicInteger()
        val worker = ConnectionWorker<Int> { closes.incrementAndGet() }
        worker.close()
        worker.start(1) { starts.incrementAndGet() }
        assertEquals(1, closes.get())
        assertEquals(0, starts.get())
    }

    @Test
    fun canceledUninterruptibleResolutionDoesNotDelayReplacement() {
        val started = CountDownLatch(1)
        val release = CountDownLatch(1)
        val finished = CountDownLatch(1)
        val secondStarted = CountDownLatch(1)
        val firstClosed = CountDownLatch(1)
        val worker = ConnectionWorker<Int> { if (it == 1) firstClosed.countDown() }
        try {
            worker.start(1) {
                started.countDown()
                // InetAddress resolution may keep running after cancellation.
                while (release.count > 0) {
                    try {
                        release.await()
                    } catch (_: InterruptedException) {
                    }
                }
                finished.countDown()
            }
            assertTrue(started.await(1, TimeUnit.SECONDS))
            worker.cancel()
            worker.start(2) {
                assertEquals(0L, firstClosed.count)
                secondStarted.countDown()
            }
            assertTrue(secondStarted.await(1, TimeUnit.SECONDS))
            assertEquals(1L, release.count)
        } finally {
            release.countDown()
            worker.close()
            assertTrue(finished.await(1, TimeUnit.SECONDS))
        }
    }
}
