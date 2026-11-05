# Android dependency comparison

Run from the repository with the same JDK 21 and Android SDK used for the app:

```sh
JAVA_HOME=/path/to/jdk-21 ANDROID_HOME=/path/to/android-sdk \
  bash scripts/benchmark-android-dependencies.sh --offline
```

The script reads `StreamClient.kt` from commit
`6aa289d1bd04604031eeab150314b65197ad1455` and changes only its class name.
It enables AGP's original ViewBinding generator for this benchmark build and
verifies that the layout matches that commit. Baseline sources and the extra
coroutine declaration are test-only. Ordinary app builds keep ViewBinding
disabled; other Android libraries still retain coroutine artifacts.
Benchmark outputs use `app/build/dependency-performance`, keeping temporary
baseline classes separate from ordinary app and test outputs.
The original `MainActivity` is also compiled under a benchmark-only class name
to exercise its actual ping timer and diagnostic scheduling methods.

The loopback fixture checks ordered two-pointer touch packets, connection
readiness, metadata-bearing frames at 1 KiB, 100 KiB and 1 MiB, and socket EOF
after closing a blocked receiver. Control bursts contain 1, 64 and 1,024 events.
Frame buffers are returned to each client's pool. Six warm-up pairs precede
24 measured pairs per workload, with baseline and candidate order alternating.
Connection setup is excluded from control and frame timings and measured
separately. Teardown measures socket closure, not process exit.
Reconnect samples reuse the original coroutine scope or replacement worker,
matching repeated connections within one activity.
Wireless setup includes the existing authentication request and response.
The activity comparison measures excess delay over the one-second ping period,
checks timer cancellation, and measures checklist invocation to loopback probe.
It uses two warm-up pairs and 12 measured pairs for pings, and six warm-up pairs
and 24 measured pairs for probes.

The Robolectric API 28 binding comparison uses the unchanged themed layout at
800 by 1,280 dp. It measures binding alone and full inflation plus binding.
Twelve warm-up pairs precede 40 alternating measured pairs. Binding-only
samples batch 100 operations. Output includes median, p90 and range in
microseconds. Gradle startup, compilation and dependency downloads are excluded.
Both binding results escape through the same volatile sink, matching storage
by the activity and preventing discarded-result optimizations.

These are host-side measurements of scheduling, socket I/O and view lookup.
They do not measure physical camera capture, hardware decoding, Android frame
presentation, or macOS streaming. Keep the machine load comparable between
pairs, inspect the spread, and repeat results affected by shared-host contention.
