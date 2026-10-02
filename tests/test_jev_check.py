"""Tests for scripts/jev_check.py, the advisory extraction-fidelity
check: every way it skips, what it asks, how it reports a disagreement,
and that it never fails a build. The TypeSafe SDK is a scripted fake
throughout; nothing here reaches the network or bills a call.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "origins"
PAGES = {
    "deepgram_pricing": "deepgram.html",
    "assemblyai_pricing": "assemblyai.md",
    "elevenlabs_pricing": "elevenlabs.md",
    "livekit_pricing": "livekit.html",
}


class FakeAPIError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status = status


class FakeNoul:
    def __init__(self, instructions):
        self.instructions = instructions


class FakeClient:
    """Answers every question 0.97 unless ``answer`` says otherwise, and
    raises whatever ``raises`` returns for a call."""

    def __init__(self, calls, answer=None, raises=None):
        self.calls = calls
        self.answer = answer or (lambda qid, state: 0.97)
        self.raises = raises or (lambda state, questions: None)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def system_one(self, state, questions):
        self.calls.append((state, questions))
        error = self.raises(state, questions)
        if error is not None:
            raise error
        return types.SimpleNamespace(
            nouls={
                qid: types.SimpleNamespace(noul=self.answer(qid, state))
                for qid in questions
            },
            usage=types.SimpleNamespace(input_tokens=1200, output_tokens=0),
            model="jev-test",
            request_id="req_test",
        )


@pytest.fixture
def jev(tmp_path, monkeypatch):
    """The script as a module, its spend file in a temp dir, its page
    fetch served from the recorded fixtures, and a recorder for SDK calls."""
    spec = importlib.util.spec_from_file_location(
        "jev_check", str(ROOT / "scripts" / "jev_check.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "SPEND_FILE", tmp_path / "spend.jsonl")
    urls = {url: PAGES[name] for name, url in module.ORIGIN_URLS.items()}
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        return (FIXTURES / urls[url]).read_text()

    monkeypatch.setattr(module, "fetch_text", fake_fetch)
    module.fetched = fetched
    module.calls = []
    return module


def _install_sdk(monkeypatch, module, **client_kwargs):
    sdk = types.ModuleType("typesafe_sdk")
    sdk.Noul = FakeNoul
    sdk.TypeSafeAPIError = FakeAPIError
    sdk.TypeSafeClient = lambda: FakeClient(module.calls, **client_kwargs)
    monkeypatch.setitem(sys.modules, "typesafe_sdk", sdk)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-a-credential")


def _spend(module):
    return [json.loads(line) for line in module.SPEND_FILE.read_text().splitlines()]


# Every way it steps aside


def test_without_a_key_it_skips_before_any_fetch_or_call(jev, monkeypatch, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert jev.main() == 0
    assert "skipped: TYPESAFE_API_KEY isn't set" in capsys.readouterr().out
    assert jev.fetched == [] and not jev.SPEND_FILE.exists()


def test_without_the_sdk_it_skips(jev, monkeypatch, capsys):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-a-credential")
    monkeypatch.setitem(sys.modules, "typesafe_sdk", None)  # import raises
    assert jev.main() == 0
    assert "skipped: typesafe-sdk isn't installed" in capsys.readouterr().out
    assert jev.fetched == []


def test_a_refused_preflight_skips_before_any_page_is_fetched(jev, monkeypatch, capsys):
    _install_sdk(monkeypatch, jev, raises=lambda state, questions: FakeAPIError(401))
    assert jev.main() == 0
    assert "skipped: preflight failed" in capsys.readouterr().out
    assert jev.fetched == [] and len(jev.calls) == 1


def test_an_unreachable_api_at_preflight_skips(jev, monkeypatch, capsys):
    _install_sdk(
        monkeypatch, jev, raises=lambda state, questions: ConnectionError("down")
    )
    assert jev.main() == 0
    assert "skipped: preflight unreachable" in capsys.readouterr().out


def test_one_vendors_refusal_leaves_the_others_checked(jev, monkeypatch, capsys):
    def raises(state, questions):
        return FakeAPIError(500) if "u3-rt-pro" in state else None

    _install_sdk(monkeypatch, jev, raises=raises)
    assert jev.main() == 0
    out = capsys.readouterr().out
    assert "assemblyai_pricing: jev refused" in out
    assert "deepgram/aura-2 kchar=0.03: agrees" in out


# What it asks and what it reports


def test_it_preflights_then_asks_one_batch_per_vendor(jev, monkeypatch, capsys):
    _install_sdk(monkeypatch, jev)
    assert jev.main() == 0
    preflight, *vendors = jev.calls
    assert list(preflight[1]) == ["preflight"]
    assert len(vendors) == len(jev.ORIGIN_URLS)
    # One question per (record, unit); Deepgram's dual-unit Nova-3 gets two.
    deepgram = vendors[0][1]
    assert {"nova-3-monolingual::audio_minute",
            "nova-3-monolingual::streaming_audio_minute"} <= set(deepgram)
    question = deepgram["aura-2::kchar"].instructions
    assert "$0.03" in question and "per 1,000 characters" in question
    assert "aura 2" in question
    assert "every extracted row agrees with its page" in capsys.readouterr().out


def test_a_low_answer_is_flagged_for_review_and_the_exit_stays_zero(
    jev, monkeypatch, capsys
):
    _install_sdk(
        monkeypatch, jev,
        answer=lambda qid, state: 0.08 if qid == "aura-2::kchar" else 0.97,
    )
    assert jev.main() == 0
    out = capsys.readouterr().out
    assert "deepgram/aura-2 kchar=0.03: PAGE DISAGREES (p=0.08) - review" in out
    assert "deepgram/aura-1 kchar=0.015: agrees (p=0.97)" in out
    assert "1 row(s) flagged for review; advisory only, build continues" in out


def test_spend_is_recorded_for_every_call(jev, monkeypatch):
    _install_sdk(monkeypatch, jev)
    jev.main()
    rows = _spend(jev)
    assert len(rows) == len(jev.calls) == 1 + len(jev.ORIGIN_URLS)
    assert rows[0]["purpose"] == "origin-fidelity preflight"
    for row in rows:
        assert row["provider"] == "typesafe" and row["model"] == "jev-test"
        assert row["input_tokens"] == 1200 and row["request_id"] == "req_test"
        assert row["questions"] >= 1 and row["wall_ms"] >= 0


def test_a_content_block_splits_the_state_and_keeps_each_highest_answer(
    jev, monkeypatch, capsys
):
    # A 403 on the whole AssemblyAI state; the halves answer, and the half
    # that holds a row's rate is the one whose answer counts.
    full = {}

    def raises(state, questions):
        if "u3-rt-pro" in state and "universal-2" in state:
            full["state"] = state
            return FakeAPIError(403)
        return None

    def answer(qid, state):
        model = qid.split("::")[0]
        return 0.95 if f"`{model}`" in state else 0.05

    _install_sdk(monkeypatch, jev, raises=raises, answer=answer)
    assert jev.main() == 0
    out = capsys.readouterr().out
    assert "assemblyai/universal-2 audio_minute=0.0025: agrees (p=0.95)" in out
    assert "assemblyai/u3-rt-pro session_minute=0.0075: agrees (p=0.95)" in out
    purposes = [row["purpose"] for row in _spend(jev)]
    assert purposes.count("origin-fidelity assemblyai_pricing (split)") == 2


# Per-vendor skips: one vendor's page failing never stops the others


def _serve(jev, monkeypatch, name, page):
    """Serve ``page`` (text, or an exception to raise) for one vendor and
    the recorded fixture for every other."""
    target = jev.ORIGIN_URLS[name]
    urls = {url: PAGES[n] for n, url in jev.ORIGIN_URLS.items()}

    def fetch(url):
        jev.fetched.append(url)
        if url == target:
            if isinstance(page, Exception):
                raise page
            return page
        return (FIXTURES / urls[url]).read_text()

    monkeypatch.setattr(jev, "fetch_text", fetch)


@pytest.mark.parametrize(
    ("page", "line"),
    [
        (ConnectionError("down"), "deepgram_pricing: page fetch failed (down); skipped"),
        (
            (
                '<script id="product-offer-schema" type="application/ld+json">'
                '[{"offers": []}]</script>'
            ),
            "deepgram_pricing: parser raised (",
        ),
        ("a page with no rates on it", "deepgram_pricing: parser yielded nothing"),
    ],
)
def test_one_vendors_unusable_page_is_skipped_and_the_rest_are_checked(
    jev, monkeypatch, capsys, page, line
):
    _install_sdk(monkeypatch, jev)
    _serve(jev, monkeypatch, "deepgram_pricing", page)
    assert jev.main() == 0
    out = capsys.readouterr().out
    assert line in out
    assert "assemblyai/universal-2 audio_minute=0.0025: agrees" in out
    # The preflight plus one batch for each vendor whose page parsed.
    assert len(jev.calls) == len(jev.ORIGIN_URLS)
