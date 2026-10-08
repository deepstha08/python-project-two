"""Cartoon backgrounds drawn in code: room, kitchen, park, city, school,
office, beach, forest, space, cafe — each in day, sunset or night light."""
import math
import random
import zlib

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .fonts import get_font

SKY = {"day": ((96, 175, 245), (200, 234, 255)), "sunset": ((255, 112, 98), (255, 206, 140)),
       "night": ((12, 16, 48), (52, 62, 125))}


def _grad(w, h, top, bot):
    t = np.linspace(0, 1, max(1, h)).reshape(-1, 1, 1)
    arr = (1 - t) * np.array(top, float) + t * np.array(bot, float)
    arr = np.repeat(arr, max(1, w), axis=1)
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGB")


def _shade(c, f):
    return tuple(max(0, min(255, int(v * f))) for v in c[:3])


def _light(time):
    """Colour transform for the time of day."""
    if time == "night":
        return lambda c: (int(c[0] * 0.42 + 10), int(c[1] * 0.45 + 12), int(c[2] * 0.55 + 38))
    if time == "sunset":
        return lambda c: (min(255, int(c[0] * 0.98 + 22)), int(c[1] * 0.84), int(c[2] * 0.76))
    return lambda c: tuple(c[:3])


def _cloud(d, x, y, s, col):
    for (a, b, c, e) in ((-1, -0.35, 1, 0.4), (-0.6, -0.8, 0.2, 0.2), (-0.1, -0.7, 0.7, 0.15), (0.4, -0.45, 1.2, 0.35)):
        d.ellipse((x + a * s, y + b * s, x + c * s, y + e * s), fill=col)


def _sky(img, d, W, H, time, rng, bottom, u):
    top, bot = SKY[time]
    img.paste(_grad(W, bottom, top, bot), (0, 0))
    if time == "night":
        for _ in range(int(140 * W / 1200)):
            x, y = rng.randrange(W), rng.randrange(max(1, int(bottom * 0.92)))
            r = rng.choice([1, 1, 2, 2, 3]) * u
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 230, rng.randint(150, 255)))
        mx, my, mr = W * 0.78, bottom * 0.2, 62 * u
        d.ellipse((mx - mr * 1.8, my - mr * 1.8, mx + mr * 1.8, my + mr * 1.8), fill=(255, 255, 220, 30))
        d.ellipse((mx - mr, my - mr, mx + mr, my + mr), fill=(250, 245, 215))
        d.ellipse((mx - mr * 0.5, my - mr * 0.3, mx - mr * 0.15, my + mr * 0.05), fill=(225, 220, 190))
    else:
        sx, sy, sr = (W * 0.8, bottom * 0.18, 75 * u) if time == "day" else (W * 0.7, bottom * 0.82, 120 * u)
        col = (255, 236, 140) if time == "day" else (255, 190, 110)
        d.ellipse((sx - sr * 1.7, sy - sr * 1.7, sx + sr * 1.7, sy + sr * 1.7), fill=col + (45,))
        d.ellipse((sx - sr, sy - sr, sx + sr, sy + sr), fill=col)
        ccol = (255, 255, 255, 225) if time == "day" else (255, 220, 200, 200)
        for _ in range(rng.randint(3, 5)):
            _cloud(d, rng.uniform(0, W), rng.uniform(bottom * 0.1, bottom * 0.6), rng.uniform(55, 95) * u, ccol)


