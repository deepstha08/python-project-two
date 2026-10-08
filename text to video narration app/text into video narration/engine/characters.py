"""Animated characters: cartoon characters drawn in code, or a cartoon body
with a real photo face (cut-out "talking head" style with a moving jaw)."""
import os
import random
import zlib
from collections import OrderedDict

from PIL import Image, ImageDraw, ImageEnhance, ImageOps

try:
    LANCZOS = Image.Resampling.LANCZOS
except AttributeError:  # older Pillow
    LANCZOS = Image.LANCZOS

OUT = (45, 35, 50)
WHITE = (255, 255, 255)
MOUTH = (110, 35, 45)
TONGUE = (235, 115, 125)

SKINS = [(255, 224, 196), (247, 206, 170), (234, 184, 140), (214, 160, 115), (176, 120, 82), (130, 86, 58)]
HAIRS = [(45, 32, 25), (92, 58, 34), (150, 98, 52), (222, 180, 100), (25, 25, 32), (170, 70, 40), (60, 40, 30)]
GREYS = [(205, 205, 210), (175, 175, 182), (232, 232, 236)]
SHIRTS = [(239, 83, 80), (66, 165, 245), (102, 187, 106), (255, 193, 7), (171, 71, 188), (255, 138, 101),
          (38, 198, 218), (236, 64, 122), (92, 107, 192), (255, 112, 67)]
PANTS = [(55, 71, 79), (40, 53, 147), (93, 64, 55), (66, 66, 66), (30, 60, 110), (80, 90, 60)]
STYLES = {"male": ["short", "spiky", "curly", "side"], "boy": ["spiky", "short", "curly", "side"],
          "female": ["long", "bun", "ponytail", "bob"], "girl": ["pigtails", "long", "bob", "ponytail"],
          "old_male": ["bald", "short"], "old_female": ["bun", "bob"]}
HEIGHTS = {"boy": 0.80, "girl": 0.80, "old_male": 0.95, "old_female": 0.93}


def _shade(c, f):
    return tuple(max(0, min(255, int(v * f))) for v in c[:3])


def _mix(a, b, t):
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


_YUNET = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "models",
                      "face_detection_yunet_2023mar.onnx")


