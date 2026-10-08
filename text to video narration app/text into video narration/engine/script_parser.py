"""Turns a plain story OR a written script into scenes and lines of dialogue.

Script format (recommended, gives you full control):

    TITLE: The Dragon Egg
    [SCENE: park, day]
    Narrator: One sunny morning, Mia found something strange.
    Mia (surprised): Leo, look! Is this... an egg?
    Leo (happy): That's the biggest egg I've ever seen!

Plain stories also work: quotes ("...") become dialogue, everything else
becomes narration, and places like "park" or "kitchen" pick the background.
"""
import re
from dataclasses import dataclass, field

NARRATOR = "Narrator"

SETTINGS = ["room", "kitchen", "park", "city", "school", "office", "beach", "forest", "space", "cafe"]
TIMES = ["day", "sunset", "night"]
EMOTIONS = ["neutral", "happy", "excited", "sad", "angry", "surprised", "scared"]

SETTING_KEYWORDS = {
    "kitchen": ["kitchen", "cooking", "fridge", "breakfast", "dinner table", "oven"],
    "park": ["park", "garden", "playground", "meadow", "field", "picnic", "bench"],
    "school": ["school", "classroom", "class", "teacher", "homework", "lesson", "exam"],
    "office": ["office", "meeting", "boss", "desk", "company", "coworker", "interview"],
    "beach": ["beach", "sea", "ocean", "sand", "waves", "shore", "island"],
    "forest": ["forest", "woods", "jungle", "camp", "hike", "cave", "mountain"],
    "city": ["city", "street", "road", "downtown", "traffic", "sidewalk", "town", "mall", "shop"],
    "space": ["space", "planet", "rocket", "spaceship", "astronaut", "galaxy", "alien", "mars"],
    "cafe": ["cafe", "café", "coffee", "restaurant", "diner", "bakery", "pizza place"],
    "room": ["room", "house", "home", "bedroom", "living room", "apartment", "sofa", "couch", "bed"],
}
TIME_KEYWORDS = {
    "night": ["night", "midnight", "evening", "dark", "moonlight", "stars"],
    "sunset": ["sunset", "dusk", "sunrise", "dawn", "golden hour"],
    "day": ["morning", "afternoon", "noon", "sunny", "daytime"],
}

# Order matters: the first matching emotion wins.
EMOTION_ALIASES = [
    ("angry", ["angry", "angrily", "mad", "furious", "annoyed", "frustrated", "grumpy", "irritated", "rage"]),
    ("scared", ["scared", "afraid", "nervous", "terrified", "worried", "anxious", "frightened", "panic", "fear", "whisper"]),
    ("sad", ["sad", "crying", "cries", "upset", "tearful", "sobbing", "heartbroken", "disappointed", "sigh", "hurt"]),
    ("surprised", ["surprised", "shocked", "amazed", "confused", "gasp", "astonished", "stunned", "curious", "wow"]),
    ("excited", ["excited", "thrilled", "eager", "shout", "yell", "energetic", "hyped", "cheer"]),
    ("happy", ["happy", "smil", "laugh", "cheerful", "joy", "glad", "grin", "proud", "relieved", "love", "giggl", "warm"]),
    ("neutral", ["neutral", "calm", "serious", "thinking", "quiet", "flat", "plain"]),
]
EMOTION_WORDS = {
    "angry": ["angry", "furious", "hate", "stop it", "how dare", "shut up", "annoying", "unacceptable"],
    "scared": ["scared", "afraid", "help!", "run!", "terrified", "monster", "ghost", "danger", "what was that"],
    "sad": ["sad", "sorry", "cry", "miss you", "lonely", "tears", "alone", "hurts", "goodbye"],
    "surprised": ["wow", "what?", "really?", "no way", "oh my", "whoa", "unbelievable", "seriously?", "what?!"],
    "happy": ["haha", "yay", "love", "great", "awesome", "thank", "happy", "glad", "amazing", "best"],
}

NAME_WORDS_MAX = 3


@dataclass
class Line:
    speaker: str
    text: str
    emotion: str = "neutral"

    @property
    def is_narration(self) -> bool:
        return self.speaker == NARRATOR


@dataclass
class Scene:
    setting: str = None
    time: str = None
    lines: list = field(default_factory=list)


