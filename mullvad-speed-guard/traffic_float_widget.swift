#!/usr/bin/env swift
import Cocoa
import Foundation

let widgetWidth: CGFloat = 274
let widgetHeight: CGFloat = 152
let minimizedWidth: CGFloat = 110
let minimizedHeight: CGFloat = 34
let pollSeconds: TimeInterval = 1.25
let slowThresholdMbps = 5.0

let executablePath = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
let appDir = executablePath.deletingLastPathComponent()
let resultsDir = appDir.appendingPathComponent("results")
let positionPath = resultsDir.appendingPathComponent("float_widget_position.json")
let logPath = resultsDir.appendingPathComponent("float_widget.log")
let panelStateURL = URL(string: ProcessInfo.processInfo.environment["MSG_PANEL_STATE_URL"] ?? "http://127.0.0.1:18790/api/state")!
let panelOpenURL = URL(string: ProcessInfo.processInfo.environment["MSG_PANEL_OPEN_URL"] ?? "http://127.0.0.1:18790/")!
let panelSpeedRescueURL = URL(string: ProcessInfo.processInfo.environment["MSG_PANEL_SPEED_RESCUE_URL"] ?? "http://127.0.0.1:18790/api/inventory/speed-rescue")!
let panelFinishSpeedRescueURL = URL(string: ProcessInfo.processInfo.environment["MSG_PANEL_FINISH_SPEED_RESCUE_URL"] ?? "http://127.0.0.1:18790/api/inventory/finish-speed-rescue")!
let panelNightlyToggleURL = URL(string: ProcessInfo.processInfo.environment["MSG_PANEL_NIGHTLY_TOGGLE_URL"] ?? "http://127.0.0.1:18790/api/inventory/nightly-fullscan")!

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
    var downloadMbps: Double?
    var sampledAt = ""
    var scanRunning = false
    var controlLockReason: String?
    var scheduledScanEnabled = true
}

struct RawTrafficSample {
    let interface: String?
    let sampledAt: String
    let sampledDate: Date?
    let receivedUptime: TimeInterval
    let rawDownload: Int
    let rawUpload: Int
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

func formatMBps(fromMbps value: Double?) -> String {
    guard let value else { return "--" }
    let megabytesPerSecond = value / 8.0
    if megabytesPerSecond >= 100 {
        return String(format: "%.0f", megabytesPerSecond)
    }
    if megabytesPerSecond >= 10 {
        return String(format: "%.1f", megabytesPerSecond)
    }
    return String(format: "%.2f", megabytesPerSecond)
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
    private var lastSample: RawTrafficSample?
    private var lastDisplayedDownloadMbps: Double?
    private var recentDownloadRatesMbps: [Double] = []

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
        let inventory = root["inventory"] as? [String: Any] ?? [:]
        let controlLock = inventory["auto_guard_control_lock"] as? [String: Any] ?? [:]
        let scheduledScan = inventory["scheduled_full_scan"] as? [String: Any] ?? [:]
        let scheduledScanEnabled = scheduledScan["enabled"] as? Bool ?? true
        let state = optionalString(connection["state"]) ?? "Unknown"
        let sampledAt = optionalString(traffic["sampled_at"]) ?? currentTimestamp()
        let interface = optionalString(traffic["interface"])
        let downloadTotal = optionalInt(traffic["download_bytes"]) ?? 0
        let uploadTotal = optionalInt(traffic["upload_bytes"]) ?? 0
        let ok = (traffic["ok"] as? Bool ?? false) && state.lowercased().hasPrefix("connected")
        let rawDownload = optionalInt(traffic["interface_download_bytes"])
        let rawUpload = optionalInt(traffic["interface_upload_bytes"])
        let resetReason = optionalString(traffic["reset_reason"])
        let currentSample = makeSample(
            interface: interface,
            sampledAt: sampledAt,
            rawDownload: rawDownload,
            rawUpload: rawUpload
        )

        var downloadMbps = lastDisplayedDownloadMbps
        if !ok {
            clearRateHistory()
            downloadMbps = nil
        } else if resetReason != nil {
            clearRateHistory()
            downloadMbps = nil
        } else if let previous = lastSample, let current = currentSample {
            downloadMbps = computeDownloadMbps(previous: previous, current: current)
            if let computedMbps = downloadMbps {
                rememberRate(computedMbps)
                let smoothedMbps = median(recentDownloadRatesMbps)
                downloadMbps = smoothedMbps
                lastDisplayedDownloadMbps = smoothedMbps
            } else {
                downloadMbps = lastDisplayedDownloadMbps
            }
        }
        if let currentSample {
            lastSample = currentSample
        }

        var status: String
        var stateText: String
        if !ok {
            status = "red"
            stateText = state.lowercased().hasPrefix("connected") ? "NO TUNNEL" : "DISCONNECTED"
            downloadMbps = nil
        } else if downloadMbps == nil || downloadMbps! < slowThresholdMbps {
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
            downloadMbps: downloadMbps,
            sampledAt: sampledAt,
            scanRunning: inventory["scan_running"] as? Bool ?? false,
            controlLockReason: optionalString(controlLock["reason"]),
            scheduledScanEnabled: scheduledScanEnabled
        )
    }

