"""Renders the final MP4: moving backgrounds, animated talking characters,
lip-sync, word-by-word captions, title hook, end card, music."""
import bisect
import math
import os
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import audio as A
from .fonts import font_size, get_font
from .scenes import render_background
from .script_parser import NARRATOR

try:
    BILINEAR = Image.Resampling.BILINEAR
except AttributeError:
    BILINEAR = Image.BILINEAR

LEAD_IN = 1.2        # time before the first line (title is readable)
SCENE_IN = 0.6       # pause at the start of later scenes
SCENE_OUT = 0.5      # pause at the end of every scene
BASE_GAP = 0.28      # pause between lines
FADE = 0.35
END_CARD = 3.0
TRANSIENT = {"surprised", "scared", "excited"}
LAYOUT = {0: [], 1: [0.5], 2: [0.29, 0.71], 3: [0.19, 0.5, 0.81]}
SCALE = {0: 1.15, 1: 1.18, 2: 1.10, 3: 0.90}


class Seg:
    def __init__(self, start, end, scene, line, clip):
        self.start, self.end, self.scene, self.line, self.clip = start, end, scene, line, clip
        self.speaker = line.speaker
        self.emotion = line.emotion
        self.visible = []
        self.emo_state = {}
        self.chunks = []         # list of (start, [(abs_start, abs_end, word), ...])
        self.chunk_starts = []
        self.caption_end = end


def plan_timeline(script, clips, extra_gap=0.0, end_card=True):
    segs, ranges, t, k = [], [], 0.0, 0
    for si, sc in enumerate(script.scenes):
        start = t
        t += LEAD_IN if si == 0 else SCENE_IN
        for ln in sc.lines:
            clip = clips[k]
            k += 1
            s = Seg(t, t + clip.duration, si, ln, clip)
            segs.append(s)
            t = s.end + BASE_GAP + extra_gap
        t += SCENE_OUT
        ranges.append((start, t))
    total = t + (END_CARD if end_card else 0.3)
    return segs, ranges, total


def make_chunks(words, max_chars=24):
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        txt = " ".join(x[2] for x in cur)
        last = w[2][-1:]
        if len(txt) >= max_chars or (last in ".!?…" and len(txt) >= 8) or (last in ",;:—" and len(txt) >= 15):
            chunks.append(cur)
            cur = []
    if cur:
        if chunks and len(" ".join(x[2] for x in cur)) < 8 and len(" ".join(x[2] for x in chunks[-1])) < 30:
            chunks[-1].extend(cur)
        else:
            chunks.append(cur)
    return chunks


