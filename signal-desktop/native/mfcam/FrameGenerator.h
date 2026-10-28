#pragma once

// Produces the camera's frames from the shared memory the Nora AI Video Call app
// writes to (see SharedFrame.h). When the app isn't sending, it shows a plain
// standby frame. Always CPU: frames arrive as plain pixels anyway.
class FrameGenerator
{
	UINT _width;
	UINT _height;
	ULONGLONG _frame;
	HANDLE _mapping;
	EclipseSharedFrame* _shared;
	ULONGLONG _lastOpenAttempt;
	std::vector<BYTE> _pixels;   // last good BGRA frame, top-down

	void EnsureShared();
	bool ReadShared();
	void FillStandby();

public:
	FrameGenerator() :
		_width(0),
		_height(0),
		_frame(0),
		_mapping(nullptr),
		_shared(nullptr),
		_lastOpenAttempt(0)
	{
	}

	~FrameGenerator()
	{
		if (_shared) UnmapViewOfFile(_shared);
		if (_mapping) CloseHandle(_mapping);
	}

	HRESULT EnsureRenderTarget(UINT width, UINT height);
	HRESULT Generate(IMFSample* sample, REFGUID format, IMFSample** outSample);
};
