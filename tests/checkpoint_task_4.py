"""
Task 4 checkpoint: verify core repo-mapper components on real algotune examples.

Runs scanner → import analyzer → file analyzer → relevance scorer → context builder
and checks that evaluator.py ranks highly for initial_program.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
ALGOTUNE = REPO_ROOT / "examples" / "algotune" / "affine_transform_2d"


def run_checkpoint() -> int:
    from openevolve.repo_mapper.models import RepoMapperConfig
    from openevolve.repo_mapper.scanner import RepositoryScanner
    from openevolve.repo_mapper.import_analyzer import ImportAnalyzer
    from openevolve.repo_mapper.file_analyzer import FileAnalyzer
    from openevolve.repo_mapper.relevance_scorer import RelevanceScorer
    from openevolve.repo_mapper.context_builder import ContextBuilder

    if not ALGOTUNE.is_dir():
        print(f"SKIP: algotune example not found at {ALGOTUNE}")
        return 0

    config = RepoMapperConfig(token_budget=2000, max_relevant_files=10)
    repo_path = REPO_ROOT.resolve()
    target = Path("examples/algotune/affine_transform_2d/initial_program.py")

    # 1. Scanner
    scanner = RepositoryScanner(config)
    repo_map = scanner.scan(repo_path)
    assert len(repo_map.files) > 0, "Repository scan returned no files"
    print(f"OK Scanner: {len(repo_map.files)} files indexed")

    # 2. Import graph
    import_analyzer = ImportAnalyzer(config)
    import_graph = import_analyzer.analyze(repo_map, target)
    print(f"OK Import graph: {len(import_graph.relations)} relations")

    # 3. File descriptors (target directory + top candidates)
    file_analyzer = FileAnalyzer(config)
    descriptors = {}
    task_dir = Path("examples/algotune/affine_transform_2d")
    for rel_path, node in repo_map.files.items():
        if not node.is_dir and rel_path.parent == task_dir:
            descriptors[rel_path] = file_analyzer.analyze_file(
                node.absolute_path, rel_path
            )

    assert target in descriptors or any(
        p.name == "initial_program.py" for p in descriptors
    ), "Target descriptor missing"
    print(f"OK File analyzer: {len(descriptors)} descriptors in task dir")

    # 4. Relevance scoring
    scorer = RelevanceScorer(config)
    scores = scorer.score_files(target, repo_map, import_graph, descriptors)
    assert scores, "No relevance scores produced"
    top_names = [s.file_path.name for s in scores[:5]]
    print(f"OK Top 5 by relevance: {top_names}")

    evaluator_rank = next(
        (i for i, s in enumerate(scores) if s.file_path.name == "evaluator.py"),
        None,
    )
    assert evaluator_rank is not None, "evaluator.py not in scores"
    assert evaluator_rank < 5, (
        f"evaluator.py should rank in top 5 for initial_program.py, got rank {evaluator_rank + 1}"
    )
    print(f"OK evaluator.py rank: {evaluator_rank + 1} (score={scores[evaluator_rank].total_score:.3f})")

    # 5. Context map
    builder = ContextBuilder(config)
    context = builder.build(target, repo_map, scores, descriptors)
    assert context.token_count <= config.token_budget * 1.1
    relevant_names = [p.name for p, _, _ in context.relevant_files]
    print(f"OK Context map: {context.token_count}/{config.token_budget} tokens")
    print(f"  Relevant files: {relevant_names}")

    assert "evaluator.py" in relevant_names or any(
        n in ("evaluator.py", "config.yaml") for n in relevant_names
    ), "Expected evaluator.py or config.yaml in context"

    prompt = context.to_prompt_section()
    assert "## Repository Context" in prompt
    assert "initial_program.py" in prompt
    print("OK Prompt section formatted correctly")

    print("\nTask 4 checkpoint PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(run_checkpoint())
