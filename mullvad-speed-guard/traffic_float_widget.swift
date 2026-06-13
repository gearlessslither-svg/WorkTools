#!/usr/bin/env swift
import Cocoa
import Foundation

let widgetWidth: CGFloat = 274
let widgetHeight: CGFloat = 124
let pollSeconds: TimeInterval = 1.25
let panelTrafficSampleSeconds = 2.0
let slowThresholdMbps = 5.0

let executablePath = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
let appDir = executablePath.deletingLastPathComponent()
let resultsDir = appDir.appendingPathComponent("results")
let positionPath = resultsDir.appendingPathComponent("float_widget_position.json")
let logPath = resultsDir.appendingPathComponent("float_widget.log")
let panelStateURL = URL(string: ProcessInfo.processInfo.environment["MSG_PANEL_STATE_URL"] ?? "http://127.0.0.1:18790/api/state")!
let panelOpenURL = URL(string: ProcessInfo.processInfo.environment["MSG_PANEL_OPEN_URL"] ?? "http://127.0.0.1:18790/")!

struct Theme {
    let accent: NSColor
    let strip: NSColor
    let bg: NSColor
    let text: NSColor
    let muted: NSColor
}

let themes: [String: Theme] = [
    "red": Theme(
        accent: NSColor(hex: "#ef4444"),
        strip: NSColor(hex: "#3b1f22"),
        bg: NSColor(hex: "#1b1e22"),
        text: NSColor(hex: "#f8fafc"),
        muted: NSColor(hex: "#a9b0ba")
    ),
    "yellow": Theme(
        accent: NSColor(hex: "#f2b84b"),
        strip: NSColor(hex: "#3a2d1c"),
        bg: NSColor(hex: "#1b1e22"),
        text: NSColor(hex: "#f8fafc"),
        muted: NSColor(hex: "#a9b0ba")
    ),
    "green": Theme(
        accent: NSColor(hex: "#35c46f"),
        strip: NSColor(hex: "#1f3328"),
        bg: NSColor(hex: "#1b1e22"),
        text: NSColor(hex: "#f8fafc"),
        muted: NSColor(hex: "#a9b0ba")
    ),
]

struct WidgetState {
    var status = "red"
    var stateText = "PANEL OFF"
    var downloadTotal: Int?
    var uploadTotal: Int?
    var currentMbps: Double?
    var sampledAt = ""
}

extension NSColor {
    convenience init(hex: String) {
        let clean = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        var value: UInt64 = 0
        Scanner(string: clean).scanHexInt64(&value)
        let r = CGFloat((value >> 16) & 0xff) / 255.0
        let g = CGFloat((value >> 8) & 0xff) / 255.0
        let b = CGFloat(value & 0xff) / 255.0
        self.init(calibratedRed: r, green: g, blue: b, alpha: 1.0)
    }
}

func log(_ message: String) {
    do {
        try FileManager.default.createDirectory(at: resultsDir, withIntermediateDirectories: true)
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        let line = "[\(formatter.string(from: Date()))] \(message)\n"
        if FileManager.default.fileExists(atPath: logPath.path) {
            let handle = try FileHandle(forWritingTo: logPath)
            try handle.seekToEnd()
            try handle.write(contentsOf: Data(line.utf8))
            try handle.close()
        } else {
            try line.write(to: logPath, atomically: true, encoding: .utf8)
        }
    } catch {
        // Logging must never take down the widget.
    }
}

func formatBytes(_ value: Int?) -> String {
    guard let value else { return "--" }
    let units = ["B", "KB", "MB", "GB", "TB", "PB"]
    var size = Double(max(0, value))
    for unit in units {
        if size < 1024 || unit == units.last {
            if unit == "B" {
                return String(format: "%.0f %@", size, unit)
            }
            if size < 10 {
                return String(format: "%.1f %@", size, unit)
            }
            return String(format: "%.0f %@", size, unit)
        }
        size /= 1024
    }
    return "--"
}

func formatMbps(_ value: Double?) -> String {
    guard let value else { return "--" }
    if value >= 100 {
        return String(format: "%.0f", value)
    }
    if value >= 10 {
        return String(format: "%.1f", value)
    }
    return String(format: "%.2f", value)
}

func optionalInt(_ value: Any?) -> Int? {
    if let intValue = value as? Int {
        return intValue
    }
    if let doubleValue = value as? Double {
        return Int(doubleValue)
    }
    if let stringValue = value as? String {
        return Int(stringValue)
    }
    return nil
}

func optionalString(_ value: Any?) -> String? {
    if let stringValue = value as? String {
        return stringValue
    }
    return nil
}

final class TrafficSampler {
    private var lastRateMbps: Double?