# --------------------------------------------------------------- overlays
class Overlays:
    def __init__(self, W, H):
        self.W, self.H, self.u = W, H, H / 1920
        self.font = get_font(int(84 * self.u))
        self.nfont = get_font(int(70 * self.u), bold=False)
        self.tag_font = get_font(int(54 * self.u))
        self.title_font = get_font(int(70 * self.u))
        self.end_font = get_font(int(92 * self.u))
        self.cache = {}

    def _wrap(self, words, font, max_w):
        space = font.getlength(" ") + 10 * self.u
        lines, cur, w = [], [], 0
        for i, word in enumerate(words):
            ww = font.getlength(word)
            if cur and w + space + ww > max_w:
                lines.append(cur)
                cur, w = [], 0
            cur.append((i, word, ww))
            w += (space if len(cur) > 1 else 0) + ww
        if cur:
            lines.append(cur)
        return lines, space

    def caption(self, words, hl, narration):
        key = ("cap", tuple(words), hl, narration)
        if key in self.cache:
            return self.cache[key]
        if len(self.cache) > 400:
            self.cache.clear()
        u = self.u
        font = self.nfont if narration else self.font
        sw = int((6 if narration else 9) * u)
        lines, space = self._wrap(words, font, self.W * (0.80 if narration else 0.86))
        lh = int(font_size(font) * 1.25)
        pad = int(26 * u)
        h = lh * len(lines) + pad * 2
        img = Image.new("RGBA", (self.W, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        widths = [sum(x[2] for x in ln) + space * (len(ln) - 1) for ln in lines]
        if narration:
            mw = max(widths)
            d.rounded_rectangle(((self.W - mw) / 2 - pad * 1.5, 0, (self.W + mw) / 2 + pad * 1.5, h),
                                radius=pad * 1.2, fill=(10, 10, 25, 165))
        y = pad
        for ln, lw in zip(lines, widths):
            x = (self.W - lw) / 2
            for i, word, ww in ln:
                if i == hl:
                    col = (140, 215, 255) if narration else (255, 226, 50)
                else:
                    col = (255, 255, 255)
                d.text((x, y), word, font=font, fill=col, stroke_width=sw, stroke_fill=(0, 0, 0))
                x += ww + space
            y += lh
        self.cache[key] = img
        return img

    def tag(self, name, color):
        key = ("tag", name, color)
        if key in self.cache:
            return self.cache[key]
        u, f = self.u, self.tag_font
        tw = f.getlength(name)
        ph = int(font_size(f) * 1.55)
        pw = int(tw + ph)
        img = Image.new("RGBA", (pw + 10, ph + 10), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((5, 5, pw + 5, ph + 5), radius=ph / 2, fill=tuple(color) + (255,),
                            outline=(255, 255, 255), width=max(2, int(5 * u)))
        try:
            d.text((5 + ph / 2, 5 + ph / 2), name, font=f, fill=(255, 255, 255), anchor="lm")
        except (ValueError, TypeError):
            d.text((5 + ph / 2, 5 + ph * 0.2), name, font=f, fill=(255, 255, 255))
        self.cache[key] = img
        return img

    def title(self, text):
        key = ("title", text)
        if key in self.cache:
            return self.cache[key]
        u, f = self.u, self.title_font
        lines, space = self._wrap(text.split(), f, self.W * 0.78)
        lh = int(font_size(f) * 1.22)
        widths = [sum(x[2] for x in ln) + space * (len(ln) - 1) for ln in lines]
        pad = int(36 * u)
        bw, bh = int(max(widths) + pad * 2), int(lh * len(lines) + pad * 1.6)
        img = Image.new("RGBA", (bw + 20, bh + 20), (0, 0, 0, 0))
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((14, 16, bw + 10, bh + 12), radius=int(30 * u), fill=(0, 0, 0, 110))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(8 * u)))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((6, 6, bw + 6, bh + 6), radius=int(30 * u), fill=(255, 255, 255))
        y = 6 + pad * 0.8
        for ln, lw in zip(lines, widths):
            x = 6 + (bw - lw) / 2
            for _, word, ww in ln:
                d.text((x, y), word, font=f, fill=(20, 20, 30))
                x += ww + space
            y += lh
        self.cache[key] = img
        return img

    def end_text(self, text):
        key = ("end", text)
        if key in self.cache:
            return self.cache[key]
        f = self.end_font
        lines, space = self._wrap(text.split(), f, self.W * 0.84)
        lh = int(font_size(f) * 1.25)
        img = Image.new("RGBA", (self.W, lh * len(lines) + 40), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        y = 20
        for ln in lines:
            lw = sum(x[2] for x in ln) + space * (len(ln) - 1)
            x = (self.W - lw) / 2
            for _, word, ww in ln:
                d.text((x, y), word, font=f, fill=(255, 255, 255), stroke_width=int(10 * self.u),
                       stroke_fill=(0, 0, 0))
                x += ww + space
            y += lh
        self.cache[key] = img
        return img


def _scaled(img, s):
    if abs(s - 1) < 0.01:
        return img
    return img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), BILINEAR)


def _faded(img, a):
    if a >= 0.999:
        return img
    img = img.copy()
    img.putalpha(img.getchannel("A").point(lambda v: int(v * a)))
    return img


