"""Optional AI screenwriter: turns a short idea into a full script of the chosen length.

Supports Claude (Anthropic API key), OpenAI (API key), Ollama (free, runs locally)
and any other OpenAI-compatible server.
"""
import re

import requests

from . import script_parser as sp

PROVIDER_NONE = "None – use my text as written"
PROVIDER_CLAUDE = "Claude (Anthropic API key)"
PROVIDER_OPENAI = "OpenAI (API key)"
PROVIDER_OLLAMA = "Ollama (free, runs on your PC)"
PROVIDER_OTHER = "Other OpenAI-compatible server"
PROVIDERS = [PROVIDER_NONE, PROVIDER_CLAUDE, PROVIDER_OPENAI, PROVIDER_OLLAMA, PROVIDER_OTHER]

DEFAULTS = {
    PROVIDER_NONE: ("", ""),
    PROVIDER_CLAUDE: ("claude-sonnet-5-5", "https://api.anthropic.com"),
    PROVIDER_OPENAI: ("gpt-4o-mini", "https://api.openai.com/v1"),
    PROVIDER_OLLAMA: ("llama3.1", "http://localhost:11434/v1"),
    PROVIDER_OTHER: ("", "http://localhost:8000/v1"),
}

WORDS_PER_MINUTE = 150

PROMPT = """You are a screenwriter for viral animated story videos (TikTok / Reels / Shorts).
Turn the idea below into a complete animated screenplay that takes about {minutes} minute(s) to perform out loud.
That means about {words} spoken words in total. Length matters: write the FULL {words} words, not less.

STORY IDEA:
\"\"\"
{story}
\"\"\"

{cast_note}
RULES:
- Mostly dialogue between characters. Use the Narrator only for short scene-setting lines (at most 1 in 5 lines).
- The very first line must be a strong hook that makes viewers keep watching.
- Short, natural, punchy spoken lines (5-25 words each). No emojis, no hashtags, no stage directions outside the format.
- Use 2 to 4 main characters with simple first names, consistent throughout.
- Use about {scenes} scene(s). Every scene starts with a [SCENE: ...] line.
- Build tension, then end with a satisfying twist, punchline or emotional payoff.

FORMAT (follow exactly, reply with nothing else):
TITLE: <catchy title, max 6 words>
[SCENE: <one of: {settings}>, <day|sunset|night>]
Narrator: <narration>
<Name> (<emotion>): <dialogue>

Allowed emotions: {emotions}.
"""

CONTINUE = """Here is an animated screenplay written so far:

{script}

It is too short. Continue the SAME story from exactly where it stops, for about {more} more spoken words.
Do not repeat or restart anything. Keep the same characters and the same format
([SCENE: setting, time] lines, "Name (emotion): line", "Narrator: line"), and finish with a proper ending.
Reply with only the new lines."""


def _clean(text):
    text = re.sub(r"^```\w*\s*|```\s*$", "", text.strip(), flags=re.M)
    return text.strip()


def _check(r, who):
    if r.status_code != 200:
        try:
            detail = r.json()
            detail = detail.get("error", detail)
            if isinstance(detail, dict):
                detail = detail.get("message", detail)
        except Exception:
            detail = r.text[:300]
        raise RuntimeError(f"{who} returned an error ({r.status_code}): {detail}")


def _call(provider, api_key, model, base_url, prompt, max_tokens=8000):
    if provider == PROVIDER_CLAUDE:
        if not api_key:
            raise RuntimeError("Please paste your Anthropic API key (console.anthropic.com).")
        url = (base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
        r = requests.post(url, timeout=600, headers={
            "x-api-key": api_key.strip(), "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]})
        _check(r, "Claude")
        data = r.json()
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

    if provider == PROVIDER_OPENAI and not api_key:
        raise RuntimeError("Please paste your OpenAI API key.")
    url = (base_url or DEFAULTS[provider][1]).rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    body = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if provider != PROVIDER_OPENAI:
        body["max_tokens"] = max_tokens
    try:
        r = requests.post(url, headers=headers, json=body, timeout=900)
    except requests.ConnectionError:
        if provider == PROVIDER_OLLAMA:
            raise RuntimeError("Can't reach Ollama. Install it from ollama.com, run `ollama pull llama3.1`, "
                               "and keep the Ollama app open.")
        raise
    _check(r, provider.split(" (")[0])
    return r.json()["choices"][0]["message"]["content"]


def spoken_words(text):
    try:
        return sp.parse(text).word_count()
    except Exception:
        return len(text.split())


def write_script(story, minutes, provider, api_key="", model="", base_url="", cast=None, log=print):
    """Return a screenplay (in the app's script format) about `minutes` long."""
    model = model or DEFAULTS[provider][0]
    base_url = base_url or DEFAULTS[provider][1]
    target = int(minutes * WORDS_PER_MINUTE)
    cast_note = ""
    if cast:
        people = ", ".join(f"{n} ({look.lower()})" if look and look != "Auto" else n for n, look in cast)
        cast_note = f"CHARACTERS (use exactly these names): {people}\n"
    prompt = PROMPT.format(
        minutes=minutes, words=target, story=story.strip(), cast_note=cast_note,
        scenes=max(1, min(12, round(minutes * 1.2))), settings=", ".join(sp.SETTINGS),
        emotions=", ".join(sp.EMOTIONS))
    max_tokens = min(16000, max(2500, int(target * 2.2)))
    log(f"AI is writing a ~{minutes} min script (~{target} words)…")
    text = _clean(_call(provider, api_key, model, base_url, prompt, max_tokens))

    for _ in range(5):
        have = spoken_words(text)
        if have >= 0.88 * target:
            break
        more = target - have
        log(f"Script has {have} of ~{target} words, asking AI to continue…")
        extra = _clean(_call(provider, api_key, model, base_url,
                             CONTINUE.format(script=text, more=more), min(16000, max(2000, int(more * 2.2)))))
        if not extra:
            break
        extra = "\n".join(l for l in extra.splitlines() if not l.strip().upper().startswith("TITLE:"))
        text = text.rstrip() + "\n" + extra
    log(f"Script ready: {spoken_words(text)} spoken words.")
    return text
