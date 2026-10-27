// signal-vcam.exe — feeds the AI output from the SIGNAL app into the
// "SIGNAL" virtual camera.
//
// Usage:
//   signal-vcam.exe <softcam.dll> <width> <height> <fps>
//
// The app writes raw RGBA frames (width * height * 4 bytes each) to stdin.
// Closing stdin (or the app exiting) removes the camera cleanly.
//
// Two camera back-ends:
//   Windows 11: a Media Foundation virtual camera (MFCreateVirtualCamera) backed
//     by our media source DLL (native/mfcam). Every app sees it, including
//     Microsoft Store apps such as WhatsApp and the Windows Camera app.
//     Frames reach the source through shared memory (native/mfcam/SharedFrame.h).
//   Windows 10, or if the above is unavailable: softcam, a DirectShow camera
//     (seen by Chrome, Zoom, OBS, Discord… but not Store apps).
//
// stdout reports one status line: "READY mf", "READY ds", or "ERROR <reason>".

#include <windows.h>
#include <mfapi.h>
#include <mfvirtualcamera.h>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <vector>
#include "mfcam/SharedFrame.h"

// Must match CLSID_VCam in native/mfcam/dllmain.cpp.
static const wchar_t MF_SOURCE_CLSID[] = L"{E74F3E0A-EFD3-4242-ABD8-1D8D8AD6403C}";
static const wchar_t CAMERA_NAME[] = L"SIGNAL";

typedef void* scCamera;
typedef scCamera (__cdecl *CreateCameraFn)(int width, int height, float framerate);
typedef void     (__cdecl *DeleteCameraFn)(scCamera camera);
typedef void     (__cdecl *SendFrameFn)(scCamera camera, const void* image_bits);
typedef HRESULT  (WINAPI *CreateVirtualCameraFn)(MFVirtualCameraType, MFVirtualCameraLifetime,
	MFVirtualCameraAccess, LPCWSTR, LPCWSTR, const GUID*, ULONG, IMFVirtualCamera**);
typedef LONG     (WINAPI *RtlGetVersionFn)(OSVERSIONINFOW*);

static int fail(const char* reason, int code)
{
	std::printf("ERROR %s\n", reason);
	std::fflush(stdout);
	return code;
}

static bool readFull(HANDLE in, uint8_t* buf, size_t size)
{
	while (size > 0)
	{
		DWORD got = 0;
		DWORD want = size > 0x10000000 ? 0x10000000 : (DWORD)size;
		if (!ReadFile(in, buf, want, &got, nullptr) || got == 0) return false;
		buf += got;
		size -= got;
	}
	return true;
}

static bool isWindows11()
{
	auto rtlGetVersion = (RtlGetVersionFn)(void*)GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "RtlGetVersion");
	OSVERSIONINFOW v{ sizeof(v) };
	return rtlGetVersion && rtlGetVersion(&v) == 0 && v.dwMajorVersion >= 10 && v.dwBuildNumber >= 22000;
}

static bool mfSourceRegistered()
{
	wchar_t key[128];
	swprintf_s(key, L"SOFTWARE\\Classes\\CLSID\\%s\\InprocServer32", MF_SOURCE_CLSID);
	HKEY h = nullptr;
	if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, key, 0, KEY_READ | KEY_WOW64_64KEY, &h) != ERROR_SUCCESS) return false;
	RegCloseKey(h);
	return true;
}