@dataclass
class Script:
    title: str = ""
    scenes: list = field(default_factory=list)

    def characters(self):
        seen = []
        for sc in self.scenes:
            for ln in sc.lines:
                if not ln.is_narration and ln.speaker not in seen:
                    seen.append(ln.speaker)
        return seen

    def all_lines(self):
        return [ln for sc in self.scenes for ln in sc.lines]

    def word_count(self) -> int:
        return sum(len(ln.text.split()) for ln in self.all_lines())

    def to_text(self) -> str:
        out = []
        if self.title:
            out.append(f"TITLE: {self.title}")
        for sc in self.scenes:
            out.append(f"\n[SCENE: {sc.setting}, {sc.time}]")
            for ln in sc.lines:
                if ln.is_narration:
                    out.append(f"{NARRATOR}: {ln.text}")
                elif ln.emotion and ln.emotion != "neutral":
                    out.append(f"{ln.speaker} ({ln.emotion}): {ln.text}")
                else:
                    out.append(f"{ln.speaker}: {ln.text}")
        return "\n".join(out).strip() + "\n"


# ----------------------------------------------------------------- helpers
def _word_re(word):
    return re.compile(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])")


def detect_setting(text, default=None):
    """First place mentioned wins, but a specific place (kitchen) beats a generic one (home)."""
    t = text.lower()
    found = []
    for setting, words in SETTING_KEYWORDS.items():
        for w in words:
            m = _word_re(w).search(t)
            if m:
                found.append((setting == "room", m.start(), setting))
    return min(found)[2] if found else default


def detect_time(text, default=None):
    t = text.lower()
    for tm in ("night", "sunset", "day"):
        if any(_word_re(w).search(t) for w in TIME_KEYWORDS[tm]):
            return tm
    return default


def guess_emotion(text):
    t = text.lower()
    for emo in ("angry", "scared", "sad", "surprised", "happy"):
        if any(w in t for w in EMOTION_WORDS[emo]):
            return emo
    if "!" in t:
        return "excited"
    return "neutral"


def normalize_emotion(raw, text=""):
    if raw:
        r = raw.lower()
        for emo, words in EMOTION_ALIASES:
            if emo in r or any(w in r for w in words):
                return emo
    return guess_emotion(text)


def clean_text(text):
    text = re.sub(r"\*[^*]{1,80}\*", " ", text.replace("**", ""))   # *waves*
    text = re.sub(r"\([^)]*\)", " ", text)                          # (laughs)
    text = re.sub(r"\[[^\]]*\]", " ", text)                         # [walks in]
    text = text.strip().strip('"“”').strip()
    return re.sub(r"\s+", " ", text).strip()


def _normalize_name(name):
    name = re.sub(r"[*_#]", "", name).strip(" .-'")
    if name.isupper() or name.islower():
        name = " ".join(w.capitalize() for w in name.split())
    return name


SCENE_RE = re.compile(
    r"^\s*(?:[\[(#]+\s*)?(?:scene|setting|location|place)\b\s*\d*\s*(?:[:\-–—.]\s*(.*?))?\s*[\])]*\s*$", re.I)
TITLE_RE = re.compile(r"^\s*[#*]*\s*title\s*[:\-]\s*(.+?)\s*[*]*$", re.I)
SPEAKER_RE = re.compile(
    r"^\s*[*_]*\s*([A-Za-zÀ-ɏ][\wÀ-ɏ .'\-]{0,30}?)\s*[*_]*\s*"
    r"(?:\(([^)]{1,40})\))?\s*[*_]*\s*[:：]\s*[*_]*\s*(.+)$")
NOT_NAMES = {"http", "https", "note", "example", "warning", "tip", "summary", "moral", "end", "the end"}


def _speaker_match(line):
    if line.lstrip()[:1] in "\"“'":
        return None
    m = SPEAKER_RE.match(line)
    if not m:
        return None
    name = m.group(1).strip()
    if len(name.split()) > NAME_WORDS_MAX or name.lower() in NOT_NAMES:
        return None
    return name, m.group(2), m.group(3)


