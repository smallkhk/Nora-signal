import VideoToolbox
import CoreMedia
import CoreVideo
import Foundation

final class H264Decoder {
    var onFrame: ((CVPixelBuffer) -> Void)?

    private var formatDesc: CMVideoFormatDescription?
    private var vtSession:  VTDecompressionSession?
    private var sps: Data?
    private var pps: Data?

    // MARK: - Feed Annex-B data (same stream format as viewer.py)

    func feed(annexB: Data) {
        let nals = splitAnnexB(annexB)
        var resetNeeded = false

        for nal in nals where !nal.isEmpty {
            switch nal[0] & 0x1F {
            case 7:                     // SPS
                if sps != nal { sps = nal; resetNeeded = true }
            case 8:                     // PPS
                if pps != nal { pps = nal; resetNeeded = true }
            default: break
            }
        }

        if resetNeeded { rebuildSession() }
        guard vtSession != nil else { return }

        // Build AVCC block: skip SPS/PPS, prepend 4-byte big-endian NAL length
        var avcc = Data()
        for nal in nals where !nal.isEmpty {
            let t = nal[0] & 0x1F
            guard t != 7, t != 8 else { continue }
            var len = UInt32(nal.count).bigEndian
            avcc.append(Data(bytes: &len, count: 4))
            avcc.append(nal)
        }
        guard !avcc.isEmpty else { return }
        decode(avcc: avcc)
    }

    // MARK: - Annex-B → NAL units

    private func splitAnnexB(_ data: Data) -> [Data] {
        let b = [UInt8](data)
        // Record (nalStart, startCodeLen) for each NAL
        var starts: [(nal: Int, codeLen: Int)] = []
        var i = 0
        while i + 2 < b.count {
            if b[i] == 0, b[i+1] == 0 {
                if i+3 < b.count, b[i+2] == 0, b[i+3] == 1 {
                    starts.append((nal: i+4, codeLen: 4)); i += 4; continue
                }
                if b[i+2] == 1 {
                    starts.append((nal: i+3, codeLen: 3)); i += 3; continue
                }
            }
            i += 1
        }
        var result: [Data] = []
        for j in 0..<starts.count {
            let s = starts[j].nal
            let e = j+1 < starts.count
                ? starts[j+1].nal - starts[j+1].codeLen
                : b.count
            if s < e { result.append(Data(b[s..<e])) }
        }
        return result
    }

    // MARK: - Format description + session

    private func rebuildSession() {
        vtSession  = nil
        formatDesc = nil
        guard let sps, let pps else { return }

        let sets: [Data] = [sps, pps]
        var ptrs  = sets.map { ($0 as NSData).bytes.assumingMemoryBound(to: UInt8.self) }
        var sizes = sets.map { $0.count }

        let st = CMVideoFormatDescriptionCreateFromH264ParameterSets(
            allocator: nil,
            parameterSetCount: 2,
            parameterSetPointers: &ptrs,
            parameterSetSizes: &sizes,
            nalUnitHeaderLength: 4,
            formatDescriptionOut: &formatDesc
        )
        guard st == noErr, let desc = formatDesc else { return }

        let attrs: [NSString: Any] = [
            kCVPixelBufferPixelFormatTypeKey:   kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
            kCVPixelBufferMetalCompatibilityKey: true,
            kCVPixelBufferIOSurfacePropertiesKey: [:] as CFDictionary,
        ]

        var cb = VTDecompressionOutputCallbackRecord(
            decompressionOutputCallback: { refCon, _, status, _, imageBuffer, _, _ in
                guard status == noErr, let pb = imageBuffer else { return }
                Unmanaged<H264Decoder>.fromOpaque(refCon!).takeUnretainedValue().onFrame?(pb)
            },
            decompressionOutputRefCon: Unmanaged.passUnretained(self).toOpaque()
        )
        VTDecompressionSessionCreate(
            allocator: nil,
            formatDescription: desc,
            decoderSpecification: nil,
            imageBufferAttributes: attrs as CFDictionary,
            outputCallback: &cb,
            decompressionSessionOut: &vtSession
        )
    }

    // MARK: - Decode one AVCC frame (synchronous — nsData stays alive)

    private func decode(avcc: Data) {
        guard let desc = formatDesc, let sess = vtSession else { return }

        let ns = avcc as NSData
        var block: CMBlockBuffer?
        CMBlockBufferCreateWithMemoryBlock(
            allocator: nil,
            memoryBlock: UnsafeMutableRawPointer(mutating: ns.bytes),
            blockLength: ns.length,
            blockAllocator: kCFAllocatorNull,   // we own the memory via `ns`
            customBlockSource: nil,
            offsetToData: 0,
            dataLength: ns.length,
            flags: 0,
            blockBufferOut: &block
        )
        guard let blk = block else { return }

        var sample: CMSampleBuffer?
        var timing = CMSampleTimingInfo(
            duration:               CMTime(value: 1, timescale: 60),
            presentationTimeStamp:  CMClockGetTime(CMClockGetHostTimeClock()),
            decodeTimeStamp:        .invalid
        )
        CMSampleBufferCreateReady(
            allocator: nil, dataBuffer: blk, formatDescription: desc,
            sampleCount: 1, sampleTimingEntryCount: 1, sampleTimingArray: &timing,
            sampleSizeEntryCount: 0, sampleSizeArray: nil, sampleBufferOut: &sample
        )
        guard let sb = sample else { return }
        // Synchronous decode keeps `ns` alive on the call stack
        VTDecompressionSessionDecodeFrame(sess, sampleBuffer: sb, flags: [],
                                          frameRefcon: nil, infoFlagsOut: nil)
    }
}
