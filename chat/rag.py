import atexit
import json
import logging
import re
from html import unescape
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, unquote, urlparse

import requests

from .query import Querier

logger = logging.getLogger("ibis.chat.rag")
CACHE_PATH = Path("chat/song_lyrics_cache.json")
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]
UA_ROTATION_LOCK = Lock()
UA_ROTATION_INDEX = 0
RESULT_LINK_TAG_PATTERN = re.compile(
    r"<a[^>]*class=['\"]result-link['\"][^>]*>",
    flags=re.IGNORECASE,
)
HREF_PATTERN = re.compile(r'href="([^"]+)"', flags=re.IGNORECASE)
LYRICS_CONTAINER_PATTERN = re.compile(
    r'<div[^>]*data-lyrics-container="true"[^>]*>',
    flags=re.IGNORECASE,
)
DIV_TOKEN_PATTERN = re.compile(r"</?div\b[^>]*>", flags=re.IGNORECASE)
BR_PATTERN = re.compile(r"<br\s*/?>", flags=re.IGNORECASE)
TAG_PATTERN = re.compile(r"<[^>]+>")
CACHE_LOCK = Lock()
LOOKUP_CACHE = {}


def _next_user_agent():
    global UA_ROTATION_INDEX
    with UA_ROTATION_LOCK:
        agent = USER_AGENTS[UA_ROTATION_INDEX % len(USER_AGENTS)]
        UA_ROTATION_INDEX += 1
    return agent


SONG_TITLE_NER_QUERIER = Querier(
    instructions=(
        "Extract all song titles mentioned anywhere in the provided full context when highly confident. "
        "Treat romanized/transliterated titles, including lowercase multi-word phrases, as valid song titles when likely."
    ),
    tool={
        "type": "function",
        "function": {
            "name": "extract_song_title_entities",
            "description": "Extract possible song title mentions from text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "possible_song_titles": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["possible_song_titles"],
            },
        },
    },
    temperature=0.0,
    token_budgets=[260, 420],
)

SONG_TITLE_VERIFIER_QUERIER = Querier(
    instructions=(
        "Decide if candidate_text should be treated as a song title in this user message context. "
        "Be conservative: reject casual slang, memes, or ordinary phrases unless context clearly "
        "indicates a song title reference."
    ),
    tool={
        "type": "function",
        "function": {
            "name": "verify_song_title_candidate",
            "description": "Return whether the candidate is a song title in this context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "is_song_title": {"type": "boolean"},
                },
                "required": ["is_song_title"],
            },
        },
    },
    temperature=0.0,
    token_budgets=[120, 220],
)

TRANSLATE_LYRICS_QUERIER = Querier(
    instructions=(
        "Translate song lyrics to English. Preserve line breaks and section labels when possible. "
        "Return only the translated lyrics text."
    ),
    temperature=0.0,
    token_budgets=[600, 1200],
)

def _load_cache():
    global LOOKUP_CACHE
    if not CACHE_PATH.exists():
        LOOKUP_CACHE = {}
        return
    LOOKUP_CACHE = json.loads(CACHE_PATH.read_text(encoding="utf-8"))


def _save_cache():
    with CACHE_LOCK:
        snapshot = dict(LOOKUP_CACHE)
    CACHE_PATH.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _lookup_title_lyrics(title):
    user_agent = _next_user_agent()
    cache_key = title.casefold()
    with CACHE_LOCK:
        if cache_key in LOOKUP_CACHE and LOOKUP_CACHE[cache_key]:
            return {"title": title, "lyrics": LOOKUP_CACHE[cache_key]}

    search_response = requests.get(
        "https://lite.duckduckgo.com/lite",
        params={"q": f"{title} genius.com"},
        headers={"User-Agent": user_agent},
        timeout=10,
    )
    search_response.raise_for_status()
    search_html = search_response.text
    if "anomaly-modal" in search_html or "bots use DuckDuckGo too" in search_html:
        raise RuntimeError("DuckDuckGo Lite returned a bot challenge page.")

    genius_url = ""
    scan_offset = 0
    for tag in RESULT_LINK_TAG_PATTERN.findall(search_html):
        tag_pos = search_html.find(tag, scan_offset)
        if tag_pos >= 0:
            scan_offset = tag_pos + len(tag)
        href = unescape(HREF_PATTERN.search(tag).group(1))
        if href.startswith("//"):
            href = f"https:{href}"
        if (
            href.startswith("/l/?")
            or href.startswith("https://duckduckgo.com/l/?")
            or href.startswith("http://duckduckgo.com/l/?")
        ):
            parsed = urlparse(href)
            params = parse_qs(parsed.query)
            href = unquote(params["uddg"][0])
        if "genius.com" in href.casefold():
            genius_url = href
            break

    if not genius_url:
        logger.info("No Genius result found for title=%s", title)
        return {"title": title, "lyrics": ""}

    lyrics_response = requests.get(
        genius_url,
        headers={"User-Agent": _next_user_agent()},
        timeout=10,
    )
    lyrics_html = lyrics_response.text
    lines = []
    for start_match in LYRICS_CONTAINER_PATTERN.finditer(lyrics_html):
        start_idx = start_match.end()
        depth = 1
        end_idx = start_idx
        for token in DIV_TOKEN_PATTERN.finditer(lyrics_html, pos=start_idx):
            tag = token.group(0).lower()
            depth = depth - 1 if tag.startswith("</div") else depth + 1
            if depth == 0:
                end_idx = token.start()
                break
        chunk = lyrics_html[start_idx:end_idx]
        chunk = BR_PATTERN.sub("\n", chunk)
        chunk = TAG_PATTERN.sub("", chunk)
        chunk = unescape(chunk)
        for line in chunk.splitlines():
            text = line.strip()
            if text:
                lines.append(text)
    lyrics = "\n".join(lines).strip()

    with CACHE_LOCK:
        if lyrics:
            LOOKUP_CACHE[cache_key] = lyrics
        elif cache_key in LOOKUP_CACHE:
            del LOOKUP_CACHE[cache_key]
    return {"title": title, "lyrics": lyrics}


