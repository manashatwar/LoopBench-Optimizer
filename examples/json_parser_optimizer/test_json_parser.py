"""
Pytest suite for the LoopBench JSON Parser demo.

Correctness is checked against Python's stdlib `json.loads` (the source of
truth). Any mismatch fails the gate and the candidate is rejected. A speed
test parses a large document and emits LOOPBENCH_SPEED_MS for the evaluator.
"""
import importlib.util
import json
import os
import time
import types

# pyrefly: ignore [missing-import]
import pytest

_PROGRAM_PATH = os.environ.get("LOOPBENCH_PROGRAM_PATH")


def _load_program() -> types.ModuleType:
    if _PROGRAM_PATH is None:
        raise RuntimeError("LOOPBENCH_PROGRAM_PATH environment variable not set.")
    spec = importlib.util.spec_from_file_location("evolved_json", _PROGRAM_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load spec from {_PROGRAM_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def prog() -> types.ModuleType:
    return _load_program()


def _big_document() -> str:
    """A deterministic, moderately large JSON document for correctness + speed."""
    obj = {
        "meta": {"version": 2, "ok": True, "note": None, "ratio": 3.14159},
        "records": [
            {
                "id": i,
                "name": f"item-{i}",
                "tags": ["a", "b", "c", f"t{i % 7}"],
                "active": (i % 2 == 0),
                "score": i * 1.5,
                "nested": {"x": i, "y": -i, "label": f"n\\t{i}"},
            }
            for i in range(1500)
        ],
    }
    return json.dumps(obj)


# ── Correctness gate (checked against json.loads) ─────────────────────────────
class TestJsonParserCorrectness:
    """Every case must match stdlib json.loads exactly."""

    @pytest.mark.parametrize("doc", [
        "true", "false", "null",
        "0", "-1", "42", "3.14", "-2.5e3", "1E2",
        '""', '"hello"', '"a\\tb\\nc"', '"quote:\\"q\\""', '"unicode:\\u00e9"',
        "[]", "{}",
        "[1, 2, 3]", '["a", "b", "c"]', "[true, false, null]",
        '{"a": 1}', '{"a": 1, "b": [2, 3], "c": {"d": 4}}',
        '  {  "spaced"  :  [ 1 , 2 ]  }  ',
        '{"deep": {"deeper": {"deepest": [1, [2, [3, [4]]]]}}}',
    ])
    def test_matches_stdlib(self, prog: types.ModuleType, doc: str) -> None:
        assert prog.run_parse(doc) == json.loads(doc)

    def test_big_document_matches_stdlib(self, prog: types.ModuleType) -> None:
        doc = _big_document()
        assert prog.run_parse(doc) == json.loads(doc)

    def test_rejects_trailing_data(self, prog: types.ModuleType) -> None:
        with pytest.raises(Exception):
            prog.run_parse("{} garbage")


# ── Speed benchmark (parseable output) ────────────────────────────────────────
class TestJsonParserSpeed:
    def test_parse_speed(self, prog: types.ModuleType) -> None:
        """Parse the big document repeatedly and emit LOOPBENCH_SPEED_MS."""
        doc = _big_document()
        expected = json.loads(doc)

        # Warm-up + correctness re-check under timing conditions
        assert prog.run_parse(doc) == expected

        iterations = 20
        start = time.perf_counter()
        for _ in range(iterations):
            prog.run_parse(doc)
        elapsed_ms = ((time.perf_counter() - start) / iterations) * 1000

        print(f"\nLOOPBENCH_SPEED_MS={elapsed_ms:.4f}")

        # Hard safety limit — rejects pathological mutations
        assert elapsed_ms < 10000, f"parse took {elapsed_ms:.1f}ms — exceeds 10s limit"