    private func makeSample(
        interface: String?,
        sampledAt: String,
        rawDownload: Int?,
        rawUpload: Int?
    ) -> RawTrafficSample? {
        guard let rawDownload, let rawUpload else {
            return nil
        }
        return RawTrafficSample(
            interface: interface,
            sampledAt: sampledAt,
            sampledDate: parsePanelTimestamp(sampledAt),
            receivedUptime: ProcessInfo.processInfo.systemUptime,
            rawDownload: rawDownload,
            rawUpload: rawUpload
        )
    }

    private func computeDownloadMbps(previous: RawTrafficSample, current: RawTrafficSample) -> Double? {
        if previous.sampledAt == current.sampledAt {
            return nil
        }
        if previous.interface != current.interface {
            clearRateHistory()
            return nil
        }
        if current.rawDownload < previous.rawDownload || current.rawUpload < previous.rawUpload {
            clearRateHistory()
            return nil
        }
        let elapsed = sampleElapsedSeconds(previous: previous, current: current)
        if elapsed < 0.5 || elapsed > 30.0 {
            return nil
        }
        let deltaDownload = current.rawDownload - previous.rawDownload
        return Double(deltaDownload) * 8.0 / elapsed / 1_000_000.0
    }

    private func sampleElapsedSeconds(previous: RawTrafficSample, current: RawTrafficSample) -> TimeInterval {
        if let previousDate = previous.sampledDate, let currentDate = current.sampledDate {
            let elapsed = currentDate.timeIntervalSince(previousDate)
            if elapsed > 0 {
                return elapsed
            }
        }
        return current.receivedUptime - previous.receivedUptime
    }

    private func rememberRate(_ mbps: Double) {
        if mbps.isFinite && mbps >= 0 {
            recentDownloadRatesMbps.append(mbps)
            if recentDownloadRatesMbps.count > 5 {
                recentDownloadRatesMbps.removeFirst(recentDownloadRatesMbps.count - 5)
            }
        }
    }

    private func clearRateHistory() {
        recentDownloadRatesMbps.removeAll()
        lastDisplayedDownloadMbps = nil
    }

    private func median(_ values: [Double]) -> Double? {
        if values.isEmpty {
            return nil
        }
        let sorted = values.sorted()
        return sorted[sorted.count / 2]
    }

    private func currentTimestamp() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return formatter.string(from: Date())
    }

    private func parsePanelTimestamp(_ value: String) -> Date? {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return formatter.date(from: value)
    }
}

final class WidgetView: NSView {
    var state = WidgetState() {
        didSet {
            needsDisplay = true
        }
    }
    var rescueBusy = false {
        didSet {
            needsDisplay = true
        }
    }
    var actionLabelOverride: String? {
        didSet {
            needsDisplay = true
        }
    }
    var minimized = false {
        didSet {
            needsDisplay = true
        }
    }
    var onSpeedRescue: (() -> Void)?
    var onFinishSpeedRescue: (() -> Void)?
    var onToggleNightly: (() -> Void)?
    var onOpenPanel: (() -> Void)?
    var onMinimize: (() -> Void)?
    var onRestore: (() -> Void)?
    var onCloseRequest: (() -> Void)?
    var dragOffset = NSPoint.zero
    private var draggedDuringMouse = false
    private var suppressRestoreOnMouseUp = false