def _window(img, d, box, time, rng, u, frame=(255, 255, 255)):
    x0, y0, x1, y1 = [int(v) for v in box]
    win = Image.new("RGB", (x1 - x0, y1 - y0))
    wd = ImageDraw.Draw(win, "RGBA")
    _sky(win, wd, win.width, win.height, time, rng, win.height, u * 0.6)
    L = _light(time)
    wd.ellipse((-win.width * 0.3, win.height * 0.7, win.width * 0.8, win.height * 1.6), fill=L((120, 190, 110)))
    wd.ellipse((win.width * 0.3, win.height * 0.75, win.width * 1.4, win.height * 1.6), fill=L((95, 170, 95)))
    img.paste(win, (x0, y0))
    fw = int(16 * u)
    d.rectangle((x0, y0, x1, y1), outline=frame, width=fw)
    d.line(((x0 + x1) / 2, y0, (x0 + x1) / 2, y1), fill=frame, width=fw // 2)
    d.line((x0, (y0 + y1) / 2, x1, (y0 + y1) / 2), fill=frame, width=fw // 2)


def _floor_planks(img, d, W, H, FY, col, u):
    img.paste(_grad(W, H - FY, _shade(col, 0.88), _shade(col, 1.08)), (0, FY))
    for i in range(1, 14):
        y = FY + (H - FY) * (i / 14) ** 1.5
        d.line((0, y, W, y), fill=_shade(col, 0.78) + (160,), width=max(1, int(3 * u)))


def _night_indoor(img, time):
    if time == "night":
        tint = Image.new("RGB", img.size, (25, 30, 70))
        return Image.blend(img, tint, 0.28)
    if time == "sunset":
        tint = Image.new("RGB", img.size, (255, 150, 80))
        return Image.blend(img, tint, 0.10)
    return img


def _plant(d, x, y, u, L):
    d.rounded_rectangle((x - 45 * u, y - 90 * u, x + 45 * u, y), radius=12 * u, fill=L((205, 110, 70)))
    for a in range(-70, 71, 28):
        ex = x + math.sin(math.radians(a)) * 110 * u
        ey = y - 90 * u - math.cos(math.radians(a)) * 150 * u
        d.line((x, y - 90 * u, ex, ey), fill=L((60, 140, 70)), width=int(26 * u))
        d.ellipse((ex - 22 * u, ey - 22 * u, ex + 22 * u, ey + 22 * u), fill=L((80, 170, 85)))


# ------------------------------------------------------------- settings
def room(img, d, W, H, time, rng, u):
    FY = int(H * 0.66)
    wall = rng.choice([(246, 222, 196), (214, 234, 248), (232, 220, 245), (222, 240, 222), (250, 230, 230)])
    img.paste(_grad(W, FY, _shade(wall, 1.04), _shade(wall, 0.9)), (0, 0))
    for x in range(0, W, int(90 * u)):
        d.rectangle((x, 0, x + int(40 * u), FY), fill=_shade(wall, 0.95) + (110,))
    _window(img, d, (W * 0.07, H * 0.15, W * 0.40, H * 0.40), time, rng, u)
    cur = rng.choice([(220, 80, 90), (90, 120, 200), (120, 170, 110), (230, 170, 70)])
    for x0, x1 in ((W * 0.03, W * 0.10), (W * 0.37, W * 0.44)):
        d.polygon((x0, H * 0.12, x1, H * 0.12, x1 - 10 * u, H * 0.45, x0 - 10 * u, H * 0.45), fill=cur)
    d.rectangle((W * 0.02, H * 0.11, W * 0.45, H * 0.125), fill=(120, 85, 60))
    # picture
    px0, py0, px1, py1 = W * 0.60, H * 0.16, W * 0.86, H * 0.31
    d.rectangle((px0, py0, px1, py1), fill=(120, 85, 60))
    d.rectangle((px0 + 18 * u, py0 + 18 * u, px1 - 18 * u, py1 - 18 * u), fill=(190, 225, 245))
    d.polygon((px0 + 18 * u, py1 - 18 * u, (px0 + px1) / 2 - 30 * u, py0 + 70 * u, (px0 + px1) / 2 + 40 * u,
               py1 - 18 * u), fill=(110, 170, 120))
    d.polygon(((px0 + px1) / 2, py1 - 18 * u, px1 - 70 * u, py0 + 90 * u, px1 - 18 * u, py1 - 18 * u),
              fill=(90, 150, 105))
    # sofa
    sofa = rng.choice([(110, 140, 200), (200, 110, 100), (120, 170, 140), (180, 140, 200)])
    d.rounded_rectangle((W * 0.50, H * 0.47, W * 0.97, H * 0.62), radius=40 * u, fill=_shade(sofa, 0.85))
    d.rounded_rectangle((W * 0.47, H * 0.56, W * 1.0, H * 0.68), radius=30 * u, fill=sofa)
    for x in (W * 0.62, W * 0.80):
        d.rounded_rectangle((x - 60 * u, H * 0.50, x + 60 * u, H * 0.58), radius=20 * u, fill=_shade(sofa, 1.12))
    # lamp
    lx = W * 0.47
    if time == "night":
        d.ellipse((lx - 220 * u, H * 0.18, lx + 220 * u, H * 0.48), fill=(255, 230, 150, 50))
    d.line((lx, H * 0.30, lx, FY), fill=(70, 60, 60), width=int(10 * u))
    d.polygon((lx - 60 * u, H * 0.30, lx + 60 * u, H * 0.30, lx + 40 * u, H * 0.23, lx - 40 * u, H * 0.23),
              fill=(255, 235, 170))
    _floor_planks(img, d, W, H, FY, rng.choice([(196, 140, 96), (170, 120, 80), (210, 170, 130)]), u)
    d.rectangle((0, FY - int(24 * u), W, FY), fill=(255, 255, 255))
    rug = rng.choice([(230, 120, 110), (110, 160, 210), (240, 200, 110)])
    d.ellipse((W * 0.12, H * 0.76, W * 0.88, H * 0.86), fill=rug + (200,))
    d.ellipse((W * 0.18, H * 0.775, W * 0.82, H * 0.845), outline=(255, 255, 255, 160), width=int(6 * u))
    return _night_indoor(img, time)


def kitchen(img, d, W, H, time, rng, u):
    FY = int(H * 0.66)
    tile = rng.choice([(235, 245, 248), (250, 240, 225), (230, 240, 230)])
    img.paste(Image.new("RGB", (W, FY), tile), (0, 0))
    step = int(70 * u)
    for x in range(0, W, step):
        d.line((x, 0, x, FY), fill=_shade(tile, 0.9), width=max(1, int(2 * u)))
    for y in range(0, FY, step):
        d.line((0, y, W, y), fill=_shade(tile, 0.9), width=max(1, int(2 * u)))
    _window(img, d, (W * 0.06, H * 0.14, W * 0.34, H * 0.36), time, rng, u)
    wood = rng.choice([(190, 130, 80), (120, 160, 190), (150, 190, 150), (230, 230, 235)])
    d.rectangle((W * 0.38, H * 0.10, W * 0.72, H * 0.30), fill=wood, outline=_shade(wood, 0.7), width=int(5 * u))
    for x in (W * 0.495, W * 0.61):
        d.line((x, H * 0.10, x, H * 0.30), fill=_shade(wood, 0.7), width=int(5 * u))
    d.rectangle((0, H * 0.53, W, FY), fill=wood)
    for x in np.arange(0, W, 180 * u):
        d.rectangle((x + 10 * u, H * 0.545, x + 170 * u, FY - 12 * u), outline=_shade(wood, 0.75), width=int(5 * u))
        d.ellipse((x + 80 * u, H * 0.565, x + 100 * u, H * 0.565 + 20 * u), fill=(90, 90, 95))
    d.rectangle((0, H * 0.505, W, H * 0.535), fill=(150, 150, 160))
    # fridge
    d.rounded_rectangle((W * 0.76, H * 0.20, W * 0.96, FY), radius=24 * u, fill=(238, 240, 245),
                        outline=(160, 165, 175), width=int(6 * u))
    d.line((W * 0.76, H * 0.36, W * 0.96, H * 0.36), fill=(160, 165, 175), width=int(6 * u))
    d.rounded_rectangle((W * 0.78, H * 0.27, W * 0.79, H * 0.33), radius=6 * u, fill=(150, 150, 160))
    # pot + fruit
    d.rounded_rectangle((W * 0.42, H * 0.45, W * 0.55, H * 0.505), radius=14 * u, fill=(200, 70, 60))
    d.chord((W * 0.58, H * 0.47, W * 0.70, H * 0.53), 0, 180, fill=(240, 240, 240))
    for i, c in enumerate([(240, 80, 60), (250, 200, 60), (120, 200, 80)]):
        x = W * 0.605 + i * 34 * u
        d.ellipse((x - 22 * u, H * 0.465, x + 22 * u, H * 0.465 + 44 * u), fill=c)
    # checker floor
    a, b = (235, 235, 235), (90, 110, 140)
    rows, cell = 8, 150 * u

    def px(c, y):  # x of grid column c at height y (columns converge toward the wall)
        s = 0.55 + 0.75 * (y - FY) / (H - FY)
        return W / 2 + c * cell * s

    for r in range(rows):
        y0 = FY + (H - FY) * (r / rows) ** 1.3
        y1 = FY + (H - FY) * ((r + 1) / rows) ** 1.3
        for c in range(-10, 10):
            d.polygon((px(c, y0), y0, px(c + 1, y0), y0, px(c + 1, y1), y1, px(c, y1), y1),
                      fill=a if (c + r) % 2 else b)
    return _night_indoor(img, time)


def park(img, d, W, H, time, rng, u):
    L = _light(time)
    GY = int(H * 0.58)
    _sky(img, d, W, H, time, rng, GY + int(60 * u), u)
    d.ellipse((-W * 0.25, GY - H * 0.10, W * 0.65, GY + H * 0.25), fill=L((130, 200, 115)))
    d.ellipse((W * 0.35, GY - H * 0.07, W * 1.3, GY + H * 0.25), fill=L((105, 182, 100)))
    img.paste(_grad(W, H - GY - int(40 * u), L((115, 205, 95)), L((70, 160, 60))), (0, GY + int(40 * u)))
    d = ImageDraw.Draw(img, "RGBA")
    for tx in (W * 0.08, W * 0.30, W * 0.88):
        th = rng.uniform(0.9, 1.15)
        d.rectangle((tx - 22 * u, GY - 120 * u * th, tx + 22 * u, GY + 70 * u), fill=L((120, 80, 50)))
        for (ox, oy, r) in ((0, -260, 130), (-90, -180, 100), (90, -180, 100), (0, -150, 110)):
            d.ellipse((tx + (ox - r) * u, GY + (oy * th - r) * u, tx + (ox + r) * u, GY + (oy * th + r) * u),
                      fill=L((60, 150, 70) if oy < -200 else (75, 165, 80)))
    d.polygon((W * 0.44, GY + 40 * u, W * 0.56, GY + 40 * u, W * 0.92, H, W * 0.08, H), fill=L((232, 210, 160)))
    bx, by = W * 0.70, GY + 70 * u
    for i in range(3):
        d.rectangle((bx, by + i * 22 * u, bx + 230 * u, by + i * 22 * u + 14 * u), fill=L((170, 105, 60)))
    d.rectangle((bx + 15 * u, by, bx + 25 * u, by + 120 * u), fill=L((70, 60, 60)))
    d.rectangle((bx + 205 * u, by, bx + 215 * u, by + 120 * u), fill=L((70, 60, 60)))
    for _ in range(60):
        x, y = rng.uniform(0, W), rng.uniform(GY + 60 * u, H)
        if abs(x - W / 2) < (y - GY) / (H - GY) * W * 0.45 + W * 0.07:
            continue
        r = 8 * u
        d.ellipse((x - r, y - r, x + r, y + r), fill=L(rng.choice([(255, 90, 120), (255, 220, 70), (255, 255, 255),
                                                                   (180, 120, 255)])))
    if time == "night":
        for _ in range(25):
            x, y, r = rng.uniform(0, W), rng.uniform(GY - 200 * u, H * 0.9), 5 * u
            d.ellipse((x - r * 3, y - r * 3, x + r * 3, y + r * 3), fill=(255, 240, 120, 40))
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 245, 150))
    return img


