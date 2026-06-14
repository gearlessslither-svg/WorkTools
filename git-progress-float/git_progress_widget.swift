#!/usr/bin/env swift
import Cocoa
import Darwin
import Foundation

let widgetWidth: CGFloat = 360
let widgetHeight: CGFloat = 174
let maxVisibleTasks = 3
let pollSeconds: TimeInterval = 0.25
let doneVisibleSeconds: TimeInterval = 4.0
let activeVisibleSeconds: TimeInterval = 10 * 60

let executablePath = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
let appDir = executablePath.deletingLastPathComponent()
let resultsDir = appDir.appendingPathComponent("results")
let tasksDir = resultsDir.appendingPathComponent("tasks")
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

struct GitProgressTask {
    var id = ""
    var command = "git"
    var repo = ""
    var phase = "Working"
    var detail = ""
    var percent: Double?
    var speed = ""
    var state = "running"
    var active = false
    var updatedAt = 0.0
    var finishedAt: Double?
    var pid: Int?
    var visible = false
}

struct GitProgressSnapshot {
    var visible = false
    var tasks: [GitProgressTask] = []
    var activeCount = 0
    var totalVisibleCount = 0
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

func optionalInt(_ value: Any?) -> Int? {
    if let intValue = value as? Int { return intValue }
    if let doubleValue = value as? Double { return Int(doubleValue) }
    if let stringValue = value as? String { return Int(stringValue) }
    return nil
}

func processExists(_ pid: Int?) -> Bool {
    guard let pid, pid > 0 else { return false }
    let result = Darwin.kill(pid_t(pid), 0)
    return result == 0 || errno == EPERM
}

final class ProgressView: NSView {
    var snapshot = GitProgressSnapshot() {
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
        NSBezierPath(rect: NSRect(x: 2, y: 2, width: bounds.width - 4, height: 30)).fill()
        theme.accent.setStroke()
        let border = NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 1), xRadius: 16, yRadius: 16)
        border.lineWidth = 2
        border.stroke()

