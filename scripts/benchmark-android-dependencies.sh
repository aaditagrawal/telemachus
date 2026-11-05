#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE=6aa289d1bd04604031eeab150314b65197ad1455
FIXTURES="$(mktemp -d)"
trap 'rm -rf "$FIXTURES"' EXIT

# The baseline is committed source, never the original checkout's working tree.
git -C "$ROOT" show "$BASE:AndroidClient/app/src/main/java/dev/telemachus/display/StreamClient.kt" |
    sed 's/StreamClient/BaselineStreamClient/g' > "$FIXTURES/BaselineStreamClient.kt"
git -C "$ROOT" show "$BASE:AndroidClient/app/src/main/java/dev/telemachus/display/MainActivity.kt" |
    sed 's/class MainActivity :/class BaselineMainActivity :/' > "$FIXTURES/BaselineMainActivity.kt"
git -C "$ROOT" show "$BASE:AndroidClient/app/src/main/res/layout/activity_main.xml" > "$FIXTURES/activity_main.xml"
cmp "$FIXTURES/activity_main.xml" "$ROOT/AndroidClient/app/src/main/res/layout/activity_main.xml"
cp "$ROOT/scripts/benchmarks/android-dependencies/"*.kt "$FIXTURES/"

# Generate the real baseline binding only in this benchmark build. Production
# builds do not enable ViewBinding or add these baseline-only test sources.
cat > "$FIXTURES/benchmark.gradle" <<'GRADLE'
gradle.beforeProject { project ->
    if (project.path == ':app') {
        project.pluginManager.withPlugin('com.android.application') {
            project.layout.buildDirectory.set(project.layout.projectDirectory.dir('build/dependency-performance'))
            def android = project.extensions.getByName('android')
            android.buildFeatures.viewBinding = true
            project.tasks.matching { it.name == 'compileDebugUnitTestKotlin' }.configureEach {
                source(System.getenv('TELEMACHUS_BENCHMARK_FIXTURES'))
            }
            project.dependencies.add('testImplementation', 'org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0')
            project.tasks.withType(Test).configureEach {
                testLogging.showStandardStreams = true
                outputs.upToDateWhen { false }
            }
        }
    }
}
GRADLE

export TELEMACHUS_BENCHMARK_FIXTURES="$FIXTURES"
cd "$ROOT/AndroidClient"
source "$ROOT/scripts/android-env.sh"
android_configure_build_env
./gradlew --no-daemon --max-workers=2 --no-parallel -I "$FIXTURES/benchmark.gradle" \
    -Pkotlin.compiler.execution.strategy=in-process \
    testDebugUnitTest --tests '*DependencyPerformanceTest' "$@"
