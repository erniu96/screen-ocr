import AppKit

let directory = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let bitmap = NSBitmapImageRep(
            bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0
        )!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        let transform = NSAffineTransform()
        transform.scale(by: CGFloat(pixels) / 1024)
        transform.concat()
        NSColor(calibratedRed: 0.21, green: 0.34, blue: 0.88, alpha: 1).setFill()
        NSBezierPath(roundedRect: NSRect(x: 48, y: 48, width: 928, height: 928), xRadius: 210, yRadius: 210).fill()
        NSColor.white.setStroke()
        let corners = NSBezierPath()
        corners.lineWidth = 50
        corners.lineCapStyle = .round
        corners.lineJoinStyle = .round
        let points: [(CGFloat, CGFloat, CGFloat, CGFloat)] = [
            (242, 242, 1, 1), (782, 242, -1, 1), (242, 782, 1, -1), (782, 782, -1, -1)
        ]
        for (x, y, dx, dy) in points {
            corners.move(to: NSPoint(x: x + dx * 120, y: y))
            corners.line(to: NSPoint(x: x, y: y))
            corners.line(to: NSPoint(x: x, y: y + dy * 120))
        }
        corners.stroke()
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 350, weight: .semibold),
            .foregroundColor: NSColor.white,
        ]
        let letter = "A" as NSString
        let bounds = letter.size(withAttributes: attributes)
        letter.draw(at: NSPoint(x: (1024 - bounds.width) / 2, y: (1024 - bounds.height) / 2), withAttributes: attributes)
        NSGraphicsContext.restoreGraphicsState()
        let suffix = scale == 2 ? "@2x" : ""
        try bitmap.representation(using: .png, properties: [:])!.write(
            to: directory.appendingPathComponent("icon_\(size)x\(size)\(suffix).png")
        )
    }
}
