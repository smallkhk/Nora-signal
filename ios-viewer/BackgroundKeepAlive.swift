import Foundation
import AVFoundation
import UIKit

// Keeps the app running in the background by playing silent audio.
// Requires UIBackgroundModes: ["audio"] in Info.plist.
// Without this, iOS suspends the app ~5 seconds after it goes to background,
// dropping the Socket.IO connection and freezing JPEG / FastCap streams.

@MainActor
final class BackgroundKeepAlive {

    static let shared = BackgroundKeepAlive()

    private var engine: AVAudioEngine?
    private var player: AVAudioPlayerNode?

    func start() {
        guard engine == nil else { return }

        // Configure session for background playback.
        // .mixWithOthers so we don't stop the user's music / podcasts.
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback,
                                     mode: .default,
                                     options: [.mixWithOthers])
            try session.setActive(true, options: [])
        } catch {
            NSLog("BackgroundKeepAlive: audio session failed — \(error)")
            return
        }

        // Build a tiny silent buffer and loop it.
        let eng = AVAudioEngine()
        let p   = AVAudioPlayerNode()
        eng.attach(p)

        guard let fmt = AVAudioFormat(standardFormatWithSampleRate: 44100,
                                       channels: 1) else { return }
        eng.connect(p, to: eng.mainMixerNode, format: fmt)

        let frames = AVAudioFrameCount(4410)   // 0.1s
        guard let buf = AVAudioPCMBuffer(pcmFormat: fmt,
                                          frameCapacity: frames) else { return }
        buf.frameLength = frames
        // Buffer memory is already zeroed → pure silence.

        do {
            try eng.start()
        } catch {
            NSLog("BackgroundKeepAlive: engine start failed — \(error)")
            return
        }

        p.scheduleBuffer(buf, at: nil, options: .loops)
        p.play()

        engine = eng
        player = p

        // Re-activate the audio session when the app returns to foreground,
        // in case iOS dropped it during a phone call or Siri.
        NotificationCenter.default.addObserver(
            forName: UIApplication.didBecomeActiveNotification,
            object: nil, queue: .main
        ) { _ in
            try? AVAudioSession.sharedInstance().setActive(true, options: [])
            if let eng = BackgroundKeepAlive.shared.engine, !eng.isRunning {
                try? eng.start()
                BackgroundKeepAlive.shared.player?.play()
            }
        }
    }
}