def _detect_face(img):
    """Return (x, y, w, h) of the biggest face, or None. Uses YuNet, then Haar cascade as fallback."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return None
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except Exception:
        pass
    arr = np.array(img)
    if os.path.exists(_YUNET) and hasattr(cv2, "FaceDetectorYN"):
        try:
            scale = min(1.0, 1024 / max(img.size))
            small = cv2.resize(arr, None, fx=scale, fy=scale) if scale < 1 else arr
            bgr = cv2.cvtColor(small, cv2.COLOR_RGB2BGR)
            det = cv2.FaceDetectorYN.create(_YUNET, "", (bgr.shape[1], bgr.shape[0]), 0.7, 0.3, 50)
            _, faces = det.detect(bgr)
            if faces is not None and len(faces):
                f = max(faces, key=lambda r: r[2] * r[3])
                return tuple(float(v) / scale for v in f[:4])
        except Exception:
            pass
    if hasattr(cv2, "CascadeClassifier"):
        try:
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
            casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            faces = casc.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
            if len(faces):
                return tuple(float(v) for v in max(faces, key=lambda f: f[2] * f[3]))
        except Exception:
            pass
    return None


def prepare_face(path):
    """Find the face in a photo and crop a square around it (hair + chin included)."""
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    W, H = img.size
    box = _detect_face(img)
    if box is not None:
        x, y, w, h = [float(v) for v in box]
        side = max(w, h) * 1.55
        cx, cy = x + w / 2, y + h * 0.42
    else:
        side = min(W, H) * 0.9
        cx, cy = W / 2, H * 0.42
    side = min(side, max(W, H))
    left = min(max(0, cx - side / 2), max(0, W - side))
    top = min(max(0, cy - side / 2), max(0, H - side))
    crop = img.crop((int(left), int(top), int(left + side), int(top + side)))
    crop = ImageEnhance.Color(crop).enhance(1.12)
    crop = ImageEnhance.Contrast(crop).enhance(1.05)
    return crop.resize((640, 640), LANCZOS)


class Character:
    W, H = 440, 800          # design canvas, feet at the bottom
    SS = 2                   # supersampling for smooth edges

    def __init__(self, name, kind="male", photo=None):
        self.name = name
        self.kind = kind if kind in STYLES else "male"
        rng = random.Random(zlib.crc32(name.lower().encode()))
        self.skin = rng.choice(SKINS)
        old = self.kind.startswith("old")
        self.hair = rng.choice(GREYS if old else HAIRS)
        self.shirt = rng.choice(SHIRTS)
        self.pants = rng.choice(PANTS)
        self.style = rng.choice(STYLES[self.kind])
        self.glasses = rng.random() < (0.55 if old else 0.15)
        self.beard = self.kind in ("male", "old_male") and rng.random() < 0.22
        self.dress = self.kind in ("female", "girl", "old_female") and rng.random() < 0.5
        self.height = HEIGHTS.get(self.kind, 1.0)
        self.blink_offset = rng.random() * 3.0
        self.blink_period = 3.0 + rng.random() * 2.0
        self.color = _shade(self.shirt, 0.85)
        self.face = prepare_face(photo) if photo else None
        self._face_cache = {}
        self._cache = OrderedDict()

    # ------------------------------------------------------------ public
    def is_blinking(self, t):
        return ((t + self.blink_offset) % self.blink_period) < 0.13

    def sprite(self, scale, eyes_open=True, mouth=0, emotion="neutral", look=0, gesture=0):
        key = (round(scale, 3), eyes_open, mouth, emotion, look, gesture)
        img = self._cache.get(key)
        if img is not None:
            self._cache.move_to_end(key)
            return img
        img = self._render(scale, eyes_open, mouth, emotion, look, gesture)
        self._cache[key] = img
        if len(self._cache) > 72:
            self._cache.popitem(last=False)
        return img

    # ----------------------------------------------------------- drawing
    def _render(self, scale, eyes_open, mouth, emotion, look, gesture):
        s = scale * self.height
        k = s * self.SS
        w, h = max(2, int(self.W * k)), max(2, int(self.H * k))
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        self._k = k
        self._lw = max(1, int(5 * k))
        if self.face is None:
            self._draw_hair_back(d)
        self._draw_body(d, gesture)
        if self.face is not None:
            self._draw_photo_head(img, d, mouth)
        else:
            self._draw_head(d, eyes_open, mouth, emotion, look)
        return img.resize((max(1, w // self.SS), max(1, h // self.SS)), LANCZOS)

    def _p(self, *v):
        return [x * self._k for x in v]

    def _limb(self, d, pts, color, width):
        k, P = self._k, self._p
        flat = P(*pts)
        d.line(flat, fill=OUT, width=int((width + 10) * k), joint="curve")
        d.line(flat, fill=color, width=int(width * k), joint="curve")
        for i in (0, len(pts) - 2):
            x, y = pts[i], pts[i + 1]
            r = width / 2
            d.ellipse(P(x - r, y - r, x + r, y + r), fill=color)

    def _draw_body(self, d, gesture):
        P, lw, skin = self._p, self._lw, self.skin
        leg = skin if self.dress else self.pants
        d.rounded_rectangle(P(172, 590, 214, 768), radius=18 * self._k, fill=leg, outline=OUT, width=lw)
        d.rounded_rectangle(P(226, 590, 268, 768), radius=18 * self._k, fill=leg, outline=OUT, width=lw)
        shoe = (70, 55, 65) if not self.dress else (200, 60, 90)
        d.ellipse(P(148, 742, 218, 792), fill=shoe, outline=OUT, width=lw)
        d.ellipse(P(222, 742, 292, 792), fill=shoe, outline=OUT, width=lw)
        # arms (behind torso)
        self._limb(d, [152, 432, 118, 585], self.shirt, 44)
        if gesture:
            self._limb(d, [288, 432, 350, 485, 372, 400], self.shirt, 44)
            hand = (372, 392)
        else:
            self._limb(d, [288, 432, 322, 585], self.shirt, 44)
            hand = (322, 597)
        for hx, hy in ((118, 597), hand):
            d.ellipse(P(hx - 25, hy - 25, hx + 25, hy + 25), fill=skin, outline=OUT, width=lw)
        # neck + torso
        d.rectangle(P(197, 355, 243, 410), fill=skin, outline=OUT, width=lw)
        if self.dress:
            d.rounded_rectangle(P(140, 395, 300, 560), radius=55 * self._k, fill=self.shirt, outline=OUT, width=lw)
            d.polygon(P(150, 480, 290, 480, 335, 665, 105, 665), fill=self.shirt, outline=OUT, width=lw)
            d.line(P(152, 480, 288, 480), fill=_shade(self.shirt, 0.75), width=int(10 * self._k))
            d.line(P(110, 650, 330, 650), fill=_shade(self.shirt, 0.8), width=int(8 * self._k))
        else:
            d.rounded_rectangle(P(136, 395, 304, 618), radius=55 * self._k, fill=self.shirt, outline=OUT, width=lw)
            d.rectangle(P(140, 590, 300, 612), fill=_shade(self.pants, 0.9))
            d.rectangle(P(208, 592, 232, 610), fill=(230, 200, 90))
        d.polygon(P(196, 397, 244, 397, 220, 440), fill=skin)
        d.line(P(196, 397, 220, 440, 244, 397), fill=_shade(self.shirt, 0.7), width=int(5 * self._k))

    def _draw_hair_back(self, d):
        P, lw, hair, st = self._p, self._lw, self.hair, self.style
        if st == "long":
            d.rounded_rectangle(P(50, 110, 390, 470), radius=130 * self._k, fill=hair, outline=OUT, width=lw)
        elif st == "ponytail":
            d.ellipse(P(330, 120, 425, 340), fill=hair, outline=OUT, width=lw)
        elif st == "pigtails":
            d.ellipse(P(5, 170, 95, 330), fill=hair, outline=OUT, width=lw)
            d.ellipse(P(345, 170, 435, 330), fill=hair, outline=OUT, width=lw)
        elif st == "bun":
            d.ellipse(P(160, 12, 280, 120), fill=hair, outline=OUT, width=lw)
        elif st == "bob":
            d.rounded_rectangle(P(48, 100, 392, 360), radius=120 * self._k, fill=hair, outline=OUT, width=lw)

    def _draw_hair_front(self, d):
        P, lw, hair, st = self._p, self._lw, self.hair, self.style
        if st == "bald":
            d.ellipse(P(52, 165, 108, 255), fill=hair, outline=OUT, width=lw)
            d.ellipse(P(332, 165, 388, 255), fill=hair, outline=OUT, width=lw)
            return
        d.chord(P(54, 38, 386, 292), 180, 360, fill=hair, outline=OUT, width=lw)
        if st in ("short", "side"):
            for x in (105, 150, 195, 245, 290, 335):
                d.ellipse(P(x - 26, 140, x + 26, 186), fill=hair)
            if st == "side":
                d.polygon(P(70, 175, 120, 110, 250, 95, 370, 165, 345, 105, 210, 62, 90, 100), fill=hair)
        elif st == "spiky":
            for i, x in enumerate(range(90, 360, 45)):
                top = 6 + (i % 2) * 22
                d.polygon(P(x - 34, 100, x + 4, top, x + 40, 100), fill=hair, outline=OUT)
            d.chord(P(58, 44, 382, 286), 180, 360, fill=hair)
        elif st == "curly":
            import math
            for a in range(180, 361, 18):
                x = 220 + 160 * math.cos(math.radians(a))
                y = 170 + 125 * math.sin(math.radians(a))
                d.ellipse(P(x - 40, y - 40, x + 40, y + 40), fill=hair, outline=OUT, width=lw)
            d.chord(P(70, 60, 370, 280), 180, 360, fill=hair)
        else:  # female styles: side-swept bangs
            d.polygon(P(62, 180, 95, 120, 200, 85, 330, 110, 380, 185, 330, 150, 230, 165, 140, 140), fill=hair)
            if st == "bun":
                d.rounded_rectangle(P(52, 150, 98, 300), radius=22 * self._k, fill=hair, outline=OUT, width=lw)
                d.rounded_rectangle(P(342, 150, 388, 300), radius=22 * self._k, fill=hair, outline=OUT, width=lw)
            if st in ("long", "bob"):
                d.rounded_rectangle(P(48, 150, 102, 360 if st == "bob" else 420), radius=26 * self._k,
                                    fill=hair, outline=OUT, width=lw)
                d.rounded_rectangle(P(338, 150, 392, 360 if st == "bob" else 420), radius=26 * self._k,
                                    fill=hair, outline=OUT, width=lw)
            if st == "pigtails":
                for x in (60, 380):
                    d.ellipse(P(x - 16, 175, x + 16, 205), fill=(255, 90, 120), outline=OUT, width=lw)

    def _draw_head(self, d, eyes_open, mouth, emotion, look):
        P, lw, skin, k = self._p, self._lw, self.skin, self._k
        cx, cy = 220, 215
        d.ellipse(P(40, 200, 98, 264), fill=skin, outline=OUT, width=lw)
        d.ellipse(P(342, 200, 400, 264), fill=skin, outline=OUT, width=lw)
        d.ellipse(P(60, 55, 380, 375), fill=skin, outline=OUT, width=lw)
        if self.beard:
            d.chord(P(78, 200, 362, 392), 0, 180, fill=self.hair, outline=OUT, width=lw)
        self._draw_hair_front(d)

        brow = _shade(self.hair, 0.55) if not self.kind.startswith("old") else (110, 110, 118)
        ey = cy + 30
        big = emotion in ("surprised", "scared")
        for side, ex in ((-1, cx - 65), (1, cx + 65)):
            rx, ry = (34, 42) if big else (30, 36)
            if eyes_open:
                d.ellipse(P(ex - rx, ey - ry, ex + rx, ey + ry), fill=WHITE, outline=OUT, width=max(1, int(4 * k)))
                pr = 14 if big else 17
                px, py = ex + look * 10, ey + 4
                d.ellipse(P(px - pr, py - pr, px + pr, py + pr), fill=(40, 30, 35))
                d.ellipse(P(px - pr * 0.55 - 4, py - pr * 0.6 - 4, px - pr * 0.55 + 5, py - pr * 0.6 + 5), fill=WHITE)
            else:
                d.arc(P(ex - 26, ey - 22, ex + 26, ey + 10), 20, 160, fill=OUT, width=max(1, int(6 * k)))
            if self.kind in ("female", "girl", "old_female"):
                top = ey - (ry if eyes_open else -4)
                for dx, dy in ((0.55, -0.15), (0.95, 0.25)):
                    x1 = ex + side * rx * dx
                    y1 = top + ry * dy * (1 if eyes_open else 0.3)
                    d.line(P(x1, y1 + 4, x1 + side * 16, y1 - 12), fill=OUT, width=max(1, int(6 * k)))
            inner_x, outer_x = ex - side * 28, ex + side * 28
            base = ey - 60
            iy, oy = {"angry": (base + 16, base - 6), "sad": (base - 12, base + 4), "scared": (base - 12, base + 2),
                      "surprised": (base - 16, base - 14), "happy": (base - 4, base - 7),
                      "excited": (base - 8, base - 10)}.get(emotion, (base, base - 2))
            d.line(P(inner_x, iy, outer_x, oy), fill=brow, width=max(1, int(10 * k)))
            for bx, by in ((inner_x, iy), (outer_x, oy)):
                d.ellipse(P(bx - 5, by - 5, bx + 5, by + 5), fill=brow)
        if self.glasses:
            for ex in (cx - 65, cx + 65):
                d.ellipse(P(ex - 46, ey - 44, ex + 46, ey + 44), outline=(50, 50, 60), width=max(1, int(6 * k)))
            d.line(P(cx - 19, ey - 6, cx + 19, ey - 6), fill=(50, 50, 60), width=max(1, int(6 * k)))
        if emotion in ("happy", "excited") or (mouth and emotion == "neutral" and False):
            blush = _mix(skin, (255, 110, 130), 0.35)
            d.ellipse(P(cx - 145, cy + 72, cx - 90, cy + 100), fill=blush)
            d.ellipse(P(cx + 90, cy + 72, cx + 145, cy + 100), fill=blush)
        d.arc(P(cx - 14, cy + 66, cx + 14, cy + 90), 20, 160, fill=_shade(skin, 0.7), width=max(1, int(5 * k)))
        self._draw_mouth(d, cx, cy + 118, mouth, emotion)

    def _draw_mouth(self, d, mx, my, mouth, emo):
        P, lw, k = self._p, self._lw, self._k
        lw7 = max(1, int(7 * k))
        if mouth == 0:
            if emo in ("happy", "excited"):
                d.arc(P(mx - 42, my - 30, mx + 42, my + 14), 20, 160, fill=OUT, width=lw7)
            elif emo == "sad":
                d.arc(P(mx - 32, my + 2, mx + 32, my + 34), 200, 340, fill=OUT, width=lw7)
            elif emo in ("surprised", "scared"):
                d.ellipse(P(mx - 14, my - 8, mx + 14, my + 20), fill=MOUTH, outline=OUT, width=lw)
            elif emo == "angry":
                d.arc(P(mx - 28, my + 4, mx + 28, my + 26), 210, 330, fill=OUT, width=lw7)
            else:
                d.arc(P(mx - 28, my - 16, mx + 28, my + 8), 30, 150, fill=OUT, width=lw7)
            return
        wd, ht = 30 + 9 * mouth, 10 + 13 * mouth
        if emo in ("happy", "excited"):
            box = (mx - wd, my - ht * 0.9, mx + wd, my + ht * 1.3)
            d.chord(P(*box), 0, 180, fill=MOUTH, outline=OUT, width=lw)
            top = (box[1] + box[3]) / 2
            d.chord(P(mx - wd * 0.55, my + ht * 0.45, mx + wd * 0.55, my + ht * 1.15), 0, 180, fill=TONGUE)
            if mouth >= 2:
                d.rectangle(P(mx - wd * 0.72, top + 2, mx + wd * 0.72, top + ht * 0.28), fill=WHITE)
        elif emo == "sad":
            box = (mx - wd * 0.8, my - ht * 0.2, mx + wd * 0.8, my + ht * 1.6)
            d.chord(P(*box), 180, 360, fill=MOUTH, outline=OUT, width=lw)
        else:
            box = (mx - wd * 0.72, my - ht * 0.45, mx + wd * 0.72, my + ht)
            d.ellipse(P(*box), fill=MOUTH, outline=OUT, width=lw)
            inset = (box[0] + 5, box[1] + 5, box[2] - 5, box[3] - 5)
            d.chord(P(*inset), 25, 155, fill=TONGUE)
            if mouth >= 2:
                d.chord(P(*inset), 205, 335, fill=WHITE)

    def _draw_photo_head(self, img, d, mouth):
        k = self._k
        D = max(8, int(340 * k))
        cx, cy = 220 * k, 212 * k
        face = self._face_cache.get(D)
        if face is None:
            face = self.face.resize((D, D), LANCZOS)
            self._face_cache = {D: face}
        split = int(D * 0.70)
        shift = int(D * 0.045 * mouth)
        comp = Image.new("RGB", (D, D + shift))
        comp.paste(face.crop((0, 0, D, split)), (0, 0))
        if shift > 0:
            comp.paste(face.crop((0, split - 1, D, split)).resize((D, shift)), (0, split))
            mw = int(D * 0.105)
            ImageDraw.Draw(comp).ellipse((D // 2 - mw, split - int(D * 0.012), D // 2 + mw,
                                          split + shift + int(D * 0.012)), fill=(70, 22, 30))
        comp.paste(face.crop((0, split, D, D)), (0, split + shift))
        mask = Image.new("L", (D, D + shift), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, D - 1, D + shift - 1), fill=255)
        ring = int(11 * k)
        d.ellipse((cx - D / 2 - ring, cy - D / 2 - ring, cx + D / 2 + ring, cy + D / 2 + shift + ring),
                  fill=WHITE, outline=OUT, width=max(1, int(4 * k)))
        img.paste(comp, (int(cx - D / 2), int(cy - D / 2)), mask)
