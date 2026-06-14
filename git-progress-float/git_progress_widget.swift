#!/usr/bin/env swift
import Cocoa
import Foundation

let widgetWidth: CGFloat = 330
let widgetHeight: CGFloat = 116
let pollSeconds: TimeInterval = 0.25
let doneVisibleSeconds: TimeInterval = 4.0

let executablePath = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
let appDir = executablePath.deletingLastPathComponent()
let resultsDir = appDir.appendingPathComponent("results")
let statusPath = resultsDir.appendingPathComponent("git_progress_status.json")
let positionPath = resultsDir.appendingPathComponent("git_progress_widget_position.json")

struct Theme {
    let accent: NSColor
    let strip: NSColor
    let bg: NSColor
    let text: NSColor
    let muted: NSColor
    let track: NSColor
}

let blueTheme = Theme(
    accent: NSColor(hex: "#58a6ff"),
    strip: NSColor(hex: "#172338"),
    bg: NSColor(hex: "#1b1f24"),
    text: NSColor(hex: "#f8fafc"),
    muted: NSColor(hex: "#a9b0ba"),
    track: NSColor(hex: "#30363d")
)
let greenTheme = Theme(
    accent: NSColor(hex: "#35c46f"),
    strip: NSColor(hex: "#1f3328"),
    bg: NSColor(hex: "#1b1f24"),
    text: NSColor(hex: "#f8fafc"),
    muted: NSColor(hex: "#a9b0ba"),
    track: NSColor(hex: "#30363d")
)
let redTheme = Theme(
    accent: NSColor(hex: "#ef4444"),
    strip: NSColor(hex: "#3b1f22"),
    bg: NSColor(hex: "#1b1f24"),
    text: NSColor(hex: "#f8fafc"),
    muted: NSColor(hex: "#a9b0ba"),
    track: NSColor(hex: "#30363d")
)

struct GitProgressState {
    var visible = false
    var command = "git"
    var repo = ""
    var phase = "Waiting"
    var detail = ""
    var percent: Double?
    var speed = ""
    var state = "idle"
    var updatedAt = 0.0
    var finishedAt: Double?
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

func optionalString(_ value: Any?) -> String? {
    value as? String
}

func optionalDouble(_ value: Any?) -> Double? {
    if let doubleValue = value as? Double { return doubleValue }
    if let intValue = value as? Int { return Double(intValue) }
    if let stringValue = value as? String { return Double(stringValue) }
    return nil
}

final class ProgressView: NSView {
    var state = GitProgressState() {
        didSet { needsDisplay = true }
    }
    var dragOffset = NSPoint.zero
    var pulse = 0.0 {
        didSet { needsDisplay = true }
    }

    override var isFlipped: Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        let theme = currentTheme()
        let bounds = self.bounds

        theme.bg.setFill()
        NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 1), xRadius: 16, yRadius: 16).fill()
        theme.strip.setFill()
        NSBezierPath(rect: NSRect(x: 2, y: 2, width: bounds.width - 4, height: 28)).fill()
        theme.accent.setStroke()
        let border = NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 1), xRadius: 16, yRadius: 16)
        border.lineWidth = 2
        border.stroke()

        theme.accent.setFill()
        NSBezierPath(ovalIn: NSRect(x: 14, y: 11, width: 10, height: 10)).fill()
        drawText("GIT", rect: NSRect(x: 32, y: 8, width: 34, height: 15), size: 10, weight: .bold, color: theme.text)
        drawText(state.command.uppercased(), rect: NSRect(x: 70, y: 8, width: 78, height: 15), size: 9, weight: .bold, color: theme.muted)
        drawText(state.repo, rect: NSRect(x: 156, y: 8, width: 154, height: 15), size: 9, weight: .bold, color: theme.muted, alignment: .right)

        drawText(state.phase, rect: NSRect(x: 16, y: 42, width: 160, height: 18), size: 13, weight: .bold, color: theme.text)
        let percentText = state.percent.map { "\(Int($0.rounded()))%" } ?? "--"
        drawText(percentText, rect: NSRect(x: 206, y: 42, width: 44, height: 18), size: 13, weight: .bold, color: theme.accent, alignment: .right)
        drawText(state.speed.isEmpty ? "--/s" : state.speed, rect: NSRect(x: 256, y: 42, width: 58, height: 18), size: 11, weight: .bold, color: theme.muted, alignment: .right)

        drawProgressBar(theme: theme)
        drawText(state.detail, rect: NSRect(x: 16, y: 88, width: 296, height: 16), size: 9, weight: .regular, color: theme.muted)
    }

    private func currentTheme() -> Theme {
        if state.state == "failed" { return redTheme }
        if state.state == "done" { return greenTheme }
        return blueTheme
    }

    private func drawProgressBar(theme: Theme) {
        let rect = NSRect(x: 16, y: 68, width: bounds.width - 32, height: 10)
        theme.track.setFill()
        NSBezierPath(roundedRect: rect, xRadius: 5, yRadius: 5).fill()
        theme.accent.setFill()
        if let percent = state.percent {
            let width = max(4, rect.width * CGFloat(max(0, min(100, percent)) / 100.0))
            NSBezierPath(roundedRect: NSRect(x: rect.minX, y: rect.minY, width: width, height: rect.height), xRadius: 5, yRadius: 5).fill()
        } else {
            let segmentWidth = rect.width * 0.28
            let x = rect.minX + CGFloat(pulse) * (rect.width - segmentWidth)
            NSBezierPath(roundedRect: NSRect(x: x, y: rect.minY, width: segmentWidth, height: rect.height), xRadius: 5, yRadius: 5).fill()
        }
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
            // Position persistence is best-effort.
        }
    }
}

