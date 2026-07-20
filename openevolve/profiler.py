"""
LoopBench Profiler (design §C2, requirement R4).

Captures execution hotspots for the baseline workload so the LLM can localize
the hot path instead of guessing.

Two paths:
  * Default (Python): profile the workload with ``cProfile`` and parse the
    resulting ``pstats`` data into the top ``max_hotspots`` hotspots ranked by
    self-time (``tottime``).
  * Override (non-Python): when a ``sandbox.profile_command`` is configured, run
    that command instead and parse its textual output generically (best-effort).

Design constraints:
  * The pstats parser is PURE and testable without Docker — it accepts a
    ``pstats.Stats`` object, a raw marshalled dump, a dump file path, or the raw
    stats dict, and returns a sorted, truncated list of compact hotspot dicts.
  * Profiling never raises: on any error the functions return an empty hotspot
    list so the optimization loop can continue (R4.4).
"""

import re
from typing import Any, List, Optional

# Default number of hotspots to keep (design §Data Models: prompt.max_hotspots).
DEFAULT_MAX_HOTSPOTS = 5

# Where the in-container cProfile run writes its marshalled stats dump.
DEFAULT_PROFILE_DUMP = "/results/profile.out"

# A hotspot record: a compact, prompt-friendly dict.
Hotspot = dict[str, Any]


def _coerce_max_hotspots(max_hotspots: Optional[int]) -> Optional[int]:
    """Normalize ``max_hotspots`` to a non-negative int (or None for no limit)."""
    if max_hotspots is None:
        return DEFAULT_MAX_HOTSPOTS
    try:
        value = int(max_hotspots)
    except (TypeError, ValueError):
        return DEFAULT_MAX_HOTSPOTS
    return value if value >= 0 else DEFAULT_MAX_HOTSPOTS


def _extract_stats_dict(profile_data: Any) -> dict:
    """Best-effort extraction of the raw pstats mapping from many input types.

    Accepts a ``pstats.Stats`` object, a ``cProfile.Profile``, the raw stats
    dict, a marshalled dump (bytes), or a path to a dump file. Returns an empty
    dict when nothing usable can be extracted.
    """
    if profile_data is None:
        return {}

    # pstats.Stats and cProfile.Profile (after create_stats) expose ``.stats``.
    stats_attr = getattr(profile_data, "stats", None)
    if isinstance(stats_attr, dict):
        return stats_attr

    # A cProfile.Profile that has not yet snapshotted its stats.
    create_stats = getattr(profile_data, "create_stats", None)
    if callable(create_stats):
        create_stats()
        snapshot = getattr(profile_data, "stats", None)
        return snapshot if isinstance(snapshot, dict) else {}

    # The raw stats mapping itself.
    if isinstance(profile_data, dict):
        return profile_data

    # A marshalled dump produced by ``Profile.dump_stats`` / ``marshal.dump``.
    if isinstance(profile_data, (bytes, bytearray)):
        import marshal

        loaded = marshal.loads(bytes(profile_data))
        return loaded if isinstance(loaded, dict) else {}

    # A filesystem path to a dump file — load via pstats.
    import os

    if isinstance(profile_data, (str, os.PathLike)):
        import pstats

        return pstats.Stats(str(profile_data)).stats  # type: ignore[attr-defined]

    return {}