def looks_like_script(text):
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return False
    speakers = sum(1 for l in lines if _speaker_match(l))
    scenes = sum(1 for l in lines if SCENE_RE.match(l))
    return scenes > 0 or (speakers >= 2 and speakers >= 0.3 * len(lines))


def _split_sentences(text):
    parts = re.split(r"(?<=[.!?…])\s+(?=[A-Z\"“'])", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _add_narration(scene, text, max_words=32):
    chunk = []
    for sent in _split_sentences(text):
        if chunk and len(" ".join(chunk + [sent]).split()) > max_words:
            scene.lines.append(Line(NARRATOR, " ".join(chunk)))
            chunk = []
        chunk.append(sent)
    if chunk:
        joined = clean_text(" ".join(chunk))
        if joined:
            scene.lines.append(Line(NARRATOR, joined))


def _finalize(script):
    script.scenes = [s for s in script.scenes if s.lines]
    prev_setting, prev_time = "room", "day"
    for sc in script.scenes:
        blob = " ".join(l.text for l in sc.lines if l.is_narration) or " ".join(l.text for l in sc.lines)
        if not sc.setting:
            sc.setting = detect_setting(blob, prev_setting)
        if not sc.time:
            sc.time = detect_time(blob, prev_time)
        prev_setting, prev_time = sc.setting, sc.time
    return script


# ------------------------------------------------------------ script mode
def parse_script(text):
    script = Script()
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or set(line) <= set("-=*_#`~ "):
            continue
        m = TITLE_RE.match(line)
        if m:
            if not script.title:
                script.title = m.group(1).strip().strip('"“”*')
            continue
        m = SCENE_RE.match(line)
        if m:
            desc = m.group(1) or ""
            cur = Scene(setting=detect_setting(desc), time=detect_time(desc))
            script.scenes.append(cur)
            continue
        if cur is None:
            cur = Scene()
            script.scenes.append(cur)
        sm = _speaker_match(line)
        if sm:
            name, paren, body = sm
            inline = re.search(r"\(([^)]{1,40})\)|\*([^*]{1,40})\*", body)
            if not paren and inline:
                paren = inline.group(1) or inline.group(2)
            if name.lower() in ("narrator", "narration", "voiceover", "voice over", "vo", "storyteller"):
                speaker = NARRATOR
            else:
                speaker = _normalize_name(name)
            body_clean = clean_text(body)
            if body_clean:
                emo = "neutral" if speaker == NARRATOR else normalize_emotion(paren, body_clean)
                cur.lines.append(Line(speaker, body_clean, emo))
        else:
            if line.startswith("[") and line.endswith("]"):
                line = line[1:-1]
            body = clean_text(line)
            if body:
                _add_narration(cur, body)
    return _finalize(script)


# ------------------------------------------------------------- prose mode
SAY = ("said|says|asked|asks|shouted|shouts|replied|replies|whispered|whispers|yelled|yells|cried|cries|"
       "answered|answers|exclaimed|called|laughed|laughs|screamed|screams|added|adds|muttered|mutters|begged|"
       "explained|explains|continued|insisted|giggled|sighed|gasped|told|tells|smiled|grinned|warned|joked|"
       "agreed|repeated|admitted|announced|demanded|wondered|growled|groaned|squealed|hissed|snapped")
STOP_NAMES = {"He", "She", "They", "It", "I", "We", "You", "The", "Then", "And", "But", "So", "When", "After",
              "Suddenly", "Finally", "Later", "His", "Her", "Their", "Our", "My", "This", "That", "A", "An",
              "Everyone", "Someone", "Nobody", "Somebody"}
QUOTE_RE = re.compile(r'[“"«]([^”"»]+)[”"»]')
ATTRIB_RE = re.compile(r"\b(?:" + SAY + r")\b", re.I)


def _valid_name(n):
    return n and n.split()[0] not in STOP_NAMES


def _find_name(fragment, after):
    if after:
        m = re.match(r"^[\s,.!?;:—–-]*(?:" + SAY + r")\s+([A-Z][\w'-]+)", fragment)
        if m and _valid_name(m.group(1)):
            return m.group(1)
        m = re.match(r"^[\s,.!?;:—–-]*([A-Z][\w'-]+(?:\s[A-Z][\w'-]+)?)\s+(?:\w+ly\s+)?(?:" + SAY + r")\b", fragment)
        if m and _valid_name(m.group(1)):
            return m.group(1)
        if re.match(r"^[\s,.!?;:—–-]*(he|she|they)\s+(?:" + SAY + r")\b", fragment, re.I):
            return "@"
    else:
        m = re.search(r"([A-Z][\w'-]+(?:\s[A-Z][\w'-]+)?)\s+(?:\w+ly\s+)?(?:" + SAY + r")\b[^.!?\"“”]*$", fragment)
        if m and _valid_name(m.group(1)):
            return m.group(1)
    return None


def _mentioned_name(text, known, exclude=None):
    """Most recent name mentioned in narration (for 'she said' after 'his sister Emma ran up')."""
    best, best_pos = None, -1
    for n in known:
        if n == exclude:
            continue
        for mm in re.finditer(r"\b" + re.escape(n) + r"\b", text):
            if mm.start() > best_pos:
                best, best_pos = n, mm.start()
    for mm in re.finditer(r"(?<=[a-z,] )([A-Z][a-z]{2,})\b", text):
        n = mm.group(1)
        if n not in STOP_NAMES and n != exclude and mm.start() > best_pos:
            best, best_pos = n, mm.start()
    return best


def parse_prose(text):
    script = Script()
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    if len(paras) > 1 and len(paras[0].split()) <= 9 and not re.search(r"[.!?\"”:]$", paras[0]):
        script.title = paras[0].strip("#* ").strip()
        paras = paras[1:]

    cur = None
    speakers = []          # most recent speaker first
    for para in paras:
        setting, tm = detect_setting(para), detect_time(para)
        if cur is None or (setting and setting != cur.setting and cur.lines) or (tm and cur.time and tm != cur.time and cur.lines):
            cur = Scene(setting=setting or (cur.setting if cur else None), time=tm or (cur.time if cur else None))
            script.scenes.append(cur)
        elif setting and not cur.setting:
            cur.setting = setting

        matches = list(QUOTE_RE.finditer(para))
        if not matches:
            _add_narration(cur, para)
            continue

        pos = 0
        last_in_para = None
        for i, m in enumerate(matches):
            before = para[pos:m.start()]
            nxt = matches[i + 1].start() if i + 1 < len(matches) else len(para)
            after = para[m.end():nxt]
            # narration before the quote (skip pure attributions like "Mia said,")
            b = before.strip(" ,;:—–-")
            if b and not (ATTRIB_RE.search(b) and len(b.split()) <= 10) and len(b.split()) >= 3:
                _add_narration(cur, b)
            name = _find_name(after, True) or _find_name(before, False)
            if name == "@" or name is None:
                mentioned = _mentioned_name(para[:m.start()], speakers, exclude=last_in_para or
                                            (speakers[0] if speakers else None))
                if mentioned:
                    name = mentioned
                elif last_in_para:
                    name = last_in_para
                elif len(speakers) >= 2:
                    name = speakers[1]
                elif len(speakers) == 1:
                    name = "Friend"
                else:
                    name = "Friend"
            name = _normalize_name(name)
            quote = re.sub(r"[,;:—–-]+$", ".", clean_text(m.group(1)))
            if quote:
                cur.lines.append(Line(name, quote, guess_emotion(quote + " " + after[:60])))
                if name in speakers:
                    speakers.remove(name)
                speakers.insert(0, name)
                last_in_para = name
            # consume the attribution part of "after"
            a = after.strip(" ,;:—–-")
            if ATTRIB_RE.search(a) and len(a.split()) <= 10:
                pos = nxt
            else:
                pos = m.end()
        tail = para[pos:].strip(" ,;:—–-")
        if tail and len(tail.split()) >= 3 and not QUOTE_RE.search(tail):
            _add_narration(cur, tail)
    return _finalize(script)


def parse(text):
    """Parse a story or script into a Script object."""
    text = (text or "").replace("\r\n", "\n").strip()
    text = re.sub(r"^```\w*|```$", "", text, flags=re.M).strip()
    if not text:
        raise ValueError("Please type a story or a script first.")
    script = parse_script(text) if looks_like_script(text) else parse_prose(text)
    if not script.all_lines():
        raise ValueError("I couldn't find any sentences in your text.")
    return script
