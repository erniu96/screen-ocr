// Generate a reproducible, non-private OCR fixture using macOS system fonts.
import AppKit

let output = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "tests/fixtures/chinese.png"
let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 1000, pixelsHigh: 300,
    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
NSColor.white.setFill()
NSRect(x: 0, y: 0, width: 1000, height: 300).fill()
let attributes: [NSAttributedString.Key: Any] = [
    .font: NSFont(name: "PingFangSC-Regular", size: 44)!,
    .foregroundColor: NSColor.black,
]
for (index, text) in ["微信文字识别测试", "macOS 原生 OCR", "订单金额 123.45 元"].enumerated() {
    (text as NSString).draw(at: NSPoint(x: 40, y: 220 - index * 80), withAttributes: attributes)
}
NSGraphicsContext.restoreGraphicsState()
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: output))
