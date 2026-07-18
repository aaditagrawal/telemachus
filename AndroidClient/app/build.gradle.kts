plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val appVersion = rootProject.file("../VERSION").readText().trim()
val versionParts = appVersion.split(".")
val computedVersionCode = versionParts[0].toInt() * 10000 + versionParts[1].toInt() * 100 + versionParts[2].toInt()
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
    compileSdk = 34

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

    kotlinOptions {
        jvmTarget = "11"
    }

    buildFeatures {
        viewBinding = true
    }

    sourceSets {
        getByName("main").assets.srcDir(layout.buildDirectory.dir("generated/oss-notices"))
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

val syncOpenSourceNotices by tasks.registering(Sync::class) {
    from(rootProject.projectDir.parentFile) {
        include("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "licenses/Apache-2.0.txt")
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
                    appendLine("| `$group:$name:$version` | Apache License 2.0 |")
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

dependencies {
    //noinspection GradleDependency
    implementation("androidx.core:core-ktx:1.12.0")
    //noinspection GradleDependency
    implementation("androidx.appcompat:appcompat:1.6.1")
    //noinspection GradleDependency
    implementation("com.google.android.material:material:1.11.0")
    //noinspection GradleDependency
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    //noinspection GradleDependency
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.7.0")

    // Wireless mode (0.8.0)
    //noinspection GradleDependency
    implementation("androidx.camera:camera-core:1.3.1")
    //noinspection GradleDependency
    implementation("androidx.camera:camera-camera2:1.3.1")
    //noinspection GradleDependency
    implementation("androidx.camera:camera-lifecycle:1.3.1")
    //noinspection GradleDependency
    implementation("androidx.camera:camera-view:1.3.1")
    implementation("com.google.zxing:core:3.5.3")

    testImplementation("junit:junit:4.13.2")
}
