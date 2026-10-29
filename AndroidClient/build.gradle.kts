// Keep AGP built-in Kotlin on the current compiler release.
buildscript {
    repositories {
        mavenCentral()
    }
    dependencies {
        classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:2.4.21")
    }
}

// Top-level build file
plugins {
    id("com.android.application") version "9.5.0" apply false
}
