"""Tests for the pluggable sandbox image + setup layer (design §C3, R6).

Two layers of coverage:
  1. Always-run unit tests that exercise the image/setup *resolution* logic with
     ``subprocess`` mocked — proving:
       * ``sandbox.image`` overrides the base image,
       * ``sandbox.setup`` steps land in the derived Dockerfile (and hash),
       * no image + no setup reproduces today's ``_resolve_image`` behavior
         byte-for-byte (backward-compat regression),
       * ``setup`` normalization (string vs list, order, blanks),
       * the cache tag changes when image / setup / packages change,
       * a custom base image layers the entrypoint + generic scorer + ENTRYPOINT.
  2. A Docker-gated end-to-end test that runs a minimal Node target through the
     generic scorer. It is skipped unless a working Docker daemon is present, so
     it never runs on the Windows dev host but does run in CI / Docker envs.
"""

import hashlib
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from sandbox.runner import (
    SANDBOX_IMAGE,
    _base_image,
    _normalize_setup,
    _resolve_image,
    ensure_derived_image,
    run_in_sandbox,
)


# ── Docker availability gate for the end-to-end test ─────────────────────────
def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=20
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


_HAVE_DOCKER = _docker_available()


# ── Fake Docker: dispatches on the command so builds can be inspected ────────
class FakeDocker:
    """Records subprocess calls and simulates docker image inspect / build.

    ``base_cached`` controls whether the default base image (SANDBOX_IMAGE) is
    reported as already built. Derived / deps tags always miss so a build is
    triggered and its Dockerfile (passed via ``input=``) can be asserted.
    """

    def __init__(self, base_cached: bool = True, build_ok: bool = True) -> None:
        self.calls: list[tuple[list, dict]] = []
        self.base_cached = base_cached
        self.build_ok = build_ok

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        if command[:3] == ["docker", "image", "inspect"]:
            target = command[3]
            if target == SANDBOX_IMAGE and self.base_cached:
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 1, "", "not found")
        if command[:2] == ["docker", "build"]:
            code = 0 if self.build_ok else 1
            return subprocess.CompletedProcess(command, code, "built", "boom")
        return subprocess.CompletedProcess(command, 0, "", "")

    @property
    def build_calls(self) -> list[tuple[list, dict]]:
        return [(c, k) for (c, k) in self.calls if c[:2] == ["docker", "build"]]

    def dockerfile_for(self, tag: str) -> str:
        for command, kwargs in self.build_calls:
            if tag in command:
                return kwargs.get("input", "")
        raise AssertionError(f"no build call for tag {tag}; calls={self.build_calls}")


def _resolve(cfg: dict, fake: FakeDocker) -> str:
    with patch("sandbox.runner.subprocess.run", side_effect=fake):
        return _resolve_image(cfg, repo_root="/repo")


# ── _normalize_setup ─────────────────────────────────────────────────────────
class TestNormalizeSetup:
    def test_none_and_empty_yield_empty_list(self):
        assert _normalize_setup(None) == []
        assert _normalize_setup([]) == []
        assert _normalize_setup("") == []

    def test_single_string_becomes_one_element_list(self):
        assert _normalize_setup("npm ci") == ["npm ci"]

    def test_list_preserves_order(self):
        steps = ["apt-get update", "apt-get install -y foo", "npm ci"]
        assert _normalize_setup(steps) == steps

    def test_blank_entries_dropped_and_stripped(self):
        assert _normalize_setup(["  npm ci  ", "", "  ", "go build"]) == [
            "npm ci",
            "go build",
        ]

    def test_duplicates_are_preserved(self):
        # Setup steps are order-sensitive build commands; unlike pip packages we
        # deliberately do NOT dedupe.
        assert _normalize_setup(["make", "make"]) == ["make", "make"]


# ── _base_image ────────────────────────────────────────────────────────────--
class TestBaseImage:
    def test_absent_or_null_is_none(self):
        assert _base_image({}) is None
        assert _base_image({"image": None}) is None
        assert _base_image(None) is None

    def test_blank_string_is_none(self):
        assert _base_image({"image": "   "}) is None

    def test_value_is_stripped(self):
        assert _base_image({"image": "  node:20-alpine "}) == "node:20-alpine"