// ── Windows 11: Media Foundation virtual camera ─────────────────────────────
static int runMediaFoundation(int width, int height)
{
	// Must fit the shared memory, sized for 1280x720 / 720x1280.
	if ((size_t)width * height > ECLIPSE_FRAME_MAX_PIXELS) { std::fprintf(stderr, "MF camera: frame size too large\n"); return -1; }

	// Loaded at runtime so the helper still starts on Windows 10.
	HMODULE sensorGroup = LoadLibraryW(L"mfsensorgroup.dll");
	auto createVirtualCamera = sensorGroup
		? (CreateVirtualCameraFn)(void*)GetProcAddress(sensorGroup, "MFCreateVirtualCamera") : nullptr;
	if (!createVirtualCamera) { std::fprintf(stderr, "MF camera: MFCreateVirtualCamera unavailable on this Windows\n"); return -1; }

	if (FAILED(CoInitializeEx(nullptr, COINIT_MULTITHREADED))) { std::fprintf(stderr, "MF camera: COM init failed\n"); return -1; }
	if (FAILED(MFStartup(MF_VERSION))) { std::fprintf(stderr, "MF camera: Media Foundation startup failed\n"); return -1; }

	IMFVirtualCamera* camera = nullptr;
	HRESULT hr = createVirtualCamera(MFVirtualCameraType_SoftwareCameraSource, MFVirtualCameraLifetime_Session,
		MFVirtualCameraAccess_CurrentUser, CAMERA_NAME, MF_SOURCE_CLSID, nullptr, 0, &camera);
	if (SUCCEEDED(hr)) hr = camera->Start(nullptr);
	if (FAILED(hr))
	{
		if (camera) camera->Release();
		MFShutdown();
		std::fprintf(stderr, "MF virtual camera failed: 0x%08lX\n", (unsigned long)hr);
		return -1;
	}

	std::printf("READY mf\n");
	std::fflush(stdout);

	const size_t pixels = (size_t)width * height;
	std::vector<uint8_t> rgba(pixels * 4);
	HANDLE in = GetStdHandle(STD_INPUT_HANDLE);
	HANDLE mapping = nullptr;
	EclipseSharedFrame* shared = nullptr;
	ULONGLONG lastOpenAttempt = 0;

	while (readFull(in, rgba.data(), rgba.size()))
	{
		// The media source creates the shared memory once an app starts
		// using the camera; keep trying to open it until then.
		if (!shared)
		{
			ULONGLONG now = GetTickCount64();
			if (now - lastOpenAttempt < 1000) continue;
			lastOpenAttempt = now;
			mapping = OpenFileMappingW(FILE_MAP_WRITE, FALSE, ECLIPSE_SHARED_FRAME_NAME);
			if (!mapping) continue;
			shared = (EclipseSharedFrame*)MapViewOfFile(mapping, FILE_MAP_WRITE, 0, 0, EclipseSharedFrameSize());
			if (!shared) { CloseHandle(mapping); mapping = nullptr; continue; }
		}

		shared->width = (UINT32)width;
		shared->height = (UINT32)height;
		shared->magic = ECLIPSE_FRAME_MAGIC;
		InterlockedIncrement(&shared->sequence);   // odd: writing
		const uint8_t* s = rgba.data();
		uint8_t* d = EclipseSharedFramePixels(shared);
		for (size_t i = 0; i < pixels; i++, s += 4, d += 4)
		{
			d[0] = s[2];
			d[1] = s[1];
			d[2] = s[0];
			d[3] = 0xFF;
		}
		InterlockedIncrement(&shared->sequence);   // even: stable
		shared->writtenAt = (LONGLONG)GetTickCount64();
	}

	if (shared) UnmapViewOfFile(shared);
	if (mapping) CloseHandle(mapping);
	camera->Shutdown();
	camera->Release();
	MFShutdown();
	CoUninitialize();
	return 0;
}

// ── Windows 10: softcam DirectShow camera ───────────────────────────────────
static int runSoftcam(const char* dllPath, int width, int height, float fps)
{
	HMODULE dll = LoadLibraryA(dllPath);
	if (!dll) return fail("dll-not-found", 2);
	auto createCamera = (CreateCameraFn)(void*)GetProcAddress(dll, "scCreateCamera");
	auto deleteCamera = (DeleteCameraFn)(void*)GetProcAddress(dll, "scDeleteCamera");
	auto sendFrame = (SendFrameFn)(void*)GetProcAddress(dll, "scSendFrame");
	if (!createCamera || !deleteCamera || !sendFrame) return fail("dll-invalid", 2);

	// Fails if another copy of the app already owns the camera.
	scCamera cam = createCamera(width, height, fps);
	if (!cam) return fail("camera-busy", 3);

	std::printf("READY ds\n");
	std::fflush(stdout);

	const size_t pixels = (size_t)width * height;
	std::vector<uint8_t> rgba(pixels * 4);
	std::vector<uint8_t> bgr(pixels * 3);
	HANDLE in = GetStdHandle(STD_INPUT_HANDLE);

	while (readFull(in, rgba.data(), rgba.size()))
	{
		const uint8_t* s = rgba.data();
		uint8_t* d = bgr.data();
		for (size_t i = 0; i < pixels; i++, s += 4, d += 3)
		{
			d[0] = s[2];
			d[1] = s[1];
			d[2] = s[0];
		}
		sendFrame(cam, bgr.data());
	}

	deleteCamera(cam);
	return 0;
}

int main(int argc, char** argv)
{
	if (argc != 5) return fail("usage: signal-vcam.exe <softcam.dll> <width> <height> <fps>", 1);

	const int width = std::atoi(argv[2]);
	const int height = std::atoi(argv[3]);
	const float fps = (float)std::atof(argv[4]);
	// softcam requires dimensions that are a multiple of four.
	if (width < 16 || height < 16 || width > 3840 || height > 2160 || width % 4 || height % 4)
		return fail("bad-size", 1);

	if (isWindows11() && !mfSourceRegistered())
		std::fprintf(stderr, "MF camera: not registered (reinstall with the Setup installer)\n");
	if (isWindows11() && mfSourceRegistered())
	{
		int result = runMediaFoundation(width, height);
		if (result >= 0) return result;
		// Fall through to softcam if the Windows 11 camera couldn't start.
	}
	return runSoftcam(argv[1], width, height, fps);
}
