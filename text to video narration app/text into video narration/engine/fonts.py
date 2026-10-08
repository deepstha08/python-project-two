"""Font loading with a bundled font first and system fonts as fallback."""
import os
from functools import lru_cache

from PIL import ImageFont

_HERE = os.path.dirname(os.path.abspath(__file__))
_ASSETS = os.path.join(os.path.dirname(_HERE), "assets", "fonts")

_BOLD = [
    os.path.join(_ASSETS, "Poppins-ExtraBold.ttf"),
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
_REGULAR = [
    os.path.join(_ASSETS, "Poppins-SemiBold.ttf"),
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]


@lru_cache(maxsize=64)
def get_font(size: int, bold: bool = True):
    size = max(8, int(size))
    for path in (_BOLD if bold else _REGULAR) + (_REGULAR if bold else _BOLD):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def font_size(font) -> int:
    return int(getattr(font, "size", 20))