def city(img, d, W, H, time, rng, u):
    L = _light(time)
    GY = int(H * 0.64)
    _sky(img, d, W, H, time, rng, GY, u)
    x = -50 * u
    while x < W:
        bw, bh = rng.uniform(90, 170) * u, rng.uniform(0.18, 0.40) * H
        d.rectangle((x, GY - bh, x + bw, GY), fill=L((150, 165, 190)))
        x += bw + rng.uniform(0, 20) * u
    x = -30 * u
    while x < W:
        bw, bh = rng.uniform(170, 280) * u, rng.uniform(0.14, 0.32) * H
        col = L(rng.choice([(205, 120, 100), (120, 140, 170), (220, 190, 140), (150, 120, 160), (110, 160, 150)]))
        d.rectangle((x, GY - bh, x + bw, GY), fill=col)
        d.rectangle((x - 6 * u, GY - bh - 14 * u, x + bw + 6 * u, GY - bh), fill=_shade(col, 0.8))
        ww, wh, gap = 34 * u, 46 * u, 22 * u
        cols = max(1, int((bw - gap) // (ww + gap)))
        off = (bw - cols * (ww + gap) + gap) / 2
        yy = GY - bh + 30 * u
        while yy + wh < GY - 30 * u:
            for c in range(cols):
                wx = x + off + c * (ww + gap)
                lit = time == "night" and rng.random() < 0.6
                wcol = (255, 220, 120) if lit else ((190, 225, 250) if time != "night" else (50, 60, 90))
                d.rectangle((wx, yy, wx + ww, yy + wh), fill=wcol)
            yy += wh + gap
        x += bw + rng.uniform(10, 40) * u
    d.rectangle((0, GY, W, H * 0.86), fill=L((190, 190, 195)))
    for xx in np.arange(0, W, 160 * u):
        d.line((xx, GY, xx - 60 * u, H * 0.86), fill=L((165, 165, 172)), width=int(4 * u))
    d.rectangle((0, H * 0.86, W, H * 0.875), fill=L((140, 140, 148)))
    d.rectangle((0, H * 0.875, W, H), fill=L((70, 72, 80)))
    for xx in np.arange(0, W, 220 * u):
        d.rectangle((xx, H * 0.935, xx + 120 * u, H * 0.945), fill=(245, 235, 180))
    lx = W * 0.92
    d.line((lx, GY - 380 * u, lx, H * 0.86), fill=(60, 65, 75), width=int(14 * u))
    d.rounded_rectangle((lx - 60 * u, GY - 400 * u, lx + 10 * u, GY - 370 * u), radius=10 * u, fill=(60, 65, 75))
    if time == "night":
        d.ellipse((lx - 230 * u, GY - 420 * u, lx + 130 * u, GY - 120 * u), fill=(255, 230, 150, 45))
    d.ellipse((lx - 50 * u, GY - 378 * u, lx, GY - 355 * u), fill=(255, 235, 160))
    return img


def school(img, d, W, H, time, rng, u):
    FY = int(H * 0.66)
    wall = (240, 228, 200)
    img.paste(_grad(W, FY, wall, _shade(wall, 0.92)), (0, 0))
    d.rectangle((0, H * 0.48, W, FY), fill=(170, 120, 80))
    d.rectangle((0, H * 0.475, W, H * 0.485), fill=(120, 85, 60))
    bx0, by0, bx1, by1 = W * 0.08, H * 0.13, W * 0.92, H * 0.38
    d.rectangle((bx0 - 16 * u, by0 - 16 * u, bx1 + 16 * u, by1 + 16 * u), fill=(140, 95, 60))
    d.rectangle((bx0, by0, bx1, by1), fill=(45, 90, 70))
    f = get_font(int(64 * u), bold=False)
    lines = rng.sample(["2 + 2 = 4", "A B C", "Homework: p. 42", "Be kind!", "E = mc²", "Quiz Friday"], 2)
    d.text((bx0 + 60 * u, by0 + 50 * u), lines[0], font=f, fill=(235, 240, 230, 220))
    d.text((bx0 + 60 * u, by0 + 160 * u), lines[1], font=f, fill=(235, 240, 230, 200))
    d.rectangle((bx0, by1 + 4 * u, bx1, by1 + 22 * u), fill=(120, 80, 50))
    cx, cy, r = W * 0.5, H * 0.065, 48 * u
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255), outline=(60, 60, 60), width=int(6 * u))
    d.line((cx, cy, cx, cy - r * 0.7), fill=(30, 30, 30), width=int(5 * u))
    d.line((cx, cy, cx + r * 0.5, cy), fill=(30, 30, 30), width=int(5 * u))
    for xx in np.arange(W * 0.02, W, 300 * u):
        d.rectangle((xx, H * 0.55, xx + 230 * u, H * 0.575), fill=(200, 150, 90))
        d.rectangle((xx + 20 * u, H * 0.575, xx + 34 * u, FY), fill=(90, 90, 100))
        d.rectangle((xx + 196 * u, H * 0.575, xx + 210 * u, FY), fill=(90, 90, 100))
    _floor_planks(img, d, W, H, FY, (200, 200, 190), u)
    return _night_indoor(img, time)


