import numpy as np
from jarvis_brain.voice.speaker import resample
from jarvis_brain.voice.text import SentenceSplitter, speakable


def feed(text: str, step: int = 3) -> list[str]:
    sp = SentenceSplitter()
    out = []
    for i in range(0, len(text), step):
        out += sp.push(text[i : i + step])
    return out + sp.flush()


def test_first_sentence_released_early():
    sp = SentenceSplitter()
    assert sp.push("It is a quarter past ten in the evening. ") == [
        "It is a quarter past ten in the evening."
    ]


def test_abbreviations_and_decimals_dont_split():
    out = feed("Dr. Smith will see you at 3.30 tomorrow, about 2.5 km away. Bring the forms.")
    assert out[0].startswith("Dr. Smith will see you at 3.30 tomorrow, about 2.5 km away.")


def test_streamed_sentences_are_released_individually():
    out = feed(
        "Right then, here's the plan for today. First this. Then that. And finally the other."
    )
    assert out[0] == "Right then, here's the plan for today."
    assert out[-1] == "And finally the other."


def test_sentences_arriving_together_merge_after_the_first():
    sp = SentenceSplitter()
    out = sp.push("Right then, here's the plan for today. First this. Then that. And finally ")
    assert out == ["Right then, here's the plan for today.", "First this. Then that."]


def test_flush_returns_tail_without_punctuation():
    sp = SentenceSplitter()
    assert sp.push("no punctuation at all") == []
    assert sp.flush() == ["no punctuation at all"]


def test_speakable_strips_markdown():
    md = "**Bold** and *italic* with `code`, a [link](http://x.y) and\n- a bullet\n## Heading"
    assert speakable(md) == "Bold and italic with code, a link and a bullet Heading"
    assert speakable("see https://example.com/page") == "see the link"


def test_resample_length_and_identity():
    pcm = (np.sin(np.arange(2400) / 10) * 8000).astype(np.int16).tobytes()
    assert resample(pcm, 24000, 24000) == pcm
    assert len(resample(pcm, 24000, 16000)) == 1600 * 2
