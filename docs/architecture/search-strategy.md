# Search Strategy

`openevolve/search_strategy.py` — decides which candidate becomes the baseline
for the next generation. Selected via `create_strategy(config)`.

## Strategies

```mermaid
flowchart TB
    H[candidate history] --> S{strategy}
    S -->|greedy| G[pick the single best candidate]
    S -->|beam| B[pick randomly among top-K<br/>beam_width]
    S -->|random_restart| R{generation % restart_interval == 0?}
    R -->|yes| RB[revert to baseline<br/>escape local optima]
    R -->|no| RG[pick the best so far]
    G --> N([next baseline])
    B --> N
    RB --> N
    RG --> N
```

## Comparison

| Strategy | Selection | Parallelizable | Use when |
|----------|-----------|----------------|----------|
| `greedy` (`GreedySearch`) | Always the top candidate | No | Fast convergence on smooth landscapes |
| `beam` (`BeamSearch`) | Random among top-`beam_width` | Yes | More exploration; parallel evaluation |
| `random_restart` (`RandomRestartSearch`) | Periodically revert to baseline every `restart_interval` | No | Escaping local optima |

## Interface

```mermaid
classDiagram
    class SearchStrategy {
      <<abstract>>
      +select_baseline(history, generation) dict
      +should_parallelize() bool
    }
    class GreedySearch
    class BeamSearch {
      +int beam_width
    }
    class RandomRestartSearch {
      +int restart_interval
    }
    SearchStrategy <|-- GreedySearch
    SearchStrategy <|-- BeamSearch
    SearchStrategy <|-- RandomRestartSearch
```

`create_strategy({"strategy": "beam", "beam_width": 5})` returns the matching
implementation; the `OptimizerLoop` calls `select_baseline(history, generation)`
at the end of every generation.