def office(img, d, W, H, time, rng, u):
    FY = int(H * 0.66)
    L = _light(time)
    img.paste(_grad(W, FY, (225, 232, 240), (200, 210, 222)), (0, 0))
    wx0, wy0, wx1, wy1 = int(W * 0.04), int(H * 0.10), int(W * 0.96), int(H * 0.47)
    win = Image.new("RGB", (wx1 - wx0, wy1 - wy0))
    wd = ImageDraw.Draw(win, "RGBA")
    _sky(win, wd, win.width, win.height, time, rng, win.height, u)
    x = 0
    while x < win.width:
        bw, bh = rng.uniform(80, 160) * u, rng.uniform(0.3, 0.8) * win.height
        wd.rectangle((x, win.height - bh, x + bw, win.height), fill=L((110, 130, 160)))
        for yy in np.arange(win.height - bh + 15 * u, win.height, 40 * u):
            for xx in np.arange(x + 12 * u, x + bw - 20 * u, 32 * u):
                if rng.random() < (0.5 if time == "night" else 0.25):
                    wd.rectangle((xx, yy, xx + 14 * u, yy + 20 * u),
                                 fill=(255, 225, 130) if time == "night" else (200, 225, 245))
        x += bw + 8 * u
    img.paste(win, (wx0, wy0))
    for xx in np.linspace(wx0, wx1, 5):
        d.line((xx, wy0, xx, wy1), fill=(90, 95, 110), width=int(12 * u))
    d.rectangle((wx0, wy0, wx1, wy1), outline=(90, 95, 110), width=int(14 * u))
    d.rectangle((W * 0.55, H * 0.55, W * 0.97, H * 0.575), fill=(150, 110, 75))
    d.rectangle((W * 0.57, H * 0.575, W * 0.95, FY), fill=(130, 95, 65))
    d.rounded_rectangle((W * 0.66, H * 0.45, W * 0.86, H * 0.535), radius=10 * u, fill=(40, 45, 55))
    d.rectangle((W * 0.675, H * 0.46, W * 0.845, H * 0.525), fill=(110, 180, 230))
    d.rectangle((W * 0.75, H * 0.535, W * 0.77, H * 0.55), fill=(40, 45, 55))
    _plant(d, W * 0.12, FY, u, lambda c: c)
    img.paste(_grad(W, H - FY, (130, 140, 160), (105, 115, 135)), (0, FY))
    return _night_indoor(img, time) if time != "night" else img


