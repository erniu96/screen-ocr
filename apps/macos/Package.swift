// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ScreenOCR",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "ScreenOCR", targets: ["ScreenOCRApp"]),
        .executable(name: "ScreenOCRWorker", targets: ["ScreenOCRWorker"]),
    ],
    targets: [
        .target(name: "CMojoBridge", publicHeadersPath: "include"),
        .target(name: "ScreenOCRRuntime"),
        .executableTarget(name: "ScreenOCRWorker", dependencies: ["ScreenOCRRuntime", "CMojoBridge"]),
        .target(name: "ScreenOCRCore"),
        .executableTarget(
            name: "ScreenOCRApp",
            dependencies: ["ScreenOCRCore"],
            linkerSettings: [
                .linkedFramework("AppKit"),
                .linkedFramework("Carbon"),
                .linkedFramework("CoreGraphics"),
            ]
        ),
        .testTarget(name: "ScreenOCRCoreTests", dependencies: ["ScreenOCRCore"]),
        .testTarget(name: "ScreenOCRRuntimeTests", dependencies: ["ScreenOCRRuntime"]),
    ],
    swiftLanguageModes: [.v5]
)
