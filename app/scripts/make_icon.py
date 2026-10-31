"""Draw Neurotree's original icons with Pillow (python3 -m pip install pillow):
  icon-source.png              1024x1024 app icon: a glowing core with six faint branches on near-black,
                               fed to `npx tauri icon icon-source.png` for the bundle sizes.
  src-tauri/icons/tray.png     44x44 menubar template icon (black on transparent; macOS tints it).
Run from app/: python3 scripts/make_icon.py"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

APP = Path(__file__).resolve().parent.parent
BG = (5, 6, 10, 255)  # #05060a, the window background
CORE = (120, 220, 255)
BRANCH = (150, 120, 255)


def app_icon(size: int = 1024) -> Image.Image:
    c = size / 2
    m = int(size * 0.1)  # macOS icon grid: content inset ~10 %
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([m, m, size - m, size - m], radius=int(size * 0.18), fill=255)
    canvas.paste(Image.new("RGBA", (size, size), BG), (0, 0), mask)

    halo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    h = ImageDraw.Draw(halo)
    for r, alpha in ((0.26, 50), (0.18, 90), (0.12, 160)):
        rr = size * r
        h.ellipse([c - rr, c - rr, c + rr, c + rr], fill=CORE + (alpha,))
    halo = halo.filter(ImageFilter.GaussianBlur(size * 0.04))

    tree = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    t = ImageDraw.Draw(tree)
    for i in range(6):
        a = math.radians(90 + i * 60)
        tip = (c + math.cos(a) * size * 0.27, c - math.sin(a) * size * 0.27)
        t.line([(c, c), tip], fill=BRANCH + (170,), width=int(size * 0.014))
        r = size * 0.03
        t.ellipse([tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r], fill=BRANCH + (255,))
    tree = Image.alpha_composite(tree.filter(ImageFilter.GaussianBlur(size * 0.012)), tree)
    rr = size * 0.07
    ImageDraw.Draw(tree).ellipse([c - rr, c - rr, c + rr, c + rr], fill=(235, 250, 255, 255))

    art = Image.alpha_composite(halo, tree)
    art.putalpha(ImageChops.multiply(art.getchannel("A"), mask))
    return Image.alpha_composite(canvas, art)


def tray_icon(size: int = 44) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size / 2
    for i in range(6):
        a = math.radians(90 + i * 60)
        tip = (c + math.cos(a) * size * 0.36, c - math.sin(a) * size * 0.36)
        d.line([(c, c), tip], fill=(0, 0, 0, 255), width=2)
        d.ellipse([tip[0] - 3, tip[1] - 3, tip[0] + 3, tip[1] + 3], fill=(0, 0, 0, 255))
    r = size * 0.16
    d.ellipse([c - r, c - r, c + r, c + r], fill=(0, 0, 0, 255))
    return img


if __name__ == "__main__":
    app_icon().save(APP / "icon-source.png")
    (APP / "src-tauri" / "icons").mkdir(parents=True, exist_ok=True)
    tray_icon().save(APP / "src-tauri" / "icons" / "tray.png")
    print("wrote icon-source.png and src-tauri/icons/tray.png")
