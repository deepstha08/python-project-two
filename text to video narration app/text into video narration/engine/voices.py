"""Voices: free Microsoft neural voices through edge-tts (needs internet),
with an offline fallback (pyttsx3) and a silent fallback so a video always renders."""
import asyncio
import hashlib
import json
import os
import re
import zlib

import numpy as np

from . import audio

# label: (voice id, character type, pitch shift Hz)
VOICES = {
    "Auto": None,
    "Woman – warm (Jenny)": ("en-US-JennyNeural", "female", 0),
    "Woman – bright (Aria)": ("en-US-AriaNeural", "female", 0),
    "Woman – young (Ava)": ("en-US-AvaNeural", "female", 0),
    "Woman – calm (Emma)": ("en-US-EmmaNeural", "female", 0),
    "Woman – British (Sonia)": ("en-GB-SoniaNeural", "female", 0),
    "Man – friendly (Guy)": ("en-US-GuyNeural", "male", 0),
    "Man – casual (Andrew)": ("en-US-AndrewNeural", "male", 0),
    "Man – deep (Christopher)": ("en-US-ChristopherNeural", "male", 0),
    "Man – storyteller (Brian)": ("en-US-BrianNeural", "male", 0),
    "Man – British (Ryan)": ("en-GB-RyanNeural", "male", 0),
    "Old man (Roger)": ("en-US-RogerNeural", "old_male", -6),
    "Older woman (Michelle)": ("en-US-MichelleNeural", "old_female", -8),
    "Girl (Ana)": ("en-US-AnaNeural", "girl", 0),
    "Boy (Ana, lower)": ("en-US-AnaNeural", "boy", -14),
}
VOICE_LABELS = list(VOICES.keys())
NARRATOR_DEFAULT = "Man – storyteller (Brian)"

LOOKS = ["Auto", "Man", "Woman", "Boy", "Girl", "Old man", "Old woman"]
LOOK_TO_TYPE = {"Man": "male", "Woman": "female", "Boy": "boy", "Girl": "girl",
                "Old man": "old_male", "Old woman": "old_female"}
POOLS = {
    "male": ["Man – friendly (Guy)", "Man – casual (Andrew)", "Man – deep (Christopher)", "Man – British (Ryan)",
             "Man – storyteller (Brian)"],
    "female": ["Woman – warm (Jenny)", "Woman – bright (Aria)", "Woman – young (Ava)", "Woman – calm (Emma)",
               "Woman – British (Sonia)"],
    "boy": ["Boy (Ana, lower)"], "girl": ["Girl (Ana)"],
    "old_male": ["Old man (Roger)", "Man – deep (Christopher)"], "old_female": ["Older woman (Michelle)"],
}
FALLBACK_VOICE = {"female": "en-US-JennyNeural", "girl": "en-US-JennyNeural", "old_female": "en-US-JennyNeural"}

FEMALE_NAMES = set("""mia emma olivia ava sophia isabella amelia lily chloe zoe sara sarah anna emily grace ella
luna aria nora hannah maya leah julia lucy ruby alice clara eva rose jade amy kate katie jess jessica laura lisa
linda mary nina rachel rebecca sofia stella tina vanessa wendy zara priya aisha fatima ananya diya meera sita riya
neha pooja anjali kavya sakura yuki mei lena lea marie sophie nicole hailey bella daisy ivy molly poppy kim jenny
jane jasmine elena valentina camila lucia maria carmen ana gabriela natalia emily abigail harper evelyn madison
scarlett victoria penelope layla riley aurora savannah brooklyn hazel violet claire skylar naomi elena sita gita
lakshmi sunita anita kiran sushma asha nisha shreya isha tara megan karen susan betty helen diana fiona""".split())
MALE_A_NAMES = {"luca", "joshua", "elijah", "ezra", "noa", "nikita", "jonah", "isa", "musa", "krishna", "rama",
                "shiva", "aditya", "andrea", "sasha", "mustafa"}
TITLE_HINTS = [("grandma", "old_female"), ("granny", "old_female"), ("nana", "old_female"),
               ("grandmother", "old_female"), ("grandpa", "old_male"), ("grandfather", "old_male"),
               ("old man", "old_male"), ("old woman", "old_female"), ("wizard", "old_male"),
               ("mom", "female"), ("mum", "female"), ("mother", "female"), ("mrs", "female"), ("ms", "female"),
               ("miss", "female"), ("queen", "female"), ("princess", "female"), ("aunt", "female"),
               ("sister", "female"), ("lady", "female"), ("woman", "female"), ("girl", "girl"),
               ("daughter", "girl"), ("dad", "male"), ("father", "male"), ("mr", "male"), ("king", "male"),
               ("prince", "male"), ("uncle", "male"), ("brother", "male"), ("sir", "male"), ("man", "male"),
               ("boy", "boy"), ("son", "boy"), ("kid", "boy"), ("baby", "girl"), ("teacher", "female"),
               ("boss", "male"), ("doctor", "male"), ("friend", "male")]