def parse_pstats(
    profile_data: Any,
    max_hotspots: Optional[int] = DEFAULT_MAX_HOTSPOTS,
) -> List[Hotspot]:
    """Parse cProfile/pstats data into a sorted, truncated hotspot list.

    Ranks by self-time (``tottime``) descending and keeps at most
    ``max_hotspots`` entries. This function is pure and never raises: malformed
    or empty input yields an empty list (R4.4).

    Args:
        profile_data: A ``pstats.Stats``/``cProfile.Profile`` object, the raw
            stats dict, a marshalled dump (bytes), or a dump file path.
        max_hotspots: Maximum number of hotspots to keep (default 5). ``None``
            uses the default; a negative value is treated as the default.

    Returns:
        A list of compact hotspot dicts, each with ``function`` (location id),
        ``tottime`` (self time, seconds), ``cumtime`` (cumulative time), and
        ``ncalls`` (call count), ordered by ``tottime`` descending.
    """
    limit = _coerce_max_hotspots(max_hotspots)
    try:
        stats_dict = _extract_stats_dict(profile_data)
    except Exception:
        return []

    if not isinstance(stats_dict, dict) or not stats_dict:
        return []

    hotspots: List[Hotspot] = []
    for func, raw in stats_dict.items():
        try:
            # pstats value tuple: (call_count, num_calls, tottime, cumtime, callers)
            _cc, num_calls, tottime, cumtime, _callers = raw
            filename, lineno, funcname = func
        except (ValueError, TypeError):
            continue
        try:
            tottime_f = float(tottime)
            cumtime_f = float(cumtime)
            ncalls_i = int(num_calls)
        except (TypeError, ValueError):
            continue
        hotspots.append(
            {
                "function": f"{filename}:{lineno}({funcname})",
                "tottime": round(tottime_f, 6),
                "cumtime": round(cumtime_f, 6),
                "ncalls": ncalls_i,
            }
        )

    # Rank by self-time descending; tie-break on cumulative time for stability.
    hotspots.sort(key=lambda h: (h["tottime"], h["cumtime"]), reverse=True)
    if limit is not None:
        hotspots = hotspots[:limit]
    return hotspots


def parse_profile_dump(
    dump_path: Any,
    max_hotspots: Optional[int] = DEFAULT_MAX_HOTSPOTS,
) -> List[Hotspot]:
    """Load a marshalled cProfile dump from ``dump_path`` and parse hotspots.

    Returns an empty list if the file is missing or unreadable (R4.4).
    """
    try:
        import pstats

        stats = pstats.Stats(str(dump_path))
    except Exception:
        return []
    return parse_pstats(stats, max_hotspots)


def parse_command_output(
    output: Optional[str],
    max_hotspots: Optional[int] = DEFAULT_MAX_HOTSPOTS,
) -> List[Hotspot]:
    """Generic best-effort parser for a non-Python profiler's textual output.

    Used with the ``sandbox.profile_command`` override (R4.6). Looks for lines
    that begin with a numeric self-time followed by an identifier, e.g.::

        0.512  my_module::hot_loop
        0.130  helper::inner

    Ranks by the leading number descending and truncates to ``max_hotspots``.
    Never raises — returns an empty list on any problem (R4.4).
    """
    limit = _coerce_max_hotspots(max_hotspots)
    try:
        hotspots: List[Hotspot] = []
        for line in (output or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            match = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s+(.+)$", stripped)
            if not match:
                continue
            hotspots.append(
                {
                    "function": match.group(2).strip(),
                    "tottime": round(float(match.group(1)), 6),
                    "cumtime": None,
                    "ncalls": None,
                }
            )
        hotspots.sort(key=lambda h: h["tottime"], reverse=True)
        if limit is not None:
            hotspots = hotspots[:limit]
        return hotspots
    except Exception:
        return []


def build_cprofile_command(
    target: str,
    dump_path: str = DEFAULT_PROFILE_DUMP,
) -> str:
    """Build the in-container command that profiles ``target`` with cProfile.

    The workload is run under ``python -m cProfile``, writing a marshalled stats
    dump to ``dump_path`` (inside the container's mounted /results directory).
    ``target`` is the container-side test file path.
    """
    return f"python -m cProfile -o {dump_path} -m pytest {target} -q"


def format_hotspots(hotspots: Optional[List[Hotspot]]) -> str:
    """Format hotspots into a compact, deterministic summary for a prompt.

    Returns an empty string when there are no hotspots so callers can omit the
    section entirely (R4.5 / R4.4).
    """
    if not hotspots:
        return ""

    lines = ["Baseline profile — top hotspots by self-time:"]
    for index, hotspot in enumerate(hotspots, start=1):
        function = hotspot.get("function", "?")
        parts = [f"{index}. {function}"]
        tottime = hotspot.get("tottime")
        if tottime is not None:
            parts.append(f"self={tottime:.6f}s")
        cumtime = hotspot.get("cumtime")
        if cumtime is not None:
            parts.append(f"cum={cumtime:.6f}s")
        ncalls = hotspot.get("ncalls")
        if ncalls is not None:
            parts.append(f"calls={ncalls}")
        lines.append("  " + "  ".join(parts))
    return "\n".join(lines)
