"""Shared test setup.

Spelling proofreader (kahwa_publisher/spelling.py, 2026-10-31): the pipeline tests drive the writer / checker with fake LLMs
that do not know the proofreading prompt, so by default the proofreader's LLM answers "no typos" (the code floor of KNOWN
typos and all the code guards still run). tests/test_numbers_spelling.py exercises the real LLM path with its own fakes.
"""
import pytest

from kahwa_publisher import spelling

REAL_PROOFREAD = spelling.proofread


@pytest.fixture(autouse=True)
def _quiet_proofreader(monkeypatch, request):
    if request.node.get_closest_marker("real_spelling"):
        return
    monkeypatch.setattr(spelling, "proofread",
                        lambda llm, lines, src_text="", log=print: REAL_PROOFREAD(lambda p, temperature=None: '{"typos": []}',
                                                                                   lines, src_text, log=log))


def pytest_configure(config):
    config.addinivalue_line("markers", "real_spelling: use the real spelling proofreader with the test's own fake LLM")
