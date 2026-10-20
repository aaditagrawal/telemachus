// Top-level build file
plugins {
    id("com.android.application") version "9.4.1" apply false
}

// Keep AGP's built-in Kotlin compiler on the current stable Kotlin release.
buildscript {
    repositories { mavenCentral() }
    dependencies { classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:2.4.21") }
}