def beach(img, d, W, H, time, rng, u):
    L = _light(time)
    SY, GY = int(H * 0.50), int(H * 0.62)
    _sky(img, d, W, H, time, rng, SY, u)
    img.paste(_grad(W, GY - SY, L((40, 160, 210)), L((90, 210, 220))), (0, SY))
    d = ImageDraw.Draw(img, "RGBA")
    for i in range(7):
        y = SY + (GY - SY) * (i + 0.5) / 7
        for xx in np.arange(rng.uniform(0, 80) * u, W, 160 * u):
            d.arc((xx, y - 8 * u, xx + 80 * u, y + 8 * u), 200, 340, fill=(255, 255, 255, 150), width=int(4 * u))
    img.paste(_grad(W, H - GY, L((245, 222, 170)), L((225, 195, 140))), (0, GY))
    d = ImageDraw.Draw(img, "RGBA")
    pts = [(xx, GY + math.sin(xx / (70 * u)) * 10 * u) for xx in np.arange(0, W + 20, 20)]
    d.line(pts, fill=(255, 255, 255, 220), width=int(14 * u))
    px, py = W * 0.12, GY + 80 * u
    trunk = [(px + math.sin(t * 1.2) * 70 * u, py - t * 520 * u) for t in np.linspace(0, 1, 12)]
    d.line(trunk, fill=L((150, 105, 65)), width=int(40 * u), joint="curve")
    tx, ty = trunk[-1]
    for a in (-160, -120, -60, -20, 20, 200):
        ex, ey = tx + math.cos(math.radians(a)) * 230 * u, ty + math.sin(math.radians(a)) * 120 * u + 60 * u
        d.polygon((tx, ty, (tx + ex) / 2, (ty + ey) / 2 - 50 * u, ex, ey, (tx + ex) / 2, (ty + ey) / 2 + 10 * u),
                  fill=L((60, 160, 80)))
    ux, uy = W * 0.84, GY + 40 * u
    d.line((ux, uy - 260 * u, ux - 20 * u, uy + 140 * u), fill=(240, 240, 240), width=int(10 * u))
    for i, c in enumerate([(240, 80, 80), (255, 255, 255)] * 3):
        d.pieslice((ux - 210 * u, uy - 380 * u, ux + 210 * u, uy - 120 * u), 180 + i * 30, 210 + i * 30, fill=L(c))
    for _ in range(14):
        x, y, r = rng.uniform(0, W), rng.uniform(GY + 120 * u, H), 9 * u
        d.ellipse((x - r, y - r * 0.7, x + r, y + r * 0.7), fill=L((255, 200, 190)))
    return img