# ── Backward-compat regression: no image + no setup == today ─────────────────-
class TestBackwardCompatResolution:
    def test_no_image_no_setup_no_packages_returns_base_image(self):
        fake = FakeDocker(base_cached=True)
        assert _resolve({}, fake) == SANDBOX_IMAGE
        # No derived/deps build happens when there is nothing to layer.
        assert fake.build_calls == []

    def test_no_image_no_setup_with_packages_matches_ensure_deps_image(self):
        fake = FakeDocker(base_cached=True)
        cfg = {"pip_install": "scipy numpy"}
        tag = _resolve(cfg, fake)

        # Byte-identical to today's ensure_deps_image resolution: packages are
        # normalized (sorted, deduped) and hashed the SAME way.
        packages = ["numpy", "scipy"]
        digest = hashlib.sha1(("\n".join(packages)).encode()).hexdigest()[:12]
        expected_tag = f"{SANDBOX_IMAGE}:deps-{digest}"
        assert tag == expected_tag

        dockerfile = fake.dockerfile_for(expected_tag)
        assert dockerfile == (
            f"FROM {SANDBOX_IMAGE}\n"
            f"RUN pip install --no-cache-dir numpy scipy\n"
        )
        # The pip-only path builds from stdin context (no COPY), as today.
        build_cmd = fake.build_calls[0][0]
        assert build_cmd == ["docker", "build", "-t", expected_tag, "-"]


# ── Custom image overrides the base ──────────────────────────────────────────
class TestCustomImageResolution:
    def test_custom_image_layers_setup_scorer_and_entrypoint(self):
        fake = FakeDocker()
        cfg = {"image": "node:20-alpine", "setup": ["npm ci", "npm run build"]}
        tag = _resolve(cfg, fake)

        assert tag.startswith(f"{SANDBOX_IMAGE}:derived-")
        dockerfile = fake.dockerfile_for(tag)
        # Base overridden.
        assert dockerfile.startswith("FROM node:20-alpine\n")
        # Setup steps present, in order.
        assert "RUN npm ci\n" in dockerfile
        assert "RUN npm run build\n" in dockerfile
        assert dockerfile.index("RUN npm ci") < dockerfile.index("RUN npm run build")
        # Entrypoint + generic scorer copied in and entrypoint set (so the
        # generic scorer works in an arbitrary base image).
        assert "COPY sandbox/entrypoint.sh /sandbox/entrypoint.sh" in dockerfile
        assert "COPY sandbox/score_generic.sh /sandbox/score_generic.sh" in dockerfile
        assert 'ENTRYPOINT ["/sandbox/entrypoint.sh"]' in dockerfile
        # No pip layer is forced for a non-Python base.
        assert "pip install" not in dockerfile
        # Custom base builds with a real context (repo_root) so COPY works.
        build_cmd = fake.build_calls[0][0]
        assert build_cmd == ["docker", "build", "-t", tag, "-f", "-", "/repo"]

    def test_custom_image_does_not_build_default_base(self):
        fake = FakeDocker()
        cfg = {"image": "golang:1.22-alpine", "setup": "go build ./..."}
        _resolve(cfg, fake)
        # The default sandbox base is never inspected/built for a custom image.
        base_inspects = [
            c for (c, _k) in fake.calls
            if c[:3] == ["docker", "image", "inspect"] and c[3] == SANDBOX_IMAGE
        ]
        assert base_inspects == []

    def test_custom_image_layers_pip_only_when_packages_present(self):
        fake = FakeDocker()
        cfg = {"image": "python:3.12-slim", "pip_install": "numpy"}
        tag = _resolve(cfg, fake)
        dockerfile = fake.dockerfile_for(tag)
        assert "RUN pip install --no-cache-dir numpy" in dockerfile


