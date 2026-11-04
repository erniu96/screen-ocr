import AppKit

@main
struct ScreenOCRMain {
    @MainActor
    static func main() {
        let application = NSApplication.shared
        let delegate = AppDelegate()
        withExtendedLifetime(delegate) {
            application.delegate = delegate
            application.setActivationPolicy(.accessory)
            application.run()
        }
    }
}
