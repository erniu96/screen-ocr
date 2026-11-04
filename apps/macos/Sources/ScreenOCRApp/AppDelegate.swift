import AppKit
import CoreGraphics
import ScreenOCRCore

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let service = OCRService()
    private let shortcut = GlobalShortcut()
    private let toast = Toast()
    private var statusItem: NSStatusItem!
    private var stateItem: NSMenuItem!
    private var shortcutItem: NSMenuItem!
    private var captureItem: NSMenuItem!
    private var cancelItem: NSMenuItem!
    private var projectItem: NSMenuItem!
    private var retryShortcutItem: NSMenuItem!
    private var projectDirectory: URL?
    private var terminating = false
    private var shortcutStatus: OSStatus = noErr
    private var smokeReport: URL?
    private var smokeStarted = Date()
    private let normalIcon = NSImage(systemSymbolName: "text.viewfinder", accessibilityDescription: "Screen OCR")

    func applicationDidFinishLaunching(_ notification: Notification) {
        configureProject()
        service.onStateChange = { [weak self] state in self?.refresh(state: state) }
        if let imagePath = argument("--smoke-image"), let reportPath = argument("--smoke-report") {
            smokeReport = URL(fileURLWithPath: reportPath)
            runSmoke(image: URL(fileURLWithPath: imagePath))
            return
        }
        buildMenu()
        shortcut.onPressed = { [weak self] in self?.startCapture() }
        registerShortcut()
        refresh(state: .idle)
        if projectDirectory == nil {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { [weak self] in self?.chooseProject() }
        }
    }

    private func argument(_ name: String) -> String? {
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else { return nil }
        return arguments[index + 1]
    }

    private func configureProject() {
        let path = argument("--project-directory") ?? UserDefaults.standard.string(forKey: "projectDirectory")
        if let path = path {
            projectDirectory = URL(fileURLWithPath: path).standardizedFileURL
            // A diagnostic launch must never change the user's saved setup.
            if argument("--smoke-image") == nil, argument("--project-directory") != nil {
                UserDefaults.standard.set(projectDirectory!.path, forKey: "projectDirectory")
            }
        }
    }

    private func menuItem(_ title: String, action: Selector? = nil, key: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        return item
    }

    private func buildMenu() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        normalIcon?.isTemplate = true
        statusItem.button?.image = normalIcon
        statusItem.button?.toolTip = "Screen OCR · ⌥S 截图识别"
        let menu = NSMenu()
        menu.autoenablesItems = false
        let heading = menuItem("Screen OCR")
        heading.isEnabled = false
        menu.addItem(heading)
        stateItem = menuItem("就绪")
        stateItem.isEnabled = false
        menu.addItem(stateItem)
        shortcutItem = menuItem("快捷键：⌥S")
        shortcutItem.isEnabled = false
        menu.addItem(shortcutItem)
        menu.addItem(.separator())
        captureItem = menuItem("截图并复制", action: #selector(startCapture), key: "s")
        captureItem.keyEquivalentModifierMask = [.option]
        menu.addItem(captureItem)
        cancelItem = menuItem("取消本次任务", action: #selector(cancelCapture))
        menu.addItem(cancelItem)
        menu.addItem(.separator())
        projectItem = menuItem("选择 OCR 项目目录…", action: #selector(chooseProject))
        menu.addItem(projectItem)
        menu.addItem(menuItem("屏幕录制权限…", action: #selector(screenPermission)))
        retryShortcutItem = menuItem("重新注册 ⌥S", action: #selector(registerShortcut))
        menu.addItem(retryShortcutItem)
        menu.addItem(.separator())
        menu.addItem(menuItem("退出 Screen OCR", action: #selector(quit), key: "q"))
        statusItem.menu = menu
    }

    private func refresh(state: OCRState) {
        guard stateItem != nil else { return }
        switch state {
        case .idle:
            stateItem.title = projectDirectory == nil ? "请选择 OCR 项目目录" : "就绪"
            statusItem.button?.title = ""
        case .capturing:
            stateItem.title = "正在框选截图…"
            statusItem.button?.title = "…"
        case .recognizing:
            stateItem.title = "正在识别…"
            statusItem.button?.title = "…"
        case .cancelling:
            stateItem.title = terminating ? "正在清理并退出…" : "正在取消…"
            statusItem.button?.title = "…"
        }
        captureItem.isEnabled = !service.isBusy && !terminating
        cancelItem.isEnabled = service.isBusy && state != .cancelling
        projectItem.isEnabled = !service.isBusy && !terminating
        projectItem.toolTip = projectDirectory?.path
        retryShortcutItem.isEnabled = shortcutStatus != noErr && !terminating
    }

    @objc private func registerShortcut() {
        shortcutStatus = shortcut.register()
        shortcutItem?.title = shortcutStatus == noErr ? "快捷键：⌥S" : "⌥S 注册失败（\(shortcutStatus)）"
        shortcutItem?.toolTip = shortcutStatus == noErr ? nil : "请停用其他应用中的 ⌥S 绑定，再重新注册。仍可通过菜单截图。"
        retryShortcutItem?.isEnabled = shortcutStatus != noErr
    }

    private func configuration() throws -> OCRConfiguration {
        guard let projectDirectory = projectDirectory else {
            throw NSError(domain: "ScreenOCR", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "请先选择已完成安装的 OCR 项目目录。"])
        }
        let configuration = OCRConfiguration(projectDirectory: projectDirectory)
        try configuration.validate()
        return configuration
    }

    @objc private func startCapture() {
        guard !service.isBusy, !terminating else { return }
        guard projectDirectory != nil else { chooseProject(); return }
        let configuration: OCRConfiguration
        do { configuration = try self.configuration() }
        catch { showError("OCR 目录尚未准备好", detail: error.localizedDescription); return }
        guard CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess() else {
            explainScreenPermission()
            return
        }
        toast.hide()
        // Let the status menu close before the system capture overlay appears.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { [weak self] in
            guard let self = self, !self.terminating, !self.service.isBusy else { return }
            self.service.capture(configuration: configuration) { [weak self] result in self?.finished(result) }
        }
    }

    @objc private func cancelCapture() { service.cancel() }

    private func finished(_ result: Result<OCRResult, Error>) {
        if terminating {
            NSApp.reply(toApplicationShouldTerminate: true)
            return
        }
        switch result {
        case .success(.text(let text)):
            guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                toast.show("没有识别到文字")
                return
            }
            NSPasteboard.general.clearContents()
            if NSPasteboard.general.setString(text, forType: .string) {
                stateItem.title = "文字已复制到剪贴板"
                toast.show("文字已复制到剪贴板")
            } else {
                stateItem.title = "剪贴板写入失败"
                showError("无法复制文字", detail: "系统剪贴板未接受识别结果，请重试。")
            }
        case .success(.empty):
            stateItem.title = "没有识别到文字"
            toast.show("没有识别到文字")
        case .success(.cancelled):
            stateItem.title = "已取消"
        case .failure(let error):
            stateItem.title = "识别失败"
            showError("识别失败", detail: error.localizedDescription)
        }
    }

    @objc private func chooseProject() {
        guard !service.isBusy else { return }
        NSApp.activate(ignoringOtherApps: true)
        let panel = NSOpenPanel()
        panel.title = "选择 OCR 项目目录"
        panel.message = "选择已完成安装并准备好运行文件的 screen-ocr 文件夹。"
        panel.prompt = "使用此目录"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.directoryURL = projectDirectory
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do { try OCRConfiguration(projectDirectory: url).validate() }
        catch { showError("这个目录尚未准备好", detail: error.localizedDescription); return }
        projectDirectory = url.standardizedFileURL
        UserDefaults.standard.set(projectDirectory!.path, forKey: "projectDirectory")
        refresh(state: .idle)
    }

    @objc private func screenPermission() {
        if CGPreflightScreenCaptureAccess() {
            toast.show("已获得屏幕录制权限")
        } else if CGRequestScreenCaptureAccess() {
            toast.show("屏幕录制权限已启用，请重试截图")
        } else {
            explainScreenPermission()
        }
    }

    private func explainScreenPermission() {
        let alert = NSAlert()
        alert.messageText = "需要允许屏幕录制"
        alert.informativeText = "请在系统设置 → 隐私与安全性 → 屏幕录制中允许 Screen OCR。如系统要求，请退出并重新打开 App，再按 ⌥S。"
        alert.addButton(withTitle: "打开系统设置")
        alert.addButton(withTitle: "稍后")
        NSApp.activate(ignoringOtherApps: true)
        if alert.runModal() == .alertFirstButtonReturn {
            NSWorkspace.shared.open(URL(fileURLWithPath: "/System/Applications/System Settings.app"))
        }
    }

    private func showError(_ title: String, detail: String) {
        toast.hide()
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = title
        alert.informativeText = detail
        alert.addButton(withTitle: "好")
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }

    @objc private func quit() { NSApp.terminate(nil) }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard service.isBusy else { return .terminateNow }
        terminating = true
        shortcut.unregister()
        toast.hide()
        service.cancel()
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        shortcut.unregister()
        toast.hide()
    }

    private func runSmoke(image: URL) {
        smokeStarted = Date()
        do {
            let configuration = try self.configuration()
            service.recognize(imageURL: image, configuration: configuration) { [weak self] result in
                self?.finishSmoke(result)
            }
        } catch { finishSmoke(.failure(error)) }
    }

    private func finishSmoke(_ result: Result<OCRResult, Error>) {
        var report: [String: Any] = ["duration_ms": Date().timeIntervalSince(smokeStarted) * 1000]
        switch result {
        case .success(.text(let text)):
            let pasteboard = NSPasteboard.withUniqueName()
            pasteboard.setString(text, forType: .string)
            report["status"] = "success"
            report["text"] = pasteboard.string(forType: .string)
            report["clipboard_matches"] = pasteboard.string(forType: .string) == text
            pasteboard.releaseGlobally()
        case .success(.empty): report["status"] = "empty"
        case .success(.cancelled): report["status"] = "cancelled"
        case .failure(let error):
            report["status"] = "error"
            report["error"] = error.localizedDescription
        }
        do {
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            if let smokeReport = smokeReport { try data.write(to: smokeReport, options: .atomic) }
        } catch { fputs("Screen OCR diagnostic report failed: \(error.localizedDescription)\n", stderr) }
        NSApp.terminate(nil)
    }
}
