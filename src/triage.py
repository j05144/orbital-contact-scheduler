"""
triage.py — LLM-assisted triage of dropped satellite contact passes.

Uses IBM Granite (granite3.3:8b via Ollama) to generate a one-sentence
explanation and an operator action recommendation for the 15 most
operationally significant dropped passes.

Selection ranking (Python-side, before any LLM call):
  1. priority ascending  (lower number = higher urgency)
  2. has_alternative False first  (no fallback = more urgent)
  3. delay_min descending  (longer wait = more impact)

Action values: ACCEPT | OVERRIDE | ESCALATE
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

_OLLAMA_HOST  = "http://localhost:11434"
_MODEL        = "granite3.3:2b"
_TOP_N        = 15
_VALID_ACTIONS = {"ACCEPT", "OVERRIDE", "ESCALATE"}

# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def _row_context(row: "pd.Series") -> str:
    """Format a single dropped-pass row as a compact context string."""
    has_alt = row.get("has_alternative") is True or str(row.get("has_alternative")).lower() == "true"
    if not has_alt:
        alt_verdict = "ALTERNATIVE: none available in the planning window."
    elif int(row.get("delay_min", 0)) <= 120:
        alt_verdict = (
            f"ALTERNATIVE: {row.get('alt_station')} in "
            f"{row.get('delay_min')} minutes. This is an acceptable substitute."
        )
    else:
        alt_verdict = (
            f"ALTERNATIVE: {row.get('alt_station')} in "
            f"{row.get('delay_min')} minutes. This is a significant delay."
        )

    return (
        f"Satellite: {row['satellite']} (priority {row['priority']})\n"
        f"Dropped pass: {row['pass_id']}\n"
        f"Scheduled start: {row['earliest_start_utc']}\n"
        f"Candidate stations: {row.get('candidate_stations', 'unknown')}\n"
        f"Blocked by: {row.get('blocked_by', 'unknown')}\n"
        f"{alt_verdict}"
    )


def decide_action(row: "pd.Series") -> tuple[str, str]:
    """Return (action, reason) determined entirely in Python."""
    has_alt = row.get("has_alternative") is True or str(row.get("has_alternative")).lower() == "true"
    p = int(row.get("priority", 3))
    if not has_alt:
        if p in (1, 2):
            return (
                "OVERRIDE",
                f"No alternative contact in the planning window for a priority {p} satellite.",
            )
        else:
            return (
                "ESCALATE",
                "No alternative contact available; routine priority, needs operator review.",
            )
    delay = int(row.get("delay_min", 0))
    alt_station = row.get("alt_station", "unknown")
    if delay > 120:
        return (
            "ESCALATE",
            f"Next opportunity is {delay} minutes away at {alt_station}, a significant delay.",
        )
    return (
        "ACCEPT",
        f"Recovered at {alt_station} after {delay} minutes, an acceptable substitute.",
    )


def _build_prompt(row: "pd.Series") -> str:
    """Build the per-pass prompt sent to Granite (explanation only)."""
    return (
        "You are a satellite mission operations assistant. "
        "A ground contact pass was dropped from the schedule. "
        "Respond with ONLY a valid JSON object — no markdown, no extra text.\n\n"
        f"{_row_context(row)}\n\n"
        'Return exactly this JSON structure:\n'
        '{"explanation": "<one sentence stating why this pass was dropped and which satellite occupied the antenna>"}\n\n'
        "Do not mention alternatives, delays, recommendations, or actions. "
        "Do not use the word 'interference'; the correct term is that another satellite occupied the antenna.\n"
        "Respond with JSON only."
    )


def _build_summary_prompt(stats: dict[str, Any]) -> str:
    """Build the aggregate summary prompt."""
    p1_dropped = stats['p1_total'] - stats['p1_scheduled']
    median_str = (
        f"the median wait for those is {stats['median_delay']} minutes"
        if stats['median_delay'] is not None
        else "no median delay is available"
    )
    return (
        "You are a satellite mission operations assistant. "
        "Write a single paragraph (3-5 sentences) summarising the scheduling run "
        "for an operator. Be direct and factual. No bullet points, no headers.\n\n"
        f"This scheduling run covered {stats['total']} passes in total. "
        f"{stats['scheduled']} passes were successfully scheduled and {stats['dropped']} were dropped. "
        f"All {stats['p1_total']} priority-1 passes were successfully scheduled. "
        f"Zero priority-1 passes were dropped. "
        if p1_dropped == 0 else
        f"This scheduling run covered {stats['total']} passes in total. "
        f"{stats['scheduled']} passes were successfully scheduled and {stats['dropped']} were dropped. "
        f"{stats['p1_scheduled']} of {stats['p1_total']} priority-1 passes were scheduled and "
        f"{p1_dropped} priority-1 {'pass was' if p1_dropped == 1 else 'passes were'} dropped. "
    ) + (
        f"Of the {stats['dropped']} dropped passes, {stats['with_alt']} have an alternative contact "
        f"later in the window; {median_str}. "
        f"The remaining {stats['without_alt']} dropped passes have no alternative at all within the planning window.\n\n"
        "Restate these facts. Do not compute new numbers, do not reassign a number to a different category, "
        "and do not add figures that are not listed above.\n"
        "Write the paragraph now:"
    )


# ---------------------------------------------------------------------------
# LLM interaction
# ---------------------------------------------------------------------------

def _call_granite(prompt: str, *, host: str = _OLLAMA_HOST) -> str:
    """Send a prompt to Granite and return the raw response string."""
    import ollama
    client = ollama.Client(host=host)
    response = client.chat(
        model=_MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.1},
    )
    return response.message.content.strip()


def _parse_json_response(raw: str) -> dict[str, str]:
    """Extract and validate the JSON object from a model response.

    Handles models that wrap JSON in markdown fences or add preamble text.
    Returns the parsed dict, or raises ValueError if unparseable.
    """
    # Strip markdown fences if present.
    text = raw
    if "```" in text:
        # Keep only the content between the first ``` pair.
        parts = text.split("```")
        for part in parts[1:]:
            candidate = part.lstrip("json").strip()
            if candidate.startswith("{"):
                text = candidate
                break

    # Find the first { ... } block.
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in response")

    return json.loads(text[start:end])


def _triage_row(
    row: "pd.Series",
    *,
    host: str = _OLLAMA_HOST,
) -> dict[str, Any]:
    """Call Granite for one dropped pass; return a validated triage dict.

    Retries once on bad JSON, then falls back to a templated response.
    """
    action, reason = decide_action(row)
    prompt = _build_prompt(row)

    for attempt in range(2):
        try:
            raw  = _call_granite(prompt, host=host)
            data = _parse_json_response(raw)
            return {
                "pass_id":     str(row["pass_id"]),
                "satellite":   str(row["satellite"]),
                "priority":    int(row["priority"]),
                "explanation": str(data.get("explanation", "")).strip(),
                "action":      action,
                "reason":      reason,
            }
        except (ValueError, KeyError, Exception) as exc:  # noqa: BLE001
            if attempt == 0:
                continue  # retry once
            # Fallback template after two failures.
            return {
                "pass_id":     str(row["pass_id"]),
                "satellite":   str(row["satellite"]),
                "priority":    int(row["priority"]),
                "explanation": (
                    f"{row['satellite']} (priority {row['priority']}) pass "
                    f"{row['pass_id']} was dropped because {row.get('blocked_by', 'another satellite')} "
                    f"occupied the antenna at {row.get('candidate_stations', 'all candidate stations')}."
                ),
                "action":  action,
                "reason":  f"{reason} (LLM unavailable: {exc})",
            }

    # Unreachable, but satisfies type checker.
    return {}  # pragma: no cover


def _generate_summary(stats: dict[str, Any], *, host: str = _OLLAMA_HOST) -> str:
    """Ask Granite for an operator summary paragraph; fall back to a template."""
    prompt = _build_summary_prompt(stats)
    try:
        return _call_granite(prompt, host=host)
    except Exception as exc:  # noqa: BLE001
        return (
            f"Scheduling run completed. "
            f"{stats['scheduled']} of {stats['total']} passes were scheduled "
            f"({stats['sched_pct']:.1f}%). "
            f"{stats['dropped']} passes were dropped; "
            f"{stats['with_alt']} have an alternative contact available "
            f"(median delay {stats['median_delay']} min) "
            f"and {stats['without_alt']} have no alternative in the planning window. "
            f"All {stats['p1_total']} priority-1 passes were scheduled "
            f"({stats['p1_pct']:.1f}%). "
            f"(LLM summary unavailable: {exc})"
        )


# ---------------------------------------------------------------------------
# Selection / ranking
# ---------------------------------------------------------------------------

def select_top(dropped_df: pd.DataFrame, n: int = _TOP_N) -> pd.DataFrame:
    """Return the *n* most operationally significant dropped passes.

    Ranking: priority asc → has_alternative False first → delay_min desc.
    """
    df = dropped_df.copy()
    # Normalise has_alternative to bool.
    df["has_alternative"] = df["has_alternative"].astype(str).str.lower() == "true"
    # Sort key: (priority, alt_rank, -delay_min).
    df["_alt_rank"] = (~df["has_alternative"]).astype(int)  # False → 1 (more urgent)
    df["_delay_sort"] = df["delay_min"].fillna(0)
    df = df.sort_values(
        ["priority", "_alt_rank", "_delay_sort"],
        ascending=[True, True, False],
    ).head(n)
    df = df.drop(columns=["_alt_rank", "_delay_sort"])
    return df


# ---------------------------------------------------------------------------
# Aggregate stats
# ---------------------------------------------------------------------------

def _compute_stats(
    dropped_df: pd.DataFrame,
    schedule_df: pd.DataFrame,
) -> dict[str, Any]:
    """Compute aggregate scheduling statistics."""
    n_sched   = len(schedule_df)
    n_dropped = len(dropped_df)
    total     = n_sched + n_dropped

    p1_sched   = len(schedule_df[schedule_df["priority"] == 1])
    p1_dropped = len(dropped_df[dropped_df["priority"] == 1])
    p1_total   = p1_sched + p1_dropped

    has_alt    = dropped_df["has_alternative"].astype(str).str.lower() == "true"
    with_alt   = int(has_alt.sum())
    without_alt = int((~has_alt).sum())
    delays     = pd.to_numeric(dropped_df.loc[has_alt, "delay_min"], errors="coerce")
    median_delay = round(float(delays.median()), 1) if not delays.empty else None

    return {
        "total":        total,
        "scheduled":    n_sched,
        "dropped":      n_dropped,
        "sched_pct":    n_sched / total * 100 if total else 0.0,
        "drop_pct":     n_dropped / total * 100 if total else 0.0,
        "p1_total":     p1_total,
        "p1_scheduled": p1_sched,
        "p1_pct":       p1_sched / p1_total * 100 if p1_total else 0.0,
        "with_alt":     with_alt,
        "without_alt":  without_alt,
        "median_delay": median_delay,
    }


# ---------------------------------------------------------------------------
# Cache check
# ---------------------------------------------------------------------------

def _is_cache_valid(triage_path: Path, dropped_path: Path) -> bool:
    """Return True when triage.json exists and is newer than dropped.csv."""
    if not triage_path.exists():
        return False
    return triage_path.stat().st_mtime >= dropped_path.stat().st_mtime


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_triage(
    dropped_path: Path,
    schedule_path: Path,
    triage_path: Path,
    *,
    force: bool = False,
    ollama_host: str = _OLLAMA_HOST,
) -> dict[str, Any]:
    """Run the full triage pipeline and write data/triage.json.

    Parameters
    ----------
    dropped_path:   Path to data/dropped.csv.
    schedule_path:  Path to data/schedule.csv.
    triage_path:    Output path for data/triage.json.
    force:          Regenerate even if the cache is valid.
    ollama_host:    Ollama base URL.

    Returns
    -------
    The triage dict (same structure as written to triage.json).
    """
    if not force and _is_cache_valid(triage_path, dropped_path):
        print("Cache is valid — loading existing triage.json (use --force to regenerate).")
        with triage_path.open() as fh:
            return json.load(fh)

    dropped_df  = pd.read_csv(dropped_path)
    schedule_df = pd.read_csv(schedule_path)

    stats   = _compute_stats(dropped_df, schedule_df)
    top15   = select_top(dropped_df)

    print(f"Generating summary paragraph via Granite ({_MODEL}) …")
    summary = _generate_summary(stats, host=ollama_host)

    items: list[dict[str, Any]] = []
    for i, (_, row) in enumerate(top15.iterrows(), 1):
        print(f"  Triaging pass {i}/{len(top15)}: {row['pass_id']} …")
        items.append(_triage_row(row, host=ollama_host))

    result: dict[str, Any] = {
        "summary":      summary,
        "generated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model":        _MODEL,
        "items":        items,
    }

    triage_path.parent.mkdir(parents=True, exist_ok=True)
    with triage_path.open("w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    print(f"Wrote {triage_path}")

    return result


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Triage dropped satellite passes with Granite.")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate triage.json even if the cache is valid.")
    parser.add_argument("--host", default=_OLLAMA_HOST,
                        help=f"Ollama base URL (default: {_OLLAMA_HOST})")
    args = parser.parse_args()

    project_root  = Path(__file__).resolve().parent.parent
    dropped_path  = project_root / "data" / "dropped.csv"
    schedule_path = project_root / "data" / "schedule.csv"
    triage_path   = project_root / "data" / "triage.json"

    result = run_triage(
        dropped_path,
        schedule_path,
        triage_path,
        force=args.force,
        ollama_host=args.host,
    )

    print()
    print("=" * 72)
    print("OPERATOR SUMMARY")
    print("=" * 72)
    print(result["summary"])
    print()
    print("TOP TRIAGE ITEMS (first 3)")
    print("-" * 72)
    for item in result["items"][:3]:
        print(f"[{item['action']}] {item['pass_id']}  ({item['satellite']}, P{item['priority']})")
        print(f"  {item['explanation']}")
        print(f"  → {item['reason']}")
        print()