def forest(img, d, W, H, time, rng, u):
    L = _light(time)
    GY = int(H * 0.60)
    _sky(img, d, W, H, time, rng, GY, u)
    for layer, (col, scale, base) in enumerate([((90, 140, 110), 0.7, 0.0), ((55, 120, 75), 1.0, 30), ((40, 100, 60), 1.3, 60)]):
        x = rng.uniform(-80, 0) * u
        while x < W + 100 * u:
            h = rng.uniform(250, 420) * u * scale
            w = h * 0.45
            yb = GY + base * u
            d.rectangle((x - 10 * u * scale, yb - 30 * u, x + 10 * u * scale, yb + 10 * u), fill=L((90, 65, 45)))
            for t in range(3):
                d.polygon((x - w * (1 - t * 0.22), yb - 30 * u - t * h * 0.28, x + w * (1 - t * 0.22),
                           yb - 30 * u - t * h * 0.28, x, yb - 30 * u - h * (0.55 + t * 0.2)), fill=L(col))
            x += w * rng.uniform(0.9, 1.4)
    img.paste(_grad(W, H - GY - int(60 * u), L((85, 140, 70)), L((60, 105, 50))), (0, GY + int(60 * u)))
    d = ImageDraw.Draw(img, "RGBA")
    for x in (W * 0.03, W * 0.97):
        d.rectangle((x - 50 * u, 0, x + 50 * u, H * 0.75), fill=L((100, 70, 45)))
        d.ellipse((x - 260 * u, -180 * u, x + 260 * u, 260 * u), fill=L((45, 110, 60)))
    for _ in range(6):
        x, y = rng.uniform(W * 0.1, W * 0.9), rng.uniform(H * 0.72, H * 0.95)
        d.rectangle((x - 8 * u, y - 30 * u, x + 8 * u, y), fill=(240, 235, 220))
        d.chord((x - 30 * u, y - 55 * u, x + 30 * u, y - 10 * u), 180, 360, fill=L((220, 60, 60)))
    if time == "night":
        for _ in range(40):
            x, y, r = rng.uniform(0, W), rng.uniform(H * 0.3, H * 0.9), 5 * u
            d.ellipse((x - r * 3, y - r * 3, x + r * 3, y + r * 3), fill=(255, 240, 120, 40))
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 245, 150))
    return img