# ------------------------------------------------------------------ render
def render(script, cast, clips, out_path, width=1080, height=1920, fps=30, title="", end_text="",
           music_mood="Happy", music_file=None, extra_gap=0.0, preset="medium", crf=19,
           narrator_onscreen=False, progress=lambda f, msg="": None, log=print):
    u = height / 1920
    segs, ranges, total = plan_timeline(script, clips, extra_gap, end_card=bool(end_text))
    log(f"Video length will be {total / 60:.1f} min ({total:.0f} s).")

    # ---------------- audio
    voice = np.zeros(int(total * A.SR) + A.SR, np.float32)
    for s in segs:
        i0 = int(s.start * A.SR)
        voice[i0:i0 + len(s.clip.samples)] += s.clip.samples
    music = None
    try:
        if music_file:
            music = A.load_music_file(music_file, len(voice) / A.SR)
        elif music_mood and music_mood != "None":
            music = A.make_music(len(voice) / A.SR, music_mood)
    except Exception as e:
        log(f"⚠ Music skipped: {e}")
    mixed = A.mix(voice, music, music_volume=0.20 if music_file else 0.16)
    env = A.frame_envelope(voice, fps)
    tmp_dir = tempfile.mkdtemp(prefix="svs_")
    wav = os.path.join(tmp_dir, "audio.wav")
    A.write_wav(wav, mixed)

    # ---------------- captions + stage per segment
    for i, s in enumerate(segs):
        words = [(s.start + a, s.start + b, w) for a, b, w in s.clip.words]
        for ch in make_chunks(words):
            s.chunks.append((ch[0][0], ch))
        s.chunk_starts = [c[0] for c in s.chunks]
        nxt = segs[i + 1].start if i + 1 < len(segs) and segs[i + 1].scene == s.scene else s.end + 0.7
        s.caption_end = min(nxt, s.end + 1.6)

    main_cast = [n for n in script.characters()][:2]
    scene_init, scene_scale = [], []
    for si, sc in enumerate(script.scenes):
        ss = [s for s in segs if s.scene == si]
        s0 = ranges[si][0]
        if narrator_onscreen:
            for s in ss:
                s.visible = [(NARRATOR, s0)]
            scene_init.append([(NARRATOR, s0)])
            scene_scale.append(1.15)
        else:
            order = []
            for s in ss:
                if not s.line.is_narration and s.speaker not in order:
                    order.append(s.speaker)
            if not order:
                order = list(main_cast)
            on_stage = order[:3]
            last = {n: -1000 + i for i, n in enumerate(on_stage)}
            appear = {n: s0 for n in on_stage}
            scene_init.append([(n, s0) for n in on_stage])
            maxn = len(on_stage)
            for s in ss:
                if not s.line.is_narration:
                    if s.speaker not in on_stage:
                        if len(on_stage) >= 3:
                            on_stage.remove(min(on_stage, key=lambda n: last[n]))
                        on_stage.append(s.speaker)
                        appear[s.speaker] = max(s0, s.start - 0.4)
                    last[s.speaker] = s.start
                s.visible = [(n, appear[n]) for n in sorted(on_stage, key=order.index)]
                maxn = max(maxn, len(on_stage))
            scene_scale.append(SCALE[min(3, maxn)])
        emo = {}
        for s in ss:
            if not s.line.is_narration:
                for n in list(emo):
                    if n != s.speaker and emo[n] in TRANSIENT:
                        emo[n] = "neutral"
                emo[s.speaker] = s.emotion
            s.emo_state = dict(emo)

    # ---------------- backgrounds
    log("Painting backgrounds…")
    margin = int(width * 0.10)
    bg_cache, bgs = {}, []
    for sc in script.scenes:
        key = (sc.setting, sc.time)
        if key not in bg_cache:
            bg_cache[key] = render_background(sc.setting, sc.time, width + margin, height)
        bgs.append(bg_cache[key])

    ov = Overlays(width, height)
    shadow_cache = {}
    black = Image.new("RGB", (width, height), (0, 0, 0))
    feet_y = int(height * 0.81)
    cap_center = int(height * 0.255)

    def shadow(scale):
        key = round(scale, 3)
        if key not in shadow_cache:
            sw, sh = int(260 * scale * u), int(46 * scale * u)
            im = Image.new("RGBA", (sw + 40, sh + 40), (0, 0, 0, 0))
            ImageDraw.Draw(im).ellipse((20, 20, 20 + sw, 20 + sh), fill=(0, 0, 0, 85))
            shadow_cache[key] = im.filter(ImageFilter.GaussianBlur(8 * u))
        return shadow_cache[key]

    # ---------------- encode
    n_frames = int(math.ceil(total * fps))
    log_path = os.path.join(tmp_dir, "ffmpeg.log")
    cmd = [A.ffmpeg_exe(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
           "-r", str(fps), "-i", "-", "-i", wav, "-c:v", "libx264", "-preset", preset, "-tune", "animation",
           "-crf", str(crf), "-pix_fmt", "yuv420p", "-profile:v", "high", "-c:a", "aac", "-b:a", "192k",
           "-ar", "48000", "-movflags", "+faststart", "-shortest", out_path]
    errf = open(log_path, "wb")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=errf, **A.no_window())

    xcur = {}
    si, seg_i = 0, -1
    scenes_end = ranges[-1][1]
    try:
        for fi in range(n_frames):
            t = fi / fps
            in_end = t >= scenes_end
            while si + 1 < len(ranges) and t >= ranges[si + 1][0]:
                si += 1
            while seg_i + 1 < len(segs) and segs[seg_i + 1].start <= t:
                seg_i += 1
            seg = segs[seg_i] if seg_i >= 0 and segs[seg_i].scene == si else None
            visible = seg.visible if seg else scene_init[si]
            speaking = seg if (seg and seg.start <= t < seg.end and not in_end) else None
            speaker = None
            if speaking:
                speaker = NARRATOR if (narrator_onscreen and speaking.line.is_narration) else speaking.speaker
            level = float(env[fi]) if fi < len(env) else 0.0

            # background with a slow camera pan
            bg = bgs[si]
            s0, s1 = ranges[si]
            p = min(1.0, max(0.0, (t - s0) / max(0.1, s1 - s0)))
            p = p * p * (3 - 2 * p)
            ox = int((bg.width - width) * (p if si % 2 == 0 else 1 - p))
            frame = bg.crop((ox, 0, ox + width, height))

            # characters
            n = len(visible)
            xs = LAYOUT[min(3, n)]
            scale = scene_scale[si]
            names = [v[0] for v in visible]
            for name in list(xcur):
                if name not in names:
                    del xcur[name]
            for idx, (name, _) in enumerate(visible):
                target = xs[min(idx, len(xs) - 1)] * width
                xcur[name] = target if name not in xcur else xcur[name] + (target - xcur[name]) * 0.18
            speaker_x = xcur.get(speaker)
            chunk_idx = 0
            if speaking and speaking.chunk_starts:
                chunk_idx = max(0, bisect.bisect_right(speaking.chunk_starts, t) - 1)
            order = sorted(visible, key=lambda v: v[0] == speaker)
            for name, appear in order:
                ch = cast.get(name)
                if ch is None:
                    continue
                talking = name == speaker
                mouth = 0
                if talking:
                    mouth = 0 if level < 0.10 else 1 if level < 0.38 else 2 if level < 0.68 else 3
                emo = (seg.emo_state.get(name, "neutral") if seg else "neutral")
                if talking and not speaking.line.is_narration:
                    emo = speaking.emotion
                x0 = xcur[name]
                if talking:
                    others = [xcur[o] for o in names if o != name]
                    look = 0 if not others else (1 if min(others, key=lambda o: abs(o - x0)) > x0 else -1)
                    if speaking.line.is_narration or chunk_idx % 3 == 2:
                        look = 0
                elif speaker_x is not None:
                    look = 1 if speaker_x > x0 + 5 else (-1 if speaker_x < x0 - 5 else 0)
                else:
                    look = 0
                gesture = 1 if (talking and chunk_idx % 2 == 1 and level > 0.15) else 0
                spr = ch.sprite(scale * u, not ch.is_blinking(t), mouth, emo, look, gesture)
                bob = math.sin(2 * math.pi * (t * 0.45 + ch.blink_offset)) * 5 * u
                if talking:
                    bob -= level * 16 * u
                off = 0.0
                age = t - appear
                if age < 0.45:
                    e = 1 - (1 - max(0.0, age) / 0.45) ** 3
                    off = (1 - e) * width * 0.65 * (-1 if x0 < width / 2 else 1)
                sh = shadow(scale)
                frame.paste(sh, (int(x0 + off - sh.width / 2), int(feet_y - sh.height / 2)), sh)
                frame.paste(spr, (int(x0 + off - spr.width / 2), int(feet_y - spr.height + bob)), spr)

            # captions
            if seg and t < seg.caption_end and seg.chunks and not in_end:
                ci = max(0, bisect.bisect_right(seg.chunk_starts, t) - 1)
                cstart, cwords = seg.chunks[ci]
                hl = -1
                for wi, (ws, we, _) in enumerate(cwords):
                    if ws <= t:
                        hl = wi
                if t >= seg.end:
                    hl = -1
                narr = seg.line.is_narration
                cap = ov.caption([w[2] for w in cwords], hl, narr)
                age = t - cstart
                if age < 0.12:
                    cap = _scaled(cap, 0.86 + 0.14 * age / 0.12)
                top = int(cap_center - cap.height / 2)
                frame.paste(cap, (int((width - cap.width) / 2), top), cap)
                if not narr and seg.speaker in cast:
                    tg = ov.tag(seg.speaker, cast[seg.speaker].color)
                    frame.paste(tg, (int((width - tg.width) / 2), int(top - tg.height + 6 * u)), tg)

            # title hook
            if title and t < 3.8 and not in_end:
                tl = ov.title(title)
                a = min(1.0, (3.8 - t) / 0.4)
                tl = _faded(_scaled(tl, 0.8 + 0.2 * min(1.0, t / 0.25)), a)
                frame.paste(tl, (int((width - tl.width) / 2), int(height * 0.115 - tl.height / 2)), tl)

            # end card
            if in_end and end_text:
                frame = Image.blend(frame, black, min(0.55, (t - scenes_end) / 0.4 * 0.55))
                et = ov.end_text(end_text)
                age = t - scenes_end
                pop = 0.6 + 0.4 * min(1.0, age / 0.3) + 0.04 * math.sin(age * 6) * min(1.0, age / 0.3)
                et = _scaled(et, pop)
                frame.paste(et, (int((width - et.width) / 2), int(height * 0.42 - et.height / 2)), et)

            # scene transitions
            a = 1.0
            if not in_end:
                a = min(a, (t - s0) / FADE)
                last_scene = si == len(ranges) - 1
                if not (last_scene and end_text):
                    a = min(a, (s1 - t) / FADE)
            if a < 1.0:
                frame = Image.blend(black, frame, max(0.0, a))

            proc.stdin.write(frame.tobytes())
            if fi % 15 == 0:
                progress(fi / n_frames, f"Rendering frame {fi}/{n_frames}")
        proc.stdin.close()
        rc = proc.wait()
    except (BrokenPipeError, OSError):
        proc.kill()
        rc = -1
    finally:
        errf.close()
    if rc != 0:
        with open(log_path, "rb") as f:
            msg = f.read().decode(errors="ignore")[-800:]
        raise RuntimeError(f"Video encoder failed: {msg}")
    try:
        os.remove(wav)
        os.remove(log_path)
        os.rmdir(tmp_dir)
    except OSError:
        pass
    progress(1.0, "Done")
    return total
