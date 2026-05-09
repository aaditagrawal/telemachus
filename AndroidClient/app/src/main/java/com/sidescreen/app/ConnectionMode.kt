package com.sidescreen.app

enum class ConnectionMode {
    USB,
    WIRELESS,
    ;

    companion object {
        fun fromName(name: String?): ConnectionMode =
            values().firstOrNull { it.name == name } ?: USB
    }
}
