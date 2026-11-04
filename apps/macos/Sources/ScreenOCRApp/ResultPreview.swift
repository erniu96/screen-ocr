import AppKit

/// Shows the complete result without taking focus from the user's paste destination.
@MainActor
final class ResultPreview {
    private var panel: NSPanel?

    func show(_ text: String) {
        hide()
        let mouse = NSEvent.mouseLocation
        let screen = NSScreen.screens.first { NSMouseInRect(mouse, $0.frame, false) } ?? NSScreen.main
        guard let screen = screen else { return }

        let visibleFrame = screen.visibleFrame.insetBy(dx: 20, dy: 20)
        let width = min(520, visibleFrame.width)
        let window = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: width, height: 100),
            styleMask: [.titled, .closable, .resizable, .nonactivatingPanel],
            backing: .buffered, defer: false
        )
        window.title = "识别结果 · 已复制到剪贴板"
        window.isReleasedWhenClosed = false
        window.isRestorable = false
        window.isFloatingPanel = true
        window.becomesKeyOnlyIfNeeded = true
        window.hidesOnDeactivate = false
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient]

        let textView = NSTextView(frame: NSRect(x: 0, y: 0, width: width, height: 100))
        textView.isEditable = false
        textView.isSelectable = true
        textView.isRichText = false
        textView.font = .systemFont(ofSize: 15)
        textView.textColor = .textColor
        textView.backgroundColor = .textBackgroundColor
        textView.textContainerInset = NSSize(width: 16, height: 14)
        textView.isVerticallyResizable = true
        textView.isHorizontallyResizable = false
        textView.autoresizingMask = [.width]
        textView.minSize = .zero
        textView.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        textView.textContainer?.widthTracksTextView = true
        textView.textContainer?.containerSize = NSSize(width: max(1, width - 32), height: CGFloat.greatestFiniteMagnitude)
        textView.textContainer?.lineFragmentPadding = 0
        textView.string = text

        var textHeight: CGFloat = 100
        if let container = textView.textContainer, let layout = textView.layoutManager {
            layout.ensureLayout(for: container)
            textHeight = ceil(layout.usedRect(for: container).height) + 28
        }
        let titleBarHeight = window.frame.height - window.contentRect(forFrameRect: window.frame).height
        let maximumHeight = max(1, min(380, visibleFrame.height - titleBarHeight))
        let height = min(max(100, textHeight), maximumHeight)

        let scrollView = NSScrollView(frame: NSRect(x: 0, y: 0, width: width, height: height))
        scrollView.hasVerticalScroller = true
        scrollView.hasHorizontalScroller = false
        scrollView.autohidesScrollers = true
        scrollView.scrollerStyle = .overlay
        scrollView.documentView = textView
        window.contentView = scrollView
        window.setContentSize(NSSize(width: width, height: height))
        textView.setFrameSize(NSSize(width: scrollView.contentSize.width, height: max(height, textHeight)))
        window.contentMinSize = NSSize(width: min(280, width), height: min(100, height))
        window.maxSize = visibleFrame.size
        window.setFrameTopLeftPoint(NSPoint(x: visibleFrame.midX - window.frame.width / 2,
                                           y: visibleFrame.maxY))
        textView.scrollRangeToVisible(NSRange(location: 0, length: 0))
        panel = window
        // Ordering a nonactivating panel front leaves keyboard focus in the current app.
        window.orderFrontRegardless()
    }

    func hide() {
        panel?.orderOut(nil)
        panel = nil
    }
}
