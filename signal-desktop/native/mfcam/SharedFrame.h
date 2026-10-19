// Frame hand-off between signal-vcam.exe (writer, runs as the user) and this
// media source (reader, runs inside Windows' Frame Server service).
//
// The media source creates the mapping, because only the service is allowed
// to create objects in the Global namespace; the helper opens it for writing.
// Layout: header followed by width*height BGRA pixels, top-down.
//
// Shared by native/signal-vcam.cpp and native/mfcam.
#pragma once
#include <windows.h>
#include <cstdio>

#define ECLIPSE_SHARED_FRAME_NAME L"Global\\SIGNAL_Frames"
// Camera size: 720x1280 (portrait) or 1280x720 (landscape). Both have the
// same pixel count, so the shared memory is the same size either way.
#define ECLIPSE_FRAME_LONG_SIDE 1280
#define ECLIPSE_FRAME_SHORT_SIDE 720
#define ECLIPSE_FRAME_MAX_PIXELS (ECLIPSE_FRAME_LONG_SIDE * ECLIPSE_FRAME_SHORT_SIDE)
#define ECLIPSE_FRAME_MAGIC 0x31434C45u  // "ELC1"
// A frame older than this means the app stopped sending; show standby.
#define ECLIPSE_FRAME_STALE_MS 3000

// The app writes the chosen orientation here ("portrait" or "landscape")
// before starting the camera; the media source reads it when an app opens
// the camera. ProgramData is readable by the Frame Server service.
#define ECLIPSE_CAMERA_CONFIG L"%ProgramData%\\SIGNAL\\camera.cfg"

struct EclipseSharedFrame
{
	UINT32 magic;
	UINT32 width;
	UINT32 height;
	// Seqlock: odd while the writer is copying a frame, even when stable.
	volatile LONG sequence;
	// GetTickCount64() of the last completed write.
	volatile LONGLONG writtenAt;
	// BGRA pixels follow.
};

inline SIZE_T EclipseSharedFrameSize()
{
	return sizeof(EclipseSharedFrame) + (SIZE_T)ECLIPSE_FRAME_MAX_PIXELS * 4;
}

inline BYTE* EclipseSharedFramePixels(EclipseSharedFrame* frame)
{
	return reinterpret_cast<BYTE*>(frame) + sizeof(EclipseSharedFrame);
}

// Camera size from the config file; portrait unless it says "landscape".
inline void EclipseCameraSize(UINT* width, UINT* height)
{
	bool landscape = false;
	wchar_t path[MAX_PATH];
	if (ExpandEnvironmentStringsW(ECLIPSE_CAMERA_CONFIG, path, MAX_PATH))
	{
		FILE* f = nullptr;
		if (_wfopen_s(&f, path, L"rb") == 0 && f)
		{
			char buf[32] = {};
			fread(buf, 1, sizeof(buf) - 1, f);
			fclose(f);
			landscape = strncmp(buf, "landscape", 9) == 0;
		}
	}
	*width = landscape ? ECLIPSE_FRAME_LONG_SIDE : ECLIPSE_FRAME_SHORT_SIDE;
	*height = landscape ? ECLIPSE_FRAME_SHORT_SIDE : ECLIPSE_FRAME_LONG_SIDE;
}