final class AppController: NSObject, NSApplicationDelegate {
    private let view = ProgressView(frame: NSRect(x: 0, y: 0, width: widgetWidth, height: widgetHeight))
    private var window: NSPanel?
    private var timer: Timer?
    private var pulseTimer: Timer?
    private var pulseDirection = 1.0

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        try? FileManager.default.createDirectory(at: resultsDir, withIntermediateDirectories: true)
        buildWindow()
        poll()
        timer = Timer.scheduledTimer(withTimeInterval: pollSeconds, repeats: true) { [weak self] _ in
            self?.poll()
        }
        pulseTimer = Timer.scheduledTimer(withTimeInterval: 0.04, repeats: true) { [weak self] _ in
            self?.advancePulse()
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
        panel.title = "Git Progress"
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.contentView = view
        panel.ignoresMouseEvents = false
        panel.acceptsMouseMovedEvents = true
        panel.orderOut(nil)
        window = panel
    }

    private func initialOrigin() -> NSPoint {
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        var x = Int(screen.maxX - widgetWidth - 80)
        var yFromTop = 86
        if let data = try? Data(contentsOf: positionPath),
           let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            x = Int(optionalDouble(payload["x"]) ?? Double(x))
            yFromTop = Int(optionalDouble(payload["y"]) ?? Double(yFromTop))
        }
        let clampedX = min(max(Int(screen.minX), x), Int(screen.maxX - widgetWidth))
        let appKitY = Int(screen.maxY) - yFromTop - Int(widgetHeight)
        let clampedY = min(max(Int(screen.minY), appKitY), Int(screen.maxY - widgetHeight))
        return NSPoint(x: clampedX, y: clampedY)
    }

    private func poll() {
        guard let state = loadState() else {
            window?.orderOut(nil)
            return
        }
        view.state = state
        if state.visible {
            window?.orderFrontRegardless()
        } else {
            window?.orderOut(nil)
        }
    }

    private func loadState() -> GitProgressState? {
        guard let data = try? Data(contentsOf: statusPath),
              let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return nil
        }
        let now = Date().timeIntervalSince1970
        let active = payload["active"] as? Bool ?? false
        let stateName = optionalString(payload["state"]) ?? "idle"
        let updatedAt = optionalDouble(payload["updated_at"]) ?? 0
        let finishedAt = optionalDouble(payload["finished_at"])
        let recentlyFinished = !active && (stateName == "done" || stateName == "failed") && finishedAt.map { now - $0 < doneVisibleSeconds } == true
        let freshActive = active && now - updatedAt < 30
        let visible = freshActive || recentlyFinished
        return GitProgressState(
            visible: visible,
            command: optionalString(payload["command"]) ?? "git",
            repo: optionalString(payload["repo"]) ?? "",
            phase: optionalString(payload["phase"]) ?? "Working",
            detail: optionalString(payload["detail"]) ?? "",
            percent: optionalDouble(payload["percent"]),
            speed: optionalString(payload["speed"]) ?? "",
            state: stateName,
            updatedAt: updatedAt,
            finishedAt: finishedAt
        )
    }

    private func advancePulse() {
        var next = view.pulse + 0.018 * pulseDirection
        if next >= 1 {
            next = 1
            pulseDirection = -1
        } else if next <= 0 {
            next = 0
            pulseDirection = 1
        }
        view.pulse = next
    }
}

let app = NSApplication.shared
let delegate = AppController()
app.delegate = delegate
app.run()
