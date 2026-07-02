# LLM Editing Engine

The engine that turns an LLM response into a verified `.patch`. Unified diffs
written by hand by an LLM fail 20–30% of the time ("corrupt patch"), so the LLM
**never writes diff line numbers** — the diff is always computed with `difflib`.

## Strategy routing

```mermaid
flowchart TB
    G[LLM generates edit] --> Q{rewrite_mode}
    Q -->|full| F[Complete file returned]
    Q -->|search_replace| S[SEARCH/REPLACE blocks]
    Q -->|auto| A{file lines &lt;= threshold}
    A -->|yes| F
    A -->|no| S

    F --> NEW[new file content]
    S --> APPLY[apply blocks by content match<br/>fuzzy fallback on whitespace]
    APPLY --> NEW
    NEW --> DIFF[difflib.unified_diff<br/>guaranteed-valid patch]
    DIFF --> TEST[test in Docker sandbox]
    TEST --> SCORE[keep if it beats baseline]
```

## Modes

| Mode | LLM returns | Applied by | Best for |
|------|-------------|-----------|----------|
| `full` | The complete improved file | Overwrite | Small files |
| `search_replace` | `SEARCH`/`REPLACE` blocks | Exact match, then whitespace-tolerant fuzzy match | Large files (surgical) |
| `auto` | Chosen by file size | `full` ≤ 300 lines, else `search_replace` | Any repo (default) |
| `diff` | A unified diff | `git apply` (lenient) | Legacy / advanced |

## Search / Replace block format

```
<<<<<<< SEARCH
<exact lines copied from the file>
=======
<the replacement lines>
>>>>>>> REPLACE
```

Application order:

```mermaid
flowchart LR
    B[SEARCH block] --> E{exact substring match?}
    E -->|yes| R1[replace once]
    E -->|no| FZ{fuzzy match<br/>trailing whitespace ignored}
    FZ -->|yes| R2[replace window]
    FZ -->|no| ERR[report: SEARCH not found]
```

Leading indentation is preserved (Python-significant); only trailing whitespace
is tolerated during fuzzy matching.

## Why difflib for the patch

Because the patch is derived from the actual before/after file contents, it is
always a valid unified diff that `git apply` accepts — regardless of how the LLM
phrased its edit. Verified on a 1,233-line file: `search_replace` produced a
27-line surgical patch touching only the hot function.