def space(img, d, W, H, time, rng, u):
    GY = int(H * 0.64)
    img.paste(_grad(W, H, (8, 6, 28), (45, 22, 80)), (0, 0))
    neb = Image.new("RGBA", img.size, (0, 0, 0, 0))
    nd = ImageDraw.Draw(neb)
    for _ in range(5):
        x, y, r = rng.uniform(0, W), rng.uniform(0, GY), rng.uniform(150, 350) * u
        nd.ellipse((x - r, y - r * 0.6, x + r, y + r * 0.6), fill=rng.choice([(200, 60, 160, 70), (60, 100, 220, 70),
                                                                                (120, 60, 220, 70)]))
    neb = neb.filter(ImageFilter.GaussianBlur(80 * u))
    img.paste(neb, (0, 0), neb)
    d = ImageDraw.Draw(img, "RGBA")
    for _ in range(int(260 * W / 1200)):
        x, y, r = rng.uniform(0, W), rng.uniform(0, GY), rng.choice([1, 1, 2, 3]) * u
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, rng.randint(140, 255)))
    px, py, pr = W * 0.75, H * 0.18, 120 * u
    d.ellipse((px - pr, py - pr, px + pr, py + pr), fill=(230, 160, 90))
    d.chord((px - pr, py - pr, px + pr, py + pr), 200, 340, fill=(245, 190, 120))
    d.ellipse((px - pr * 1.8, py - pr * 0.35, px + pr * 1.8, py + pr * 0.35), outline=(240, 220, 180), width=int(10 * u))
    ex, ey, er = W * 0.18, H * 0.30, 55 * u
    d.ellipse((ex - er, ey - er, ex + er, ey + er), fill=(70, 140, 230))
    d.ellipse((ex - er * 0.5, ey - er * 0.6, ex + er * 0.3, ey + er * 0.1), fill=(90, 190, 110))
    d.ellipse((-W * 0.3, GY, W * 1.3, GY + H * 0.9), fill=(150, 150, 165))
    img.paste(_grad(W, H - GY - int(80 * u), (150, 150, 165), (110, 110, 125)), (0, GY + int(80 * u)))
    d = ImageDraw.Draw(img, "RGBA")
    for _ in range(9):
        x, y, r = rng.uniform(0, W), rng.uniform(GY + 60 * u, H), rng.uniform(25, 70) * u
        d.ellipse((x - r, y - r * 0.4, x + r, y + r * 0.4), fill=(120, 120, 135))
        d.arc((x - r, y - r * 0.4, x + r, y + r * 0.4), 20, 160, fill=(175, 175, 190), width=int(5 * u))
    return img


