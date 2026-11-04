// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ScreenOCR",
    platforms: [.macOS(.v13)],
    products: [.executable(name: "ScreenOCR", targets: ["ScreenOCRApp"])],
    targets: [
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
    ],
    swiftLanguageModes: [.v5]
)
