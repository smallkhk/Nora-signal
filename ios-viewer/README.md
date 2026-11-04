# FastCap iOS Viewer

Native iOS viewer for the fastcap UDP screen-share protocol.
Same protocol as `viewer.py` — UDP, H.264 Annex-B, same token auth.

## Requirements
- Mac with Xcode 15+
- iPhone with iOS 16+
- Free Apple ID (no paid developer account needed for personal device)

## Build steps

1. Open Xcode → **File → New → Project**
2. Choose **iOS → App**
3. Set:
   - Product Name: `FastcapViewer`
   - Interface: `SwiftUI`
   - Language: `Swift`
   - Uncheck "Include Tests"
4. Delete the generated `ContentView.swift`
5. Drag these three files into the project:
   - `FastcapClient.swift`
   - `H264Decoder.swift`
   - `ContentView.swift`
6. In **Signing & Capabilities**, select your Apple ID team
7. Connect your iPhone via USB → select it as build target → **Run (⌘R)**
8. On first run Xcode asks you to trust the developer on your phone:
   Settings → General → VPN & Device Management → trust your Apple ID

## Usage

Enter the relay host, port, session ID (8 chars), and password,
then tap Connect. The screen streams from the host PC over UDP.

## Frameworks used (no extra packages needed)
- `Network.framework` — UDP socket
- `VideoToolbox` — hardware H.264 decode
- `AVFoundation` — `AVSampleBufferDisplayLayer`
- `CryptoKit` — SHA-256 token