        drawHeader(theme: theme)
        for (index, task) in snapshot.tasks.prefix(maxVisibleTasks).enumerated() {
            drawTask(task, index: index, theme: theme)
        }
    }

    private func currentTheme() -> Theme {
        if snapshot.tasks.contains(where: { $0.state == "failed" }) { return redTheme }
        if !snapshot.tasks.isEmpty && snapshot.activeCount == 0 { return greenTheme }
        return blueTheme
    }

    private func drawHeader(theme: Theme) {
        theme.accent.setFill()
        NSBezierPath(ovalIn: NSRect(x: 14, y: 12, width: 10, height: 10)).fill()
        drawText("GIT TASKS", rect: NSRect(x: 32, y: 8, width: 82, height: 16), size: 10, weight: .bold, color: theme.text)

        let activeText = snapshot.activeCount == 1 ? "1 ACTIVE" : "\(snapshot.activeCount) ACTIVE"
        let totalText = snapshot.totalVisibleCount > maxVisibleTasks ? "\(maxVisibleTasks)/\(snapshot.totalVisibleCount) SHOWN" : activeText
        drawText(totalText, rect: NSRect(x: 210, y: 8, width: 128, height: 16), size: 10, weight: .bold, color: theme.muted, alignment: .right)
    }

    private func drawTask(_ task: GitProgressTask, index: Int, theme: Theme) {
        let y = CGFloat(42 + index * 42)
        let commandRepo = "\(task.command.uppercased())  \(task.repo)"
        drawText(commandRepo, rect: NSRect(x: 16, y: y, width: 178, height: 15), size: 11, weight: .bold, color: theme.text)

        let percentText = task.percent.map { "\(Int($0.rounded()))%" } ?? "--"
        drawText(percentText, rect: NSRect(x: 198, y: y, width: 42, height: 15), size: 11, weight: .bold, color: theme.accent, alignment: .right)
        drawText(task.speed.isEmpty ? "--/s" : task.speed, rect: NSRect(x: 246, y: y, width: 94, height: 15), size: 10, weight: .bold, color: theme.muted, alignment: .right)

        drawText(task.phase, rect: NSRect(x: 16, y: y + 17, width: 112, height: 14), size: 9, weight: .semibold, color: theme.muted)
        drawText(task.detail, rect: NSRect(x: 132, y: y + 17, width: 208, height: 14), size: 9, weight: .regular, color: theme.muted)
        drawProgressBar(task: task, rect: NSRect(x: 16, y: y + 32, width: bounds.width - 32, height: 6), theme: theme)
    }

    private func drawProgressBar(task: GitProgressTask, rect: NSRect, theme: Theme) {
        theme.track.setFill()
        NSBezierPath(roundedRect: rect, xRadius: 3, yRadius: 3).fill()
        theme.accent.setFill()
        if let percent = task.percent {
            let width = max(3, rect.width * CGFloat(max(0, min(100, percent)) / 100.0))
            NSBezierPath(roundedRect: NSRect(x: rect.minX, y: rect.minY, width: width, height: rect.height), xRadius: 3, yRadius: 3).fill()
        } else {
            let segmentWidth = rect.width * 0.24
            let x = rect.minX + CGFloat(pulse) * (rect.width - segmentWidth)
            NSBezierPath(roundedRect: NSRect(x: x, y: rect.minY, width: segmentWidth, height: rect.height), xRadius: 3, yRadius: 3).fill()
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
        try? FileManager.default.createDirectory(at: tasksDir, withIntermediateDirectories: true)
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
        guard let snapshot = loadSnapshot(), snapshot.visible else {
            window?.orderOut(nil)
            return
        }
        view.snapshot = snapshot
        window?.orderFrontRegardless()
    }

    private func loadSnapshot() -> GitProgressSnapshot? {
        let now = Date().timeIntervalSince1970
        var tasks = loadTaskFiles(now: now)
        if tasks.isEmpty, let fallback = loadTask(at: statusPath, now: now) {
            tasks = [fallback]
        }
        let visibleTasks = tasks.filter { $0.visible }
        if visibleTasks.isEmpty {
            return nil
        }
        let sorted = visibleTasks.sorted { lhs, rhs in
            if lhs.active != rhs.active { return lhs.active && !rhs.active }
            return lhs.updatedAt > rhs.updatedAt
        }
        return GitProgressSnapshot(
            visible: true,
            tasks: Array(sorted.prefix(maxVisibleTasks)),
            activeCount: visibleTasks.filter { $0.active }.count,
            totalVisibleCount: visibleTasks.count
        )
    }

    private func loadTaskFiles(now: Double) -> [GitProgressTask] {
        guard let files = try? FileManager.default.contentsOfDirectory(
            at: tasksDir,
            includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles]
        ) else {
            return []
        }
        return files
            .filter { $0.pathExtension == "json" }
            .compactMap { loadTask(at: $0, now: now) }
    }

    private func loadTask(at url: URL, now: Double) -> GitProgressTask? {
        guard let data = try? Data(contentsOf: url),
              let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return nil
        }
        let active = payload["active"] as? Bool ?? false
        let stateName = optionalString(payload["state"]) ?? "idle"
        let updatedAt = optionalDouble(payload["updated_at"]) ?? 0
        let finishedAt = optionalDouble(payload["finished_at"])
        let pid = optionalInt(payload["pid"])
        let recentlyFinished = !active && (stateName == "done" || stateName == "failed") && finishedAt.map { now - $0 < doneVisibleSeconds } == true
        let freshActive = active && (processExists(pid) || now - updatedAt < activeVisibleSeconds)
        let visible = freshActive || recentlyFinished
        return GitProgressTask(
            id: optionalString(payload["id"]) ?? url.deletingPathExtension().lastPathComponent,
            command: optionalString(payload["command"]) ?? "git",
            repo: optionalString(payload["repo"]) ?? "",
            phase: optionalString(payload["phase"]) ?? "Working",
            detail: optionalString(payload["detail"]) ?? "",
            percent: optionalDouble(payload["percent"]),
            speed: optionalString(payload["speed"]) ?? "",
            state: stateName,
            active: active,
            updatedAt: updatedAt,
            finishedAt: finishedAt,
            pid: pid,
            visible: visible
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
