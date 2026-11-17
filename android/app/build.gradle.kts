plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "com.terra.g4ut"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.terra.g4ut"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
    }
    // Vaste signing-sleutel (in repo) zodat elke build als update over de vorige heen kan.
    signingConfigs {
        create("terra") {
            storeFile = rootProject.file("keystore/terra-g4ut.jks")
            storePassword = "terrag4ut"
            keyAlias = "terrag4ut"
            keyPassword = "terrag4ut"
        }
    }
    buildTypes {
        debug { signingConfig = signingConfigs.getByName("terra") }
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("terra")
        }
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    sourceSets {
        getByName("main") {
            // web-UI wordt als assets meegebakken (index.html op assets-root)
            assets.srcDirs(rootProject.layout.projectDirectory.dir("../web"))
        }
    }
}

chaquopy {
    defaultConfig {
        version = "3.12"
        pip {
            install("numpy")
            install("requests")
        }
    }
    sourceSets {
        getByName("main") {
            srcDir(rootProject.layout.projectDirectory.dir("../core"))
        }
    }
}

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.webkit:webkit:1.12.1")
}
