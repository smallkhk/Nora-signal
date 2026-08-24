#!/usr/bin/env python3
"""Generate build/icon.ico for the SIGNAL desktop app.

electron-builder requires a Windows .ico containing at least a 256x256 image,
so this writes a multi-resolution icon (16 -> 256). Artwork is drawn at 4x and
downsampled for antialiasing.

Usage:  python3 build/make-icon.py
Requires: Pillow
"""

from PIL import Image, ImageDraw

SUPERSAMPLE = 4
BASE = 256
S = BASE * SUPERSAMPLE

BG = (11, 18, 32, 255)        # near-black navy
ARC = (232, 240, 252, 255)    # near-white broadcast arcs
LIVE = (239, 68, 68, 255)     # red "live" dot


def rounded_background(draw):
    radius = int(S * 0.22)
    draw.rounded_rectangle([0, 0, S - 1, S - 1], radius=radius, fill=BG)


def broadcast_mark(draw):
    """Center dot plus three concentric arcs radiating up-left and up-right,
    reading as a live broadcast signal."""
    cx = cy = S // 2
    dot_r = int(S * 0.072)
    draw.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=LIVE)

    stroke = int(S * 0.045)
    for i, scale in enumerate((0.17, 0.27, 0.37)):
        r = int(S * scale)
        box = [cx - r, cy - r, cx + r, cy + r]
        # Two mirrored arcs, leaving gaps top and bottom.
        draw.arc(box, start=205, end=335, fill=ARC, width=stroke)
        draw.arc(box, start=25, end=155, fill=ARC, width=stroke)


def main():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    rounded_background(draw)
    broadcast_mark(draw)

    icon = img.resize((BASE, BASE), Image.LANCZOS)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icon.save("build/icon.ico", format="ICO", sizes=sizes)
    icon.save("build/icon.png", format="PNG")
    print("wrote build/icon.ico and build/icon.png")


if __name__ == "__main__":
    main()
