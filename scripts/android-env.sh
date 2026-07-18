#!/bin/bash

# Shared Android toolchain and device discovery for Telemachus scripts.

android_configure_build_env() {
    if [ -n "${JAVA_HOME:-}" ] && [ ! -x "$JAVA_HOME/bin/java" ]; then
        echo "JAVA_HOME does not contain an executable Java runtime: $JAVA_HOME" >&2
        return 1
    fi

    if [ -z "${JAVA_HOME:-}" ]; then
        for candidate in \
            "/Applications/Android Studio.app/Contents/jbr/Contents/Home" \
            "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home" \
            "/usr/local/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"; do
            if [ -x "$candidate/bin/java" ]; then
                export JAVA_HOME="$candidate"
                break
            fi
        done
    fi

    if [ -z "${JAVA_HOME:-}" ] && [ "$(uname -s)" = "Darwin" ] &&
       [ -x /usr/libexec/java_home ]; then
        JAVA_17_HOME="$(/usr/libexec/java_home -v 17 2>/dev/null || true)"
        if [ -n "$JAVA_17_HOME" ]; then
            export JAVA_HOME="$JAVA_17_HOME"
        fi
    fi

    if [ -n "${JAVA_HOME:-}" ]; then
        export PATH="$JAVA_HOME/bin:$PATH"
    fi
    if ! command -v java >/dev/null 2>&1; then
        echo "JDK 17 was not found. Install Android Studio or set JAVA_HOME." >&2
        return 1
    fi

    if [ -z "${ANDROID_HOME:-}" ] && [ -n "${ANDROID_SDK_ROOT:-}" ]; then
        export ANDROID_HOME="$ANDROID_SDK_ROOT"
    fi
    if [ -z "${ANDROID_HOME:-}" ]; then
        for candidate in \
            "$HOME/Library/Android/sdk" \
            "$HOME/Android/Sdk" \
            "/opt/homebrew/share/android-commandlinetools" \
            "/usr/local/share/android-commandlinetools"; do
            if [ -d "$candidate" ]; then
                export ANDROID_HOME="$candidate"
                break
            fi
        done
    fi
    if [ -n "${ANDROID_HOME:-}" ]; then
        if [ ! -d "$ANDROID_HOME" ]; then
            echo "ANDROID_HOME does not exist: $ANDROID_HOME" >&2
            return 1
        fi
        export ANDROID_SDK_ROOT="$ANDROID_HOME"
    elif [ ! -f local.properties ]; then
        echo "Android SDK not found. Set ANDROID_HOME/ANDROID_SDK_ROOT or create local.properties." >&2
        return 1
    fi
}

adb_select_single_device() {
    local adb_bin="${ADB:-adb}"
    if ! command -v "$adb_bin" >/dev/null 2>&1; then
        echo "ADB was not found. Install Android platform tools or set ADB." >&2
        return 1
    fi

    if [ -n "${ANDROID_SERIAL:-}" ]; then
        "$adb_bin" -s "$ANDROID_SERIAL" get-state 2>/dev/null | grep -qx device || {
            echo "ANDROID_SERIAL is not an authorized device: $ANDROID_SERIAL" >&2
            return 1
        }
        printf '%s\n' "$ANDROID_SERIAL"
        return 0
    fi

    local devices count
    devices="$("$adb_bin" devices | awk 'NR > 1 && $2 == "device" { print $1 }')"
    count="$(printf '%s\n' "$devices" | awk 'NF { count++ } END { print count + 0 }')"
    if [ "$count" -ne 1 ]; then
        if [ "$count" -eq 0 ]; then
            echo "No authorized Android device was found." >&2
        else
            echo "Multiple Android devices are authorized; set ANDROID_SERIAL." >&2
            printf '%s\n' "$devices" >&2
        fi
        return 1
    fi
    printf '%s\n' "$devices"
}

adb_require_single_device() {
    ANDROID_SERIAL="$(adb_select_single_device)" || return 1
    export ANDROID_SERIAL
}

adb_cmd() {
    local adb_bin="${ADB:-adb}"
    if [ -n "${ANDROID_SERIAL:-}" ]; then
        "$adb_bin" -s "$ANDROID_SERIAL" "$@"
    else
        "$adb_bin" "$@"
    fi
}