    override var isFlipped: Bool {
        true
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        let theme = themes[state.status] ?? themes["red"]!
        let bounds = self.bounds

        if minimized {
            drawMinimized(theme: theme)
            return
        }

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
        drawText(timeText.isEmpty ? "--:--:--" : timeText, rect: NSRect(x: 164, y: 7, width: 54, height: 16), size: 9, weight: .bold, color: theme.muted, alignment: .right)
        drawCloseButton(rect: closeRect(), color: theme.muted)
        drawMinimizeButton(rect: minimizeRect(), color: theme.muted)

        drawText("NOW", rect: NSRect(x: 16, y: 43, width: 70, height: 13), size: 9, weight: .bold, color: theme.muted)
        let speedText = state.status == "red" ? "--" : formatMBps(fromMbps: state.downloadMbps)
        drawText(speedText, rect: NSRect(x: 16, y: 56, width: 92, height: 40), size: 30, weight: .bold, color: theme.accent)
        drawText("MB/s", rect: NSRect(x: 105, y: 75, width: 48, height: 16), size: 10, weight: .bold, color: theme.muted)

        drawText("DOWN", rect: NSRect(x: 166, y: 43, width: 88, height: 13), size: 9, weight: .bold, color: theme.muted)
        drawText(formatBytes(state.downloadTotal), rect: NSRect(x: 166, y: 58, width: 94, height: 19), size: 13, weight: .bold, color: theme.text)
        drawText("UP", rect: NSRect(x: 166, y: 82, width: 88, height: 13), size: 9, weight: .bold, color: theme.muted)
        drawText(formatBytes(state.uploadTotal), rect: NSRect(x: 166, y: 96, width: 94, height: 19), size: 13, weight: .bold, color: theme.text)

        drawActionButton(theme: theme)
    }

