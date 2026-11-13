import io

import os

import sys

from types import SimpleNamespace



import pytest



sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))



import ai_client  # noqa: E402





class FakeProvider:

    """Stands in for an OpenAI(...) client; records calls, optionally fails."""



    def __init__(self, fail=False, models=("openai/gpt-oss-120b", "qwen/qwen3.8-27b", "whisper-large-v3")):

        self.fail, self.calls = fail, []

        self.models = SimpleNamespace(list=lambda: SimpleNamespace(

            data=[SimpleNamespace(id=m) for m in models]))

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._call("chat")))

        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._call("audio")))



    def _call(self, kind):

        def create(**kwargs):

            self.calls.append((kind, kwargs))

            if self.fail:

                raise RuntimeError("429 rate limit")

            return f"{kind}-ok"

        return create





@pytest.fixture

def providers(monkeypatch):

    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")

    monkeypatch.setenv("GROQ_API_KEY", "test-groq")

    monkeypatch.delenv("AI_PROVIDER", raising=False)

    monkeypatch.delenv("GROQ_CHAT_MODEL", raising=False)

    monkeypatch.setattr(ai_client, "_GROQ_MODEL_IDS", None)

    openai, groq = FakeProvider(), FakeProvider()

    monkeypatch.setattr(ai_client, "_openai", lambda: openai)

    monkeypatch.setattr(ai_client, "_groq", lambda: groq)

    return openai, groq





def test_openai_is_used_first(providers):

    openai, groq = providers

    assert ai_client.client().chat.completions.create(model="gpt-4o-mini", messages=[]) == "chat-ok"

    assert len(openai.calls) == 1 and not groq.calls





def test_openai_failure_falls_back_to_groq_with_a_groq_model(providers):

    openai, groq = providers

    openai.fail = True

    assert ai_client.client().chat.completions.create(model="gpt-4o-mini", messages=[]) == "chat-ok"

    assert groq.calls[0][1]["model"] == "openai/gpt-oss-120b"





def test_a_failing_groq_model_is_followed_by_the_next(providers, monkeypatch):

    openai, _ = providers

    openai.fail = True



    class FirstModelFails(FakeProvider):

        def _call(self, kind):

            inner = super()._call(kind)



            def create(**kw):

                if kw.get("model") == "openai/gpt-oss-120b":

                    self.calls.append((kind, kw))

                    raise RuntimeError("Failed to validate JSON")

                return inner(**kw)

            return create

    groq = FirstModelFails()

    monkeypatch.setattr(ai_client, "_groq", lambda: groq)

    assert ai_client.client().chat.completions.create(model="gpt-4o-mini", messages=[]) == "chat-ok"

    assert [c[1]["model"] for c in groq.calls] == ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]





def test_retired_preferred_model_is_skipped(providers, monkeypatch):

    openai, _ = providers

    openai.fail = True

    groq = FakeProvider(models=("openai/gpt-oss-120b",))

    monkeypatch.setattr(ai_client, "_groq", lambda: groq)

    ai_client.client().chat.completions.create(model="gpt-4o-mini", messages=[])

    assert groq.calls[0][1]["model"] == "openai/gpt-oss-120b"





def test_image_requests_never_go_to_groq(providers):

    openai, groq = providers

    openai.fail = True

    msgs = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:"}}]}]

    with pytest.raises(RuntimeError):

        ai_client.client().chat.completions.create(model="gpt-4o-mini", messages=msgs)

    assert not groq.calls





def test_ai_provider_groq_skips_openai(providers, monkeypatch):

    openai, groq = providers

    monkeypatch.setenv("AI_PROVIDER", "groq")

    ai_client.client().chat.completions.create(model="gpt-4o-mini", messages=[])

    assert not openai.calls and groq.calls





def test_transcription_fallback_rewinds_the_audio_file(providers):

    openai, groq = providers

    openai.fail = True

    audio = io.BytesIO(b"RIFF....")

    audio.read()  # the failed OpenAI upload consumed it

    ai_client.client().audio.transcriptions.create(model="whisper-1", file=audio)

    assert groq.calls[0][1]["model"] == "whisper-large-v3" and audio.tell() == 0





def test_without_groq_key_the_openai_error_is_raised(providers, monkeypatch):

    openai, _ = providers

    openai.fail = True

    monkeypatch.delenv("GROQ_API_KEY")

    with pytest.raises(RuntimeError, match="429"):

        ai_client.client().chat.completions.create(model="gpt-4o-mini", messages=[])



def test_reasoning_models_get_low_effort_and_room_for_the_answer(providers, monkeypatch):
    openai, _ = providers
    openai.fail = True
    groq = FakeProvider(models=("openai/gpt-oss-120b",))
    monkeypatch.setattr(ai_client, "_groq", lambda: groq)
    ai_client.client().chat.completions.create(model="gpt-4o-mini", messages=[], max_tokens=900)
    sent = groq.calls[0][1]
    assert sent["reasoning_effort"] == "low" and sent["max_tokens"] == 900 + ai_client.REASONING_EXTRA_TOKENS
