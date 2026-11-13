#!/usr/bin/env python3
"""
One AI client for the whole pipeline: OpenAI first, Groq as the automatic fallback.

Every script used to build `OpenAI(api_key=...)` itself, so an OpenAI outage, rate
limit or empty balance sent scripts to the plain template and captions/picture
timing to an even spread. `client()` returns an object with the same
`.chat.completions.create(...)` and `.audio.transcriptions.create(...)` calls; if the
OpenAI call raises, the same request goes to Groq's OpenAI-compatible API
(https://api.groq.com/openai/v1) with a Groq model.

Keys (repository secrets -> workflow env): OPENAI_API_KEY, GROQ_API_KEY (secret
GROQ_API). Either one alone is enough. AI_PROVIDER=groq skips OpenAI entirely (for an
OpenAI outage or quota problem, or to test the fallback).

Not covered: requests with images (vision_check.py -- advisory only) stay on OpenAI,
since a Groq text model can't see them.
"""
import os
import sys

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
# Preferred Groq models, in order (checked once against Groq's model list, so a retired
# model is skipped instead of failing); a chat request that fails on one tries the next.
# Llama first: gpt-oss is a reasoning model whose hidden reasoning eats the callers'
# max_tokens -- in a 2026-11-13 test it returned 4 of 10 segments, then invalid JSON.
# GROQ_CHAT_MODEL / GROQ_WHISPER_MODEL override.
GROQ_CHAT_MODELS = ("llama-3.3-70b-versatile", "openai/gpt-oss-120b", "llama-3.1-8b-instant")
GROQ_CHAT_TRIES = 2
GROQ_WHISPER_MODELS = ("whisper-large-v3", "whisper-large-v3-turbo")

_GROQ_MODEL_IDS = None


def _log(msg):
    print(f"[ai_client] {msg}", file=sys.stderr)


def openai_key():
    return os.environ.get("OPENAI_API_KEY", "").strip()


def groq_key():
    return (os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_API") or "").strip()


def available():
    """True when at least one provider has a key -- callers' 'can we use AI?' check."""
    return bool(openai_key() or groq_key())


def _openai():
    from openai import OpenAI
    return OpenAI(api_key=openai_key())


def _groq():
    from openai import OpenAI
    return OpenAI(api_key=groq_key(), base_url=GROQ_BASE_URL)


def _groq_models(groq, preferred, env_name):
    """Models from `preferred` that Groq currently serves, in order (env override wins)."""
    global _GROQ_MODEL_IDS
    override = os.environ.get(env_name, "").strip()
    if override:
        return [override]
    if _GROQ_MODEL_IDS is None:
        try:
            _GROQ_MODEL_IDS = {m.id for m in groq.models.list().data}
        except Exception as exc:  # noqa: BLE001 -- listing is a nicety; try them in order
            _log(f"couldn't list Groq models ({type(exc).__name__}); trying them in order")
            _GROQ_MODEL_IDS = set()
    served = [m for m in preferred if m in _GROQ_MODEL_IDS]
    return served or list(preferred)


def _has_images(messages):
    for m in messages or []:
        content = m.get("content") if isinstance(m, dict) else None
        if isinstance(content, list) and any(isinstance(p, dict) and p.get("type") == "image_url"
                                             for p in content):
            return True
    return False


def _with_fallback(call_openai, call_groq, what):
    use_openai = openai_key() and os.environ.get("AI_PROVIDER", "").strip().lower() != "groq"
    if use_openai:
        try:
            return call_openai()
        except Exception as exc:  # noqa: BLE001 -- any OpenAI failure: try Groq
            if not groq_key() or call_groq is None:
                raise
            _log(f"OpenAI {what} failed ({type(exc).__name__}: {str(exc)[:160]}) -- using Groq")
    elif not groq_key() or call_groq is None:
        raise RuntimeError(f"no AI provider available for {what}")
    return call_groq()


class _Completions:
    def create(self, **kwargs):
        def groq_call():
            groq = _groq()
            models = _groq_models(groq, GROQ_CHAT_MODELS, "GROQ_CHAT_MODEL")[:GROQ_CHAT_TRIES]
            for i, model in enumerate(models):
                _log(f"chat via Groq ({model})")
                try:
                    return groq.chat.completions.create(**dict(kwargs, model=model))
                except Exception as exc:  # noqa: BLE001 -- e.g. JSON validation: next model
                    if i == len(models) - 1:
                        raise
                    _log(f"Groq {model} failed ({type(exc).__name__}: {str(exc)[:120]}) -- next model")
        return _with_fallback(lambda: _openai().chat.completions.create(**kwargs),
                              None if _has_images(kwargs.get("messages")) else groq_call, "chat")


class _Transcriptions:
    def create(self, **kwargs):
        def groq_call():
            groq = _groq()
            model = _groq_models(groq, GROQ_WHISPER_MODELS, "GROQ_WHISPER_MODEL")[0]
            _log(f"transcription via Groq ({model})")
            f = kwargs.get("file")
            if hasattr(f, "seek"):
                f.seek(0)  # the failed OpenAI upload may have read the file already
            return groq.audio.transcriptions.create(**dict(kwargs, model=model))
        return _with_fallback(lambda: _openai().audio.transcriptions.create(**kwargs),
                              groq_call, "transcription")


class _Namespace:
    pass


def client():
    """Drop-in for OpenAI(api_key=...): .chat.completions.create / .audio.transcriptions.create."""
    c = _Namespace()
    c.chat, c.audio = _Namespace(), _Namespace()
    c.chat.completions = _Completions()
    c.audio.transcriptions = _Transcriptions()
    return c