def cafe(img, d, W, H, time, rng, u):
    FY = int(H * 0.66)
    brick, mortar = (175, 90, 70), (215, 190, 170)
    img.paste(Image.new("RGB", (W, FY), mortar), (0, 0))
    bh, bw = 44 * u, 120 * u
    for r, y in enumerate(np.arange(0, FY, bh)):
        for x in np.arange(-(r % 2) * bw / 2, W, bw):
            d.rectangle((x + 4 * u, y + 4 * u, x + bw - 4 * u, y + bh - 4 * u),
                        fill=_shade(brick, rng.uniform(0.88, 1.08)))
    _window(img, d, (W * 0.06, H * 0.14, W * 0.38, H * 0.40), time, rng, u, frame=(70, 50, 40))
    mx0, my0, mx1, my1 = W * 0.56, H * 0.12, W * 0.92, H * 0.38
    d.rectangle((mx0 - 14 * u, my0 - 14 * u, mx1 + 14 * u, my1 + 14 * u), fill=(120, 80, 50))
    d.rectangle((mx0, my0, mx1, my1), fill=(40, 40, 42))
    f, f2 = get_font(int(70 * u)), get_font(int(46 * u), bold=False)
    d.text((mx0 + 40 * u, my0 + 30 * u), "MENU", font=f, fill=(255, 220, 120))
    for i, item in enumerate(["Coffee ....... 3", "Tea ............ 2", "Cake .......... 4"]):
        d.text((mx0 + 40 * u, my0 + (130 + i * 70) * u), item, font=f2, fill=(240, 240, 235))
    for lx in (W * 0.25, W * 0.5, W * 0.75):
        d.line((lx, 0, lx, H * 0.08), fill=(40, 40, 40), width=int(5 * u))
        d.chord((lx - 60 * u, H * 0.06, lx + 60 * u, H * 0.12), 180, 360, fill=(50, 50, 55))
        d.ellipse((lx - 160 * u, H * 0.07, lx + 160 * u, H * 0.20), fill=(255, 220, 140, 45))
        d.ellipse((lx - 18 * u, H * 0.085, lx + 18 * u, H * 0.10), fill=(255, 240, 180))
    d.rectangle((0, H * 0.52, W, FY), fill=(120, 80, 55))
    d.rectangle((0, H * 0.505, W, H * 0.525), fill=(80, 55, 40))
    d.rounded_rectangle((W * 0.70, H * 0.41, W * 0.86, H * 0.505), radius=12 * u, fill=(150, 155, 165))
    d.rectangle((W * 0.74, H * 0.47, W * 0.82, H * 0.48), fill=(60, 60, 65))
    for i in range(3):
        x = W * 0.15 + i * 70 * u
        d.rounded_rectangle((x, H * 0.47, x + 50 * u, H * 0.505), radius=8 * u, fill=(250, 250, 250))
    _floor_planks(img, d, W, H, FY, (120, 85, 60), u)
    return _night_indoor(img, time)


DRAWERS = {"room": room, "kitchen": kitchen, "park": park, "city": city, "school": school, "office": office,
           "beach": beach, "forest": forest, "space": space, "cafe": cafe}

_VIGNETTE = {}


def _vignette(img):
    key = img.size
    if key not in _VIGNETTE:
        w, h = img.size
        y, x = np.ogrid[-1:1:h * 1j, -1:1:w * 1j]
        r = np.sqrt(x ** 2 * 0.9 + y ** 2)
        mask = (255 * np.clip(1 - 0.30 * np.clip(r - 0.35, 0, None) ** 1.6, 0, 1)).astype(np.uint8)
        m = Image.fromarray(mask, "L")
        _VIGNETTE[key] = Image.merge("RGB", (m, m, m))
    return ImageChops.multiply(img, _VIGNETTE[key])


def render_background(setting, time, width, height, seed=0):
    """Return an RGB background of size (width, height)."""
    setting = setting if setting in DRAWERS else "room"
    time = time if time in SKY else "day"
    rng = random.Random(zlib.crc32(f"{setting}-{time}-{seed}".encode()))
    u = height / 1920.0
    img = Image.new("RGB", (width, height), (0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    img = DRAWERS[setting](img, d, width, height, time, rng, u) or img
    img = img.filter(ImageFilter.GaussianBlur(radius=1.6 * u))   # soft depth-of-field so characters pop
    return _vignette(img)