    private func drawMinimized(theme: Theme) {
        theme.bg.setFill()
        NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 1), xRadius: 17, yRadius: 17).fill()
        theme.accent.setStroke()
        let border = NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 1), xRadius: 17, yRadius: 17)
        border.lineWidth = 2
        border.stroke()
        theme.accent.setFill()
        NSBezierPath(ovalIn: NSRect(x: 12, y: 12, width: 9, height: 9)).fill()
        drawText("VPN", rect: NSRect(x: 29, y: 9, width: 32, height: 16), size: 10, weight: .bold, color: theme.text)
        drawText(state.status == "red" ? "OFF" : state.stateText, rect: NSRect(x: 60, y: 9, width: 38, height: 16), size: 8, weight: .bold, color: theme.muted, alignment: .right)
    }

    private func minimizeRect() -> NSRect {
        NSRect(x: bounds.width - 24, y: 7, width: 16, height: 16)
    }

    private func closeRect() -> NSRect {
        NSRect(x: bounds.width - 44, y: 7, width: 16, height: 16)
    }

    private func drawMinimizeButton(rect: NSRect, color: NSColor) {
        color.withAlphaComponent(0.85).setStroke()
        let line = NSBezierPath()
        line.move(to: NSPoint(x: rect.minX + 4, y: rect.midY))
        line.line(to: NSPoint(x: rect.maxX - 4, y: rect.midY))
        line.lineWidth = 2
        line.stroke()
    }

    private func drawCloseButton(rect: NSRect, color: NSColor) {
        color.withAlphaComponent(0.85).setStroke()
        let first = NSBezierPath()
        first.move(to: NSPoint(x: rect.minX + 4, y: rect.minY + 4))
        first.line(to: NSPoint(x: rect.maxX - 4, y: rect.maxY - 4))
        first.lineWidth = 1.8
        first.stroke()
        let second = NSBezierPath()
        second.move(to: NSPoint(x: rect.maxX - 4, y: rect.minY + 4))
        second.line(to: NSPoint(x: rect.minX + 4, y: rect.maxY - 4))
        second.lineWidth = 1.8
        second.stroke()
    }

    private func actionRect() -> NSRect {
        NSRect(x: 14, y: 122, width: bounds.width - 28, height: 20)
    }

    private func actionTitle() -> String {
        if let actionLabelOverride {
            return actionLabelOverride
        }
        if state.scanRunning {
            if state.controlLockReason == "manual speed rescue" {
                return "FINISH TO BEST"
            }
            return "TRUE TEST RUNNING"
        }
        return "TEST + SWITCH"
    }

    private func actionEnabled() -> Bool {
        !rescueBusy && (!state.scanRunning || state.controlLockReason == "manual speed rescue")
    }

    private func drawActionButton(theme: Theme) {
        let rect = actionRect()
        let enabled = actionEnabled()
        let path = NSBezierPath(roundedRect: rect, xRadius: 8, yRadius: 8)
        if enabled {
            theme.accent.withAlphaComponent(0.18).setFill()
            path.fill()
            theme.accent.setStroke()
        } else {
            theme.strip.setFill()
            path.fill()
            theme.muted.withAlphaComponent(0.45).setStroke()
        }
        path.lineWidth = 1
        path.stroke()
        drawText(
            actionTitle(),
            rect: rect.insetBy(dx: 6, dy: 3),
            size: 10,
            weight: .bold,
            color: enabled ? theme.accent : theme.muted,
            alignment: .center
        )
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
        let point = convert(event.locationInWindow, from: nil)
        draggedDuringMouse = false
        if minimized {
            beginDrag()
            return
        }
        if closeRect().contains(point) {
            onCloseRequest?()
            return
        }
        if minimizeRect().contains(point) {
            suppressRestoreOnMouseUp = true
            onMinimize?()
            return
        }
        if actionRect().contains(point) {
            if state.scanRunning && state.controlLockReason == "manual speed rescue" && !rescueBusy {
                onFinishSpeedRescue?()
            } else if actionEnabled() {
                onSpeedRescue?()
            } else {
                NSWorkspace.shared.open(panelOpenURL)
            }
            return
        }
        if event.clickCount >= 2 {
            NSWorkspace.shared.open(panelOpenURL)
            return
        }
        beginDrag()
    }

    override func rightMouseDown(with event: NSEvent) {
        let menu = NSMenu()
        let scanTitle = state.scheduledScanEnabled
            ? "每日4点全节点测速：开（点此关闭）"
            : "每日4点全节点测速：关（点此开启）"
        let toggleItem = NSMenuItem(title: scanTitle, action: #selector(toggleNightlyMenuAction), keyEquivalent: "")
        toggleItem.target = self
        menu.addItem(toggleItem)
        menu.addItem(NSMenuItem.separator())
        let openItem = NSMenuItem(title: "打开控制面板", action: #selector(openPanelMenuAction), keyEquivalent: "")
        openItem.target = self
        menu.addItem(openItem)
        NSMenu.popUpContextMenu(menu, with: event, for: self)
    }

    @objc private func toggleNightlyMenuAction() {
        onToggleNightly?()
    }

    @objc private func openPanelMenuAction() {
        onOpenPanel?()
    }

    private func beginDrag() {
        guard let window else { return }
        let mouse = NSEvent.mouseLocation
        dragOffset = NSPoint(x: mouse.x - window.frame.origin.x, y: mouse.y - window.frame.origin.y)
    }

    override func mouseDragged(with event: NSEvent) {
        guard let window else { return }
        draggedDuringMouse = true
        let mouse = NSEvent.mouseLocation
        var origin = NSPoint(x: mouse.x - dragOffset.x, y: mouse.y - dragOffset.y)
        if let frame = NSScreen.main?.visibleFrame {
            origin.x = min(max(frame.minX, origin.x), frame.maxX - window.frame.width)
            origin.y = min(max(frame.minY, origin.y), frame.maxY - window.frame.height)
        }
        window.setFrameOrigin(origin)
    }

    override func mouseUp(with event: NSEvent) {
        if suppressRestoreOnMouseUp {
            suppressRestoreOnMouseUp = false
            savePosition()
            return
        }
        if minimized && !draggedDuringMouse {
            onRestore?()
            return
        }
        savePosition()
    }

    override func resetCursorRects() {
        if minimized {
            addCursorRect(bounds, cursor: .pointingHand)
            return
        }
        addCursorRect(closeRect(), cursor: .pointingHand)
        addCursorRect(minimizeRect(), cursor: .pointingHand)
        addCursorRect(actionRect(), cursor: .pointingHand)
    }

    func savePosition() {
        guard let window, let screen = NSScreen.main else { return }
        let topLeftY = max(0, Int(screen.visibleFrame.maxY - window.frame.origin.y - window.frame.height))
        let payload: [String: Int] = [
            "x": max(0, Int(window.frame.origin.x)),
            "y": topLeftY,
            "minimized": minimized ? 1 : 0,
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
    private var minimized = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        try? FileManager.default.createDirectory(at: resultsDir, withIntermediateDirectories: true)
        NSApp.setActivationPolicy(.accessory)
        minimized = loadMinimizedState()
        view.minimized = minimized
        view.onSpeedRescue = { [weak self] in
            self?.confirmAndStartSpeedRescue()
        }
        view.onFinishSpeedRescue = { [weak self] in
            self?.finishSpeedRescue(closeAfter: false)
        }
        view.onToggleNightly = { [weak self] in self?.toggleNightly() }
        view.onOpenPanel = { NSWorkspace.shared.open(panelOpenURL) }
        view.onMinimize = { [weak self] in self?.setMinimized(true) }
        view.onRestore = { [weak self] in self?.setMinimized(false) }
        view.onCloseRequest = { [weak self] in self?.closeWidget() }
        buildWindow()
        fetchNow()
        timer = Timer.scheduledTimer(withTimeInterval: pollSeconds, repeats: true) { [weak self] _ in
            self?.fetchNow()
        }
    }

    private func buildWindow() {
        let origin = initialOrigin()
        let width = minimized ? minimizedWidth : widgetWidth
        let height = minimized ? minimizedHeight : widgetHeight
        let panel = NSPanel(
            contentRect: NSRect(x: origin.x, y: origin.y, width: width, height: height),
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
        let width = minimized ? minimizedWidth : widgetWidth
        let height = minimized ? minimizedHeight : widgetHeight
        if let data = try? Data(contentsOf: positionPath),
           let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            x = optionalInt(payload["x"]) ?? x
            yFromTop = optionalInt(payload["y"]) ?? yFromTop
        }
        let clampedX = min(max(Int(screen.minX), x), Int(screen.maxX - width))
        let appKitY = Int(screen.maxY) - yFromTop - Int(height)
        let clampedY = min(max(Int(screen.minY), appKitY), Int(screen.maxY - height))
        return NSPoint(x: clampedX, y: clampedY)
    }

    private func loadMinimizedState() -> Bool {
        guard let data = try? Data(contentsOf: positionPath),
              let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return false
        }
        return (optionalInt(payload["minimized"]) ?? 0) == 1
    }

    private func setMinimized(_ nextValue: Bool) {
        guard let window, minimized != nextValue else { return }
        let oldFrame = window.frame
        minimized = nextValue
        view.minimized = nextValue
        view.frame = NSRect(x: 0, y: 0, width: nextValue ? minimizedWidth : widgetWidth, height: nextValue ? minimizedHeight : widgetHeight)

        let width = nextValue ? minimizedWidth : widgetWidth
        let height = nextValue ? minimizedHeight : widgetHeight
        var origin = oldFrame.origin
        origin.y += oldFrame.height - height
        if let screenFrame = NSScreen.main?.visibleFrame {
            origin.x = min(max(screenFrame.minX, origin.x), screenFrame.maxX - width)
            origin.y = min(max(screenFrame.minY, origin.y), screenFrame.maxY - height)
        }
        window.setFrame(NSRect(x: origin.x, y: origin.y, width: width, height: height), display: true, animate: false)
        window.orderFrontRegardless()
        view.savePosition()
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
                    state.downloadMbps = nil
                    state.sampledAt = String(currentClockSuffix())
                    self.view.state = state
                    self.window?.orderFrontRegardless()
                }
            }
        }
    }

    private func confirmAndStartSpeedRescue() {
        if view.rescueBusy {
            return
        }
        let alert = NSAlert()
        alert.messageText = "Test and switch VPN relay?"
        alert.informativeText = "This will true-test fast candidates and leave Mullvad connected to the best verified relay. Your VPN connection may briefly drop while relays are tested."
        alert.addButton(withTitle: "Start")
        alert.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        if alert.runModal() != .alertFirstButtonReturn {
            return
        }
        startSpeedRescue()
    }

    private func startSpeedRescue() {
        view.rescueBusy = true
        view.actionLabelOverride = "STARTING..."
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self else { return }
            do {
                let payload = try self.postSpeedRescue()
                let pid = payload["pid"].map { String(describing: $0) } ?? "-"
                log("speed rescue started pid=\(pid)")
                DispatchQueue.main.async {
                    self.view.rescueBusy = false
                    self.view.actionLabelOverride = "STARTED"
                    self.fetchNow()
                    self.clearActionOverrideSoon()
                }
            } catch {
                log("speed rescue failed: \(error.localizedDescription)")
                DispatchQueue.main.async {
                    self.view.rescueBusy = false
                    self.view.actionLabelOverride = "FAILED"
                    self.showSpeedRescueError(error)
                    self.clearActionOverrideSoon()
                }
            }
        }
    }

    private func closeWidget() {
        timer?.invalidate()
        timer = nil
        view.savePosition()
        if view.state.scanRunning && view.state.controlLockReason == "manual speed rescue" {
            finishSpeedRescue(closeAfter: true)
            return
        }
        NSApp.terminate(nil)
    }

    private func finishSpeedRescue(closeAfter: Bool) {
        if view.rescueBusy {
            return
        }
        view.rescueBusy = true
        view.actionLabelOverride = closeAfter ? "CLOSING..." : "FINISHING..."
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self else { return }
            do {
                let payload = try self.postFinishSpeedRescue()
                let result = payload["result"] as? [String: Any] ?? [:]
                let connected = result["connected"] as? [String: Any] ?? [:]
                let hostname = connected["hostname"].map { String(describing: $0) } ?? "-"
                log("speed rescue finished to best hostname=\(hostname)")
                DispatchQueue.main.async {
                    self.view.rescueBusy = false
                    self.view.actionLabelOverride = "BEST SET"
                    self.fetchNow()
                    if closeAfter {
                        NSApp.terminate(nil)
                    } else {
                        self.clearActionOverrideSoon()
                    }
                }
            } catch {
                log("speed rescue finish failed: \(error.localizedDescription)")
                DispatchQueue.main.async {
                    self.view.rescueBusy = false
                    self.view.actionLabelOverride = "FINISH FAILED"
                    if closeAfter {
                        NSApp.terminate(nil)
                    } else {
                        self.showSpeedRescueError(error)
                        self.clearActionOverrideSoon()
                    }
                }
            }
        }
    }

    private func postSpeedRescue() throws -> [String: Any] {
        var request = URLRequest(url: panelSpeedRescueURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 5
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("mullvad-speed-guard-float/2.1", forHTTPHeaderField: "User-Agent")
        request.httpBody = Data("{}".utf8)

        let semaphore = DispatchSemaphore(value: 0)
        var resultData: Data?
        var resultError: Error?
        let task = URLSession.shared.dataTask(with: request) { data, _, error in
            resultData = data
            resultError = error
            semaphore.signal()
        }
        task.resume()
        if semaphore.wait(timeout: .now() + 6) == .timedOut {
            task.cancel()
            throw NSError(domain: "FloatWidget", code: 10, userInfo: [NSLocalizedDescriptionKey: "panel speed rescue timeout"])
        }
        if let resultError {
            throw resultError
        }
        guard let resultData else {
            throw NSError(domain: "FloatWidget", code: 11, userInfo: [NSLocalizedDescriptionKey: "empty speed rescue response"])
        }
        let json = try JSONSerialization.jsonObject(with: resultData)
        guard let root = json as? [String: Any] else {
            throw NSError(domain: "FloatWidget", code: 12, userInfo: [NSLocalizedDescriptionKey: "invalid speed rescue response"])
        }
        if root["ok"] as? Bool == false {
            let message = root["error"].map { String(describing: $0) } ?? "speed rescue request failed"
            throw NSError(domain: "FloatWidget", code: 13, userInfo: [NSLocalizedDescriptionKey: message])
        }
        return root
    }

    private func postFinishSpeedRescue() throws -> [String: Any] {
        var request = URLRequest(url: panelFinishSpeedRescueURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 18
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("mullvad-speed-guard-float/2.2", forHTTPHeaderField: "User-Agent")
        request.httpBody = Data("{}".utf8)

        let semaphore = DispatchSemaphore(value: 0)
        var resultData: Data?
        var resultError: Error?
        let task = URLSession.shared.dataTask(with: request) { data, _, error in
            resultData = data
            resultError = error
            semaphore.signal()
        }
        task.resume()
        if semaphore.wait(timeout: .now() + 20) == .timedOut {
            task.cancel()
            throw NSError(domain: "FloatWidget", code: 20, userInfo: [NSLocalizedDescriptionKey: "finish speed rescue timeout"])
        }
        if let resultError {
            throw resultError
        }
        guard let resultData else {
            throw NSError(domain: "FloatWidget", code: 21, userInfo: [NSLocalizedDescriptionKey: "empty finish response"])
        }
        let json = try JSONSerialization.jsonObject(with: resultData)
        guard let root = json as? [String: Any] else {
            throw NSError(domain: "FloatWidget", code: 22, userInfo: [NSLocalizedDescriptionKey: "invalid finish response"])
        }
        if root["ok"] as? Bool == false {
            let message = root["error"].map { String(describing: $0) } ?? "finish speed rescue request failed"
            throw NSError(domain: "FloatWidget", code: 23, userInfo: [NSLocalizedDescriptionKey: message])
        }
        return root
    }

    private func toggleNightly() {
        let desired = !view.state.scheduledScanEnabled
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            do {
                _ = try self.postNightlyToggle(enabled: desired)
                DispatchQueue.main.async {
                    var state = self.view.state
                    state.scheduledScanEnabled = desired
                    self.view.state = state
                    self.fetchNow()
                }
            } catch {
                DispatchQueue.main.async { self.showNightlyToggleError(error) }
            }
        }
    }

    private func postNightlyToggle(enabled: Bool) throws -> [String: Any] {
        var request = URLRequest(url: panelNightlyToggleURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 5
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("mullvad-speed-guard-float/2.3", forHTTPHeaderField: "User-Agent")
        request.httpBody = Data("{\"enabled\": \(enabled ? "true" : "false")}".utf8)

        let semaphore = DispatchSemaphore(value: 0)
        var resultData: Data?
        var resultError: Error?
        let task = URLSession.shared.dataTask(with: request) { data, _, error in
            resultData = data
            resultError = error
            semaphore.signal()
        }
        task.resume()
        if semaphore.wait(timeout: .now() + 6) == .timedOut {
            task.cancel()
            throw NSError(domain: "FloatWidget", code: 30, userInfo: [NSLocalizedDescriptionKey: "nightly toggle timeout"])
        }
        if let resultError {
            throw resultError
        }
        guard let resultData,
              let root = try JSONSerialization.jsonObject(with: resultData) as? [String: Any] else {
            throw NSError(domain: "FloatWidget", code: 31, userInfo: [NSLocalizedDescriptionKey: "invalid nightly toggle response"])
        }
        if root["ok"] as? Bool == false {
            let message = root["error"].map { String(describing: $0) } ?? "nightly toggle failed"
            throw NSError(domain: "FloatWidget", code: 32, userInfo: [NSLocalizedDescriptionKey: message])
        }
        return root
    }

    private func showNightlyToggleError(_ error: Error) {
        let alert = NSAlert()
        alert.messageText = "无法切换每日全节点测速"
        alert.informativeText = error.localizedDescription
        alert.addButton(withTitle: "OK")
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }

    private func clearActionOverrideSoon() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 4) { [weak self] in
            self?.view.actionLabelOverride = nil
        }
    }

    private func showSpeedRescueError(_ error: Error) {
        let alert = NSAlert()
        alert.messageText = "Could not start Test + Switch"
        alert.informativeText = error.localizedDescription
        alert.addButton(withTitle: "OK")
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
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