def _translate_lyrics_to_english(client, title, lyrics):
    translation = TRANSLATE_LYRICS_QUERIER.run(
        client=client,
        system_context={"title": title},
        input=lyrics,
        token_budgets=[800, 1400],
    ).response
    return (translation or "").strip() or lyrics


def _normalize_full_context(full_context):
    if isinstance(full_context, dict):
        normalized = dict(full_context)
        recent = normalized.get("recent_messages", [])
        if isinstance(recent, str):
            normalized["recent_messages"] = [line.strip() for line in recent.splitlines() if line.strip()]
        elif recent is None:
            normalized["recent_messages"] = []
        return normalized
    normalized = {
        "input_text": str(full_context or ""),
        "user": {},
        "global_memory": "",
        "recent_messages": [],
    }
    return normalized


def _build_ner_corpus(full_context):
    full_context = _normalize_full_context(full_context)
    parts = []
    input_text = str(full_context.get("input_text", "")).strip()
    if input_text:
        parts.append(f"latest_input: {input_text}")
    user = full_context.get("user", {})
    if not isinstance(user, dict):
        user = {}
    summary = str(user.get("conversation_summary", "")).strip()
    if summary:
        parts.append(f"conversation_summary: {summary}")
    global_memory = str(full_context.get("global_memory", "")).strip()
    if global_memory:
        parts.append(f"global_memory: {global_memory}")
    for msg in full_context.get("recent_messages", []) or []:
        if isinstance(msg, dict):
            speaker = str(msg.get("speaker", "")).strip()
            content = str(msg.get("content", msg.get("text", ""))).strip()
        else:
            speaker = ""
            content = str(msg).strip()
        if content:
            parts.append(f"recent_message[{speaker}]: {content}")
    return "\n".join(parts)


def lookup_key_text_context(client, full_context):
    try:
        full_context = _normalize_full_context(full_context)
        ner_corpus = _build_ner_corpus(full_context)
        model_args = SONG_TITLE_NER_QUERIER.run(
            client=client,
            system_context={"task": "song_title_ner", "full_context": full_context},
            input=ner_corpus,
        ).arguments
        possible = []
        for item in model_args["possible_song_titles"]:
            title = re.sub(r"\s+", " ", str(item).strip().lower())
            if not title:
                continue
            verdict = SONG_TITLE_VERIFIER_QUERIER.run(
                client=client,
                system_context={"message_text": ner_corpus, "candidate_text": title, "full_context": full_context},
                input=title,
            ).arguments
            if verdict["is_song_title"]:
                possible.append(title)

        logger.info("Lookup song title candidates: %s", possible)
        result_payload = {}
        for title in possible:
            try:
                result = _lookup_title_lyrics(title)
                lyrics = result["lyrics"]
                if lyrics:
                    lyrics = _translate_lyrics_to_english(client, result["title"], lyrics)
                    with CACHE_LOCK:
                        LOOKUP_CACHE[result["title"].casefold()] = lyrics
                    result_payload[result["title"]] = lyrics
            except Exception:
                logger.exception("Song lookup failed for title=%s", title)

        return result_payload
    except Exception:
        logger.exception("Retrieved-context lookup failed; continuing without retrieved context.")
        return {}


_load_cache()
atexit.register(_save_cache)