    func fetch() throws -> WidgetState {
        var request = URLRequest(url: panelStateURL)
        request.timeoutInterval = 0.9
        request.setValue("mullvad-speed-guard-float/2.0", forHTTPHeaderField: "User-Agent")

        let semaphore = DispatchSemaphore(value: 0)
        var resultData: Data?
        var resultError: Error?
        let task = URLSession.shared.dataTask(with: request) { data, _, error in
            resultData = data
            resultError = error
            semaphore.signal()
        }
        task.resume()
        if semaphore.wait(timeout: .now() + 1.2) == .timedOut {
            task.cancel()
            throw NSError(domain: "FloatWidget", code: 1, userInfo: [NSLocalizedDescriptionKey: "panel timeout"])
        }
        if let resultError {
            throw resultError
        }
        guard let resultData else {
            throw NSError(domain: "FloatWidget", code: 2, userInfo: [NSLocalizedDescriptionKey: "empty panel response"])
        }
        let json = try JSONSerialization.jsonObject(with: resultData)
        guard let root = json as? [String: Any] else {
            throw NSError(domain: "FloatWidget", code: 3, userInfo: [NSLocalizedDescriptionKey: "invalid panel response"])
        }
        return compute(root)
    }

    private func compute(_ root: [String: Any]) -> WidgetState {
        let connection = root["connection"] as? [String: Any] ?? [:]
        let traffic = root["traffic"] as? [String: Any] ?? [:]
        let state = optionalString(connection["state"]) ?? "Unknown"
        let sampledAt = optionalString(traffic["sampled_at"]) ?? currentTimestamp()
        let downloadTotal = optionalInt(traffic["download_bytes"]) ?? 0
        let uploadTotal = optionalInt(traffic["upload_bytes"]) ?? 0
        let ok = (traffic["ok"] as? Bool ?? false) && state.lowercased().hasPrefix("connected")
        let downDelta = optionalInt(traffic["last_delta_download_bytes"])
        let upDelta = optionalInt(traffic["last_delta_upload_bytes"])

        var currentMbps: Double?
        if let downDelta, let upDelta {
            let downMbps = Double(max(0, downDelta)) * 8.0 / panelTrafficSampleSeconds / 1_000_000.0
            let upMbps = Double(max(0, upDelta)) * 8.0 / panelTrafficSampleSeconds / 1_000_000.0
            currentMbps = downMbps + upMbps
            lastRateMbps = currentMbps
        } else {
            currentMbps = lastRateMbps
        }

        var status: String
        var stateText: String
        if !ok {
            status = "red"
            stateText = state.lowercased().hasPrefix("connected") ? "NO TUNNEL" : "DISCONNECTED"
            currentMbps = nil
        } else if currentMbps == nil || currentMbps! < slowThresholdMbps {
            status = "yellow"
            stateText = "SLOW"
        } else {
            status = "green"
            stateText = "FAST"
        }

        return WidgetState(
            status: status,
            stateText: stateText,
            downloadTotal: downloadTotal,
            uploadTotal: uploadTotal,
            currentMbps: currentMbps,
            sampledAt: sampledAt
        )
    }

    private func currentTimestamp() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return formatter.string(from: Date())
    }
}

final class WidgetView: NSView {
    var state = WidgetState() {
        didSet {
            needsDisplay = true
        }
    }
    var dragOffset = NSPoint.zero

