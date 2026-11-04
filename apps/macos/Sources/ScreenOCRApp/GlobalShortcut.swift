import AppKit
import Carbon

/// Owns a single exclusive system shortcut. All calls stay on the main thread.
final class GlobalShortcut {
    private var hotKey: EventHotKeyRef?
    private var eventHandler: EventHandlerRef?
    var onPressed: (() -> Void)?

    @discardableResult
    func register() -> OSStatus {
        unregister()
        var specification = EventTypeSpec(
            eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed)
        )
        let callback: EventHandlerUPP = { _, event, context in
            guard let event = event, let context = context else { return OSStatus(eventNotHandledErr) }
            var identifier = EventHotKeyID()
            let result = GetEventParameter(
                event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                nil, MemoryLayout<EventHotKeyID>.size, nil, &identifier
            )
            guard result == noErr, identifier.signature == 0x534F4352, identifier.id == 1 else {
                return OSStatus(eventNotHandledErr)
            }
            let owner = Unmanaged<GlobalShortcut>.fromOpaque(context).takeUnretainedValue()
            owner.onPressed?()
            return noErr
        }
        let installed = InstallEventHandler(
            GetApplicationEventTarget(), callback, 1, &specification,
            Unmanaged.passUnretained(self).toOpaque(), &eventHandler
        )
        guard installed == noErr else { return installed }
        let result = RegisterEventHotKey(
            UInt32(kVK_ANSI_S), UInt32(optionKey), EventHotKeyID(signature: 0x534F4352, id: 1),
            GetApplicationEventTarget(), UInt32(kEventHotKeyExclusive), &hotKey
        )
        if result != noErr { unregister() }
        return result
    }

    func unregister() {
        if let hotKey = hotKey { UnregisterEventHotKey(hotKey) }
        if let eventHandler = eventHandler { RemoveEventHandler(eventHandler) }
        hotKey = nil
        eventHandler = nil
    }
}
