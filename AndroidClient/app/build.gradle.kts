plugins {
    id("com.android.application")
}

val appVersion = providers.environmentVariable("TELEMACHUS_VERSION").getOrElse("0.0.0")
val versionParts = appVersion.split(".")
require(versionParts.size == 3 && versionParts.all { part -> part.toIntOrNull() != null }) {
    "TELEMACHUS_VERSION must be a semantic version, got '$appVersion'."
}
val computedVersionCode =
    100_000 +
        (
            versionParts[0].toInt() * 10000 +
                versionParts[1].toInt() * 100 +
                versionParts[2].toInt()
        )
val releaseStoreFile = providers.environmentVariable("TELEMACHUS_KEYSTORE_FILE")
val releaseStorePassword = providers.environmentVariable("TELEMACHUS_KEYSTORE_PASSWORD")
val releaseKeyAlias = providers.environmentVariable("TELEMACHUS_KEY_ALIAS")
val releaseKeyPassword = providers.environmentVariable("TELEMACHUS_KEY_PASSWORD")
val releaseSigningConfigured =
    listOf(releaseStoreFile, releaseStorePassword, releaseKeyAlias, releaseKeyPassword)
        .all { it.isPresent && it.get().isNotBlank() }
val releasePackagingRequested =
    gradle.startParameter.taskNames.any {
        it.substringAfterLast(":") == "assembleRelease" || it.substringAfterLast(":") == "bundleRelease"
    }

if (releasePackagingRequested && !releaseSigningConfigured) {
    throw GradleException(
        "Release signing is not configured. Set TELEMACHUS_KEYSTORE_FILE, " +
            "TELEMACHUS_KEYSTORE_PASSWORD, TELEMACHUS_KEY_ALIAS, and " +
            "TELEMACHUS_KEY_PASSWORD.",
    )
}