EMO_PROSODY = {  # (rate %, pitch Hz)
    "neutral": (0, 0), "happy": (5, 4), "excited": (10, 8), "sad": (-12, -6),
    "angry": (6, -3), "surprised": (8, 10), "scared": (10, 6),
}


def guess_type(name):
    low = name.lower()
    for hint, kind in TITLE_HINTS:
        if re.search(r"(?<![a-z])" + hint + r"(?![a-z])", low):
            return kind
    first = re.sub(r"[^a-z]", "", low.split()[0]) if low.split() else ""
    if first in FEMALE_NAMES:
        return "female"
    if first.endswith("a") and first not in MALE_A_NAMES and len(first) > 2:
        return "female"
    return "male"


def assign_voices(characters, overrides=None, narrator_label=NARRATOR_DEFAULT):
    """Return {name: (voice_label, character_type)}; every character gets a distinct voice where possible."""
    overrides = overrides or {}
    used = {narrator_label}
    result = {}
    for name in characters:
        ov = overrides.get(name.lower(), {})
        v_label, look = ov.get("voice", "Auto"), ov.get("look", "Auto")
        kind = LOOK_TO_TYPE.get(look)
        if v_label and v_label != "Auto" and v_label in VOICES:
            kind = kind or VOICES[v_label][1]
        else:
            kind = kind or guess_type(name)
            pool = POOLS[kind]
            start = zlib.crc32(name.lower().encode()) % len(pool)
            ordered = pool[start:] + pool[:start]
            v_label = next((v for v in ordered if v not in used), ordered[0])
        used.add(v_label)
        result[name] = (v_label, kind)
    return result


# ------------------------------------------------------------- synthesis
class Clip:
    def __init__(self, samples, words):
        self.samples = samples
        self.words = words  # list of (start_s, end_s, word)

    @property
    def duration(self):
        return len(self.samples) / audio.SR


def _norm(w):
    return re.sub(r"[^\w]", "", w.lower())


def align_words(text, spoken, total):
    """Give each word of the original text (with punctuation) a start/end time."""
    tokens = text.split()
    if not tokens:
        return []
    times = [None] * len(tokens)
    j = 0
    for i, tok in enumerate(tokens):
        n = _norm(tok)
        if not n:
            continue
        for k in range(j, min(j + 4, len(spoken))):
            sw = _norm(spoken[k][2])
            if sw and (sw == n or n.startswith(sw) or sw.startswith(n)):
                start, end = spoken[k][0], spoken[k][1]
                joined, kk = sw, k
                while len(joined) < len(n) and kk + 1 < len(spoken) and n.startswith(joined + _norm(spoken[kk + 1][2])):
                    kk += 1
                    joined += _norm(spoken[kk][2])
                    end = spoken[kk][1]
                times[i] = (start, end)
                j = kk + 1
                break
    if spoken:
        first, last = spoken[0][0], spoken[-1][1]
    else:
        first, last = 0.05, max(0.1, total - 0.05)
    # fill gaps proportionally between known neighbours
    i = 0
    while i < len(tokens):
        if times[i] is not None:
            i += 1
            continue
        a = i
        while i < len(tokens) and times[i] is None:
            i += 1
        lo = times[a - 1][1] if a > 0 else first
        hi = times[i][0] if i < len(tokens) else last
        hi = max(hi, lo + 0.05 * (i - a))
        weights = [len(t) + 2 for t in tokens[a:i]]
        tot, t = float(sum(weights)), lo
        for idx, w in zip(range(a, i), weights):
            d = (hi - lo) * w / tot
            times[idx] = (t, t + d)
            t += d
    return [(s, e, tok) for (s, e), tok in zip(times, tokens)]