# ── Default base + setup steps ───────────────────────────────────────────────
class TestDefaultBaseWithSetup:
    def test_default_base_setup_layers_on_sandbox_image(self):
        fake = FakeDocker(base_cached=True)
        cfg = {"setup": ["apt-get update"], "pip_install": "numpy"}
        tag = _resolve(cfg, fake)

        assert tag.startswith(f"{SANDBOX_IMAGE}:derived-")
        dockerfile = fake.dockerfile_for(tag)
        assert dockerfile.startswith(f"FROM {SANDBOX_IMAGE}\n")
        assert "RUN apt-get update\n" in dockerfile
        assert "RUN pip install --no-cache-dir numpy\n" in dockerfile
        # Default base already bundles the scorer — no COPY, stdin context.
        assert "COPY sandbox/entrypoint.sh" not in dockerfile
        build_cmd = fake.build_calls[0][0]
        assert build_cmd == ["docker", "build", "-t", tag, "-"]


# ── Cache-tag sensitivity ─────────────────────────────────────────────────────
class TestDerivedImageCaching:
    def _tag(self, cfg: dict) -> str:
        return _resolve(cfg, FakeDocker())

    def test_tag_changes_with_image(self):
        a = self._tag({"image": "node:20-alpine", "setup": ["npm ci"]})
        b = self._tag({"image": "node:18-alpine", "setup": ["npm ci"]})
        assert a != b

    def test_tag_changes_with_setup(self):
        a = self._tag({"image": "node:20-alpine", "setup": ["npm ci"]})
        b = self._tag({"image": "node:20-alpine", "setup": ["npm ci", "npm test"]})
        assert a != b

    def test_tag_changes_with_packages(self):
        a = self._tag({"setup": ["echo hi"], "pip_install": "numpy"})
        b = self._tag({"setup": ["echo hi"], "pip_install": "numpy scipy"})
        assert a != b

    def test_cached_derived_image_is_reused_without_rebuild(self):
        # When the derived tag already exists, no build is issued.
        def fake_run(command, **kwargs):
            if command[:3] == ["docker", "image", "inspect"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            raise AssertionError(f"unexpected build call: {command}")

        with patch("sandbox.runner.subprocess.run", side_effect=fake_run):
            tag = _resolve_image(
                {"image": "node:20-alpine", "setup": ["npm ci"]}, repo_root="/repo"
            )
        assert tag.startswith(f"{SANDBOX_IMAGE}:derived-")


# ── Build-failure fallback ────────────────────────────────────────────────────
class TestDerivedImageFallback:
    def test_falls_back_to_base_on_build_failure(self):
        fake = FakeDocker(build_ok=False)
        with patch("sandbox.runner.subprocess.run", side_effect=fake):
            tag = ensure_derived_image(
                "node:20-alpine", ["npm ci"], [],
                repo_root="/repo", is_default_base=False,
            )
        assert tag == "node:20-alpine"


# ── End-to-end (Docker): a Node target through the generic scorer ────────────-
_NODE_PROGRAM = """\
// Minimal non-Python target: run a workload, print the LoopBench speed marker.
const start = Date.now();
let acc = 0;
for (let i = 0; i < 2_000_000; i++) acc += i % 7;
const elapsed = Date.now() - start;
console.log("LOOPBENCH_SPEED_MS=" + elapsed);
// Correctness signal: exit 0 when the computed value is sane.
process.exit(acc >= 0 ? 0 : 1);
"""


@pytest.mark.skipif(not _HAVE_DOCKER, reason="Docker daemon not available on this host")
def test_node_target_end_to_end_generic_scorer(tmp_path: Path):
    program = tmp_path / "program.js"
    program.write_text(_NODE_PROGRAM, encoding="utf-8")
    # A placeholder test file (unused — the command runs the node program).
    test_file = tmp_path / "placeholder.txt"
    test_file.write_text("unused\n", encoding="utf-8")

    sandbox_cfg = {
        "image": "node:20-alpine",
        "test_command": "node /workspace/program.js",
        "repeats": 3,
        "timeout": 300,
    }

    result = run_in_sandbox(
        program_path=str(program),
        test_file=str(test_file),
        sandbox_cfg=sandbox_cfg,
    )

    # Correctness from exit code (generic scorer path — no python3 in image).
    assert result.get("correctness") == 1.0
    assert result.get("all_passed") is True
    # A speed distribution comes back via the generic (awk) scorer.
    assert result.get("speed_ms") is not None
    assert result.get("runs", 0) >= 1
    assert isinstance(result.get("speed_ms_samples"), list)
    assert len(result["speed_ms_samples"]) >= 1
    assert result.get("combined_score", 0.0) > 0.0