android {
    namespace = "dev.telemachus.display"
    compileSdk = 37

    // AGP reads this experimental property (values: "4k" | "16k" | "64k").
    // The obsolete gradle.properties key android.nativeLibrary.alignment is ignored.
    experimentalProperties["android.nativeLibraryAlignmentPageSize"] = "16k"

    defaultConfig {
        applicationId = "dev.telemachus.display"
        minSdk = 26
        //noinspection OldTargetApi
        targetSdk = 34 // Match the currently installed SDK; API compatibility is covered in CI.
        versionCode = computedVersionCode
        versionName = appVersion
    }

    signingConfigs {
        if (releaseSigningConfigured) {
            create("release") {
                storeFile = file(releaseStoreFile.get())
                storePassword = releaseStorePassword.get()
                keyAlias = releaseKeyAlias.get()
                keyPassword = releaseKeyPassword.get()
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (releaseSigningConfigured) signingConfigs.getByName("release") else null
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }

    buildFeatures {
        viewBinding = true
    }

    sourceSets {
        getByName("main").assets.srcDir(layout.buildDirectory.dir("generated/oss-notices").get().asFile)
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
        unitTests.isIncludeAndroidResources = true
    }
}

val syncOpenSourceNotices by tasks.registering(Sync::class) {
    from(rootProject.projectDir.parentFile) {
        include("LICENSE", "NOTICE", "licenses/Apache-2.0.txt")
    }
    into(layout.buildDirectory.dir("generated/oss-notices"))
}

val generateReleaseDependencyLicenses by tasks.registering {
    val outputFile =
        layout.buildDirectory.file(
            "generated/dependency-license-report/ANDROID_RUNTIME_DEPENDENCY_LICENSES.md",
        )
    outputs.file(outputFile)

    doLast {
        val permittedApacheGroups =
            listOf(
                "androidx.",
                "com.google.android.material",
                "com.google.auto.value",
                "com.google.errorprone",
                "com.google.guava",
                "com.google.zxing",
                "org.jetbrains",
            )
        // Reviewed from upstream license metadata and bundled notices.
        val reviewedLicenses = mapOf(
            "com.google.dagger:dagger" to "Apache License 2.0",
            "com.google.j2objc:j2objc-annotations" to "Apache License 2.0",
            "jakarta.inject:jakarta.inject-api" to "Apache License 2.0",
            "javax.inject:javax.inject" to "Apache License 2.0",
            "org.jspecify:jspecify" to "Apache License 2.0",
        )
        val dependencies =
            configurations
                .getByName("releaseRuntimeClasspath")
                .resolvedConfiguration
                .resolvedArtifacts
                .map { artifact ->
                    val id = artifact.moduleVersion.id
                    Triple(id.group, artifact.name, id.version)
                }.distinct()
                .sortedWith(compareBy({ it.first }, { it.second }, { it.third }))
        val unknown =
            dependencies.filter { dependency ->
                "${dependency.first}:${dependency.second}" !in reviewedLicenses &&
                    permittedApacheGroups.none { prefix -> dependency.first.startsWith(prefix) }
            }
        check(unknown.isEmpty()) {
            "Review and classify new runtime dependency licenses: " +
                unknown.joinToString { "${it.first}:${it.second}:${it.third}" }
        }

        val report =
            buildString {
                appendLine("# Android Runtime Dependency Licenses")
                appendLine()
                appendLine("Generated from `releaseRuntimeClasspath`; test-only dependencies are excluded.")
                appendLine()
                appendLine("| Dependency | License |")
                appendLine("| --- | --- |")
                dependencies.forEach { (group, name, version) ->
                    val license = reviewedLicenses["$group:$name"] ?: "Apache License 2.0"
                    appendLine("| `$group:$name:$version` | $license |")
                }
                appendLine()
                appendLine("See `licenses/Apache-2.0.txt` for the complete license text.")
            }
        val destination = outputFile.get().asFile
        destination.parentFile.mkdirs()
        destination.writeText(report)
    }
}

syncOpenSourceNotices {
    dependsOn(generateReleaseDependencyLicenses)
    from(generateReleaseDependencyLicenses.map { it.outputs.files.singleFile })
}

tasks.named("preBuild").configure {
    dependsOn(syncOpenSourceNotices)
}

tasks
    .matching { it.name == "assembleRelease" || it.name == "bundleRelease" }
    .configureEach {
        doFirst {
            check(releaseSigningConfigured) {
                "Release signing is not configured. Set TELEMACHUS_KEYSTORE_FILE, " +
                    "TELEMACHUS_KEYSTORE_PASSWORD, TELEMACHUS_KEY_ALIAS, and " +
                    "TELEMACHUS_KEY_PASSWORD."
            }
        }
    }

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_11)
    }
}

dependencies {
    constraints {
        implementation("com.google.guava:guava:33.7.2-android") {
            because("CVE-2026-102554 fixes unsafe collection deserialization in CameraX's transitive Guava dependency")
        }
    }

    //noinspection GradleDependency
    implementation("androidx.core:core-ktx:1.19.1")
    //noinspection GradleDependency
    implementation("androidx.appcompat:appcompat:1.8.0")
    //noinspection GradleDependency
    implementation("com.google.android.material:material:1.14.0")
    //noinspection GradleDependency
    implementation("androidx.constraintlayout:constraintlayout:2.2.2")
    //noinspection GradleDependency
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.11.0")

    // Wireless mode (0.8.0)
    // CameraX 1.4.0+ ships a 16 KB-aligned libimage_processing_util_jni.so.
    // 1.3.x triggers Android 15+ "LOAD segment not aligned" warnings.
    //noinspection GradleDependency
    implementation("androidx.camera:camera-core:1.6.2")
    //noinspection GradleDependency
    implementation("androidx.camera:camera-camera2:1.6.2")
    //noinspection GradleDependency
    implementation("androidx.camera:camera-lifecycle:1.6.2")
    //noinspection GradleDependency
    implementation("androidx.camera:camera-view:1.6.2")
    implementation("com.google.zxing:core:3.5.4")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.robolectric:robolectric:4.17")
}