    override var isFlipped: Bool {
        true
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        let theme = themes[state.status] ?? themes["red"]!
        let bounds = self.bounds

        theme.bg.setFill()
        NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 1), xRadius: 18, yRadius: 18).fill()
        theme.strip.setFill()
        NSBezierPath(rect: NSRect(x: 2, y: 2, width: bounds.width - 4, height: 26)).fill()
        theme.accent.setStroke()
        let border = NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 1), xRadius: 18, yRadius: 18)
        border.lineWidth = 2
        border.stroke()

        theme.accent.setFill()
        NSBezierPath(ovalIn: NSRect(x: 12, y: 10, width: 10, height: 10)).fill()

        drawText("VPN", rect: NSRect(x: 31, y: 7, width: 34, height: 16), size: 10, weight: .bold, color: theme.text)
        drawText(state.stateText, rect: NSRect(x: 74, y: 7, width: 96, height: 16), size: 9, weight: .bold, color: theme.muted)
        let timeText = String(state.sampledAt.suffix(8))
        drawText(timeText.isEmpty ? "--:--:--" : timeText, rect: NSRect(x: 184, y: 7, width: 76, height: 16), size: 9, weight: .bold, color: theme.muted, alignment: .right)

        drawText("NOW", rect: NSRect(x: 16, y: 43, width: 70, height: 13), size: 9, weight: .bold, color: theme.muted)
        let speedText = state.status == "red" ? "--" : formatMbps(state.currentMbps)
        drawText(speedText, rect: NSRect(x: 16, y: 56, width: 92, height: 40), size: 30, weight: .bold, color: theme.accent)
        drawText("Mbps", rect: NSRect(x: 105, y: 75, width: 48, height: 16), size: 10, weight: .bold, color: theme.muted)

        drawText("DOWN", rect: NSRect(x: 166, y: 43, width: 88, height: 13), size: 9, weight: .bold, color: theme.muted)
        drawText(formatBytes(state.downloadTotal), rect: NSRect(x: 166, y: 58, width: 94, height: 19), size: 13, weight: .bold, color: theme.text)
        drawText("UP", rect: NSRect(x: 166, y: 82, width: 88, height: 13), size: 9, weight: .bold, color: theme.muted)
        drawText(formatBytes(state.uploadTotal), rect: NSRect(x: 166, y: 96, width: 94, height: 19), size: 13, weight: .bold, color: theme.text)
    }

    private func drawText(
        _ text: String,
        rect: NSRect,
        size: CGFloat,
        weight: NSFont.Weight,
        color: NSColor,
        alignment: NSTextAlignment = .left
    ) {
        let style = NSMutableParagraphStyle()
        style.alignment = alignment
        style.lineBreakMode = .byTruncatingTail
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: size, weight: weight),
            .foregroundColor: color,
            .paragraphStyle: style,
        ]
        (text as NSString).draw(in: rect, withAttributes: attributes)
    }

    override func mouseDown(with event: NSEvent) {
        if event.clickCount >= 2 {
            NSWorkspace.shared.open(panelOpenURL)
            return
        }
        guard let window else { return }
        let mouse = NSEvent.mouseLocation
        dragOffset = NSPoint(x: mouse.x - window.frame.origin.x, y: mouse.y - window.frame.origin.y)
    }

    override func mouseDragged(with event: NSEvent) {
        guard let window else { return }
        let mouse = NSEvent.mouseLocation
        var origin = NSPoint(x: mouse.x - dragOffset.x, y: mouse.y - dragOffset.y)
        if let frame = NSScreen.main?.visibleFrame {
            origin.x = min(max(frame.minX, origin.x), frame.maxX - widgetWidth)
            origin.y = min(max(frame.minY, origin.y), frame.maxY - widgetHeight)
        }
        window.setFrameOrigin(origin)
    }

    override func mouseUp(with event: NSEvent) {
        savePosition()
    }

    override func rightMouseDown(with event: NSEvent) {
        NSWorkspace.shared.open(panelOpenURL)
    }

    private func savePosition() {
        guard let window, let screen = NSScreen.main else { return }
        let topLeftY = max(0, Int(screen.visibleFrame.maxY - window.frame.origin.y - widgetHeight))
        let payload: [String: Int] = [
            "x": max(0, Int(window.frame.origin.x)),
            "y": topLeftY,
        ]
        do {
            try FileManager.default.createDirectory(at: resultsDir, withIntermediateDirectories: true)
            let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: positionPath)
        } catch {
            log("position save failed: \(error.localizedDescription)")
        }
    }
}

final class AppController: NSObject, NSApplicationDelegate {
    private let sampler = TrafficSampler()
    private let view = WidgetView(frame: NSRect(x: 0, y: 0, width: widgetWidth, height: widgetHeight))
    private var window: NSPanel?
    private var timer: Timer?
    private var fetchInFlight = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        try? FileManager.default.createDirectory(at: resultsDir, withIntermediateDirectories: true)
        NSApp.setActivationPolicy(.accessory)
        buildWindow()
        fetchNow()
        timer = Timer.scheduledTimer(withTimeInterval: pollSeconds, repeats: true) { [weak self] _ in
            self?.fetchNow()
        }
    }

    private func buildWindow() {
        let origin = initialOrigin()
        let panel = NSPanel(
            contentRect: NSRect(x: origin.x, y: origin.y, width: widgetWidth, height: widgetHeight),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.title = "VPN Traffic"
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.contentView = view
        panel.ignoresMouseEvents = false
        panel.acceptsMouseMovedEvents = true
        panel.orderFrontRegardless()
        window = panel
    }

    private func initialOrigin() -> NSPoint {
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        var x = Int(screen.minX + 80)
        var yFromTop = 80
        if let data = try? Data(contentsOf: positionPath),
           let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            x = optionalInt(payload["x"]) ?? x
            yFromTop = optionalInt(payload["y"]) ?? yFromTop
        }
        let clampedX = min(max(Int(screen.minX), x), Int(screen.maxX - widgetWidth))
        let appKitY = Int(screen.maxY) - yFromTop - Int(widgetHeight)
        let clampedY = min(max(Int(screen.minY), appKitY), Int(screen.maxY - widgetHeight))
        return NSPoint(x: clampedX, y: clampedY)
    }

    private func fetchNow() {
        if fetchInFlight {
            return
        }
        fetchInFlight = true
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self else { return }
            do {
                let state = try self.sampler.fetch()
                DispatchQueue.main.async {
                    self.fetchInFlight = false
                    self.view.state = state
                    self.window?.orderFrontRegardless()
                }
            } catch {
                log("refresh failed: \(error.localizedDescription)")
                DispatchQueue.main.async {
                    self.fetchInFlight = false
                    var state = self.view.state
                    state.status = "red"
                    state.stateText = "PANEL OFF"
                    state.currentMbps = nil
                    state.sampledAt = String(currentClockSuffix())
                    self.view.state = state
                    self.window?.orderFrontRegardless()
                }
            }
        }
    }
}

func currentClockSuffix() -> String {
    let formatter = DateFormatter()
    formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
    return formatter.string(from: Date())
}

let app = NSApplication.shared
let delegate = AppController()
app.delegate = delegate
app.run()