async def _edge_one(text, voice, rate, pitch, path):
    import edge_tts
    kw = dict(rate=f"{rate:+d}%", pitch=f"{pitch:+d}Hz")
    try:
        comm = edge_tts.Communicate(text, voice, boundary="WordBoundary", **kw)
    except TypeError:
        comm = edge_tts.Communicate(text, voice, **kw)
    words = []
    with open(path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                s = chunk["offset"] / 1e7
                words.append((s, s + chunk["duration"] / 1e7, chunk["text"]))
    if os.path.getsize(path) < 500:
        raise RuntimeError("empty audio")
    return words


async def _edge_all(jobs, progress):
    sem = asyncio.Semaphore(5)
    done = [0]
    results = {}

    async def run(job):
        idx, text, voice, rate, pitch, path = job
        async with sem:
            last = None
            for attempt in range(3):
                for v in ([voice] if attempt < 2 else [voice, FALLBACK_VOICE.get("female")]):
                    try:
                        results[idx] = await _edge_one(text, v, rate, pitch, path)
                        break
                    except Exception as e:  # network hiccup, retry
                        last = e
                if idx in results:
                    break
                await asyncio.sleep(1.5 * (attempt + 1))
            if idx not in results:
                results[idx] = last
            done[0] += 1
            progress(done[0] / max(1, len(jobs)))

    await asyncio.gather(*(run(j) for j in jobs))
    return results


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _offline_tts(text, kind, rate, path):
    import pyttsx3
    eng = pyttsx3.init()
    want_female = kind in ("female", "girl", "old_female")
    for v in eng.getProperty("voices"):
        nm = (v.name or "").lower()
        if want_female == any(k in nm for k in ("female", "zira", "hazel", "susan", "samantha", "karen", "victoria")):
            eng.setProperty("voice", v.id)
            break
    eng.setProperty("rate", int(175 * (1 + rate / 100)))
    eng.save_to_file(text, path)
    eng.runAndWait()
    if not os.path.exists(path) or os.path.getsize(path) < 500:
        raise RuntimeError("offline voice failed")


def synthesize(jobs, cache_dir, global_rate=0, log=print, progress=lambda f: None):
    """jobs: list of (text, voice_label, emotion). Returns list of Clip."""
    os.makedirs(cache_dir, exist_ok=True)
    plan, pending, results = [], [], {}
    for i, (text, label, emo) in enumerate(jobs):
        vid, kind, pshift = VOICES.get(label) or VOICES[NARRATOR_DEFAULT]
        r_e, p_e = EMO_PROSODY.get(emo, (0, 0))
        rate = int(max(-50, min(100, global_rate + r_e)))
        pitch = int(pshift + p_e)
        key = hashlib.sha1(f"{text}|{vid}|{rate}|{pitch}".encode()).hexdigest()[:20]
        mp3 = os.path.join(cache_dir, key + ".mp3")
        meta = os.path.join(cache_dir, key + ".json")
        plan.append((text, kind, rate, mp3, meta))
        if os.path.exists(mp3) and os.path.exists(meta):
            with open(meta) as f:
                results[i] = json.load(f)
        else:
            pending.append((i, text, vid, rate, pitch, mp3))

    if pending:
        log(f"Recording {len(pending)} voice lines…")
        try:
            out = _run_async(_edge_all(pending, progress))
        except ImportError:
            out = {p[0]: RuntimeError("edge-tts not installed") for p in pending}
        failures = 0
        for p in pending:
            idx = p[0]
            words = out.get(idx)
            if isinstance(words, list):
                results[idx] = words
                with open(plan[idx][4], "w") as f:
                    json.dump(words, f)
            else:
                failures += 1
                results[idx] = None
        if failures:
            log(f"⚠ {failures} line(s) couldn't use online voices (no internet?). Trying offline voice…")

    clips = []
    offline_ok = True
    for i, (text, kind, rate, mp3, meta) in enumerate(plan):
        words = results.get(i)
        samples = None
        if words is not None:
            try:
                samples = audio.decode(mp3)
            except Exception:
                samples = None
        if samples is None and offline_ok:
            wav = mp3[:-4] + "_offline.wav"
            try:
                _offline_tts(text, kind, rate, wav)
                samples, words = audio.decode(wav), []
            except Exception:
                offline_ok = False
        if samples is None:   # silent placeholder keeps timing so the video still renders
            dur = 0.4 + len(text.split()) / 2.6
            samples, words = np.zeros(int(dur * audio.SR), np.float32), []
            log_once = getattr(synthesize, "_warned", False)
            if not log_once:
                log("⚠ No voice engine available – lines will be silent. Connect to the internet for voices.")
                synthesize._warned = True
        samples, cut = audio.trim_silence(samples)
        samples = audio.normalize_clip(samples)
        dur = len(samples) / audio.SR
        spoken = [(max(0.0, s - cut), max(0.0, e - cut), w) for s, e, w in (words or [])]
        clips.append(Clip(samples, align_words(text, spoken, dur)))
    synthesize._warned = False
    return clips
