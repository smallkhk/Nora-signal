import Foundation

// Checks GitHub releases for a newer IPA and returns the download URL.
// Repo: smallkhk/nora-signal — looks for a release asset named NoraViewer*.ipa

struct ReleaseInfo {
    let tag:     String
    let ipaURL:  URL
}

enum UpdateChecker {

    private static let currentVersion = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.0"

    static func check(completion: @escaping (ReleaseInfo?) -> Void) {
        let url = URL(string: "https://api.github.com/repos/smallkhk/nora-signal/releases/latest")!
        var req = URLRequest(url: url)
        req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        req.setValue("2022-11-28", forHTTPHeaderField: "X-GitHub-Api-Version")

        URLSession.shared.dataTask(with: req) { data, _, error in
            guard let data, error == nil,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let tag  = json["tag_name"] as? String,
                  let assets = json["assets"] as? [[String: Any]] else {
                completion(nil); return
            }

            // Find the first .ipa asset
            guard let asset = assets.first(where: {
                      ($0["name"] as? String)?.hasSuffix(".ipa") == true
                  }),
                  let urlStr = asset["browser_download_url"] as? String,
                  let ipaURL = URL(string: urlStr) else {
                completion(nil); return
            }

            // Compare semver — only suggest update if tag > current
            let isNewer = _isNewer(tag: tag, than: currentVersion)
            completion(isNewer ? ReleaseInfo(tag: tag, ipaURL: ipaURL) : nil)
        }.resume()
    }

    // Returns true if tagVersion > currentVersion (naive semver compare)
    private static func _isNewer(tag: String, than current: String) -> Bool {
        let clean: (String) -> [Int] = { v in
            v.trimmingCharacters(in: .letters)
             .split(separator: ".")
             .compactMap { Int($0) }
        }
        let a = clean(tag), b = clean(current)
        for i in 0..<max(a.count, b.count) {
            let x = i < a.count ? a[i] : 0
            let y = i < b.count ? b[i] : 0
            if x != y { return x > y }
        }
        return false
    }
}
