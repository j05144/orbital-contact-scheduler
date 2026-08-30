# Design Notes

## Results

### Configuration

| Parameter | Value |
|---|---|
| Ground stations | 3 — Svalbard, Kiruna, Hawaii |
| Satellites | ~30 polar-orbiting weather satellites plus ISS |
| Planning window | 48 hours |
| Minimum elevation | 10 degrees |
| Antenna turnaround | 15 minutes |

### Scheduling outcome

| Metric | Count | Share |
|---|---|---|
| Total passes | 1 267 | 100% |
| Scheduled | 280 | 22.1% |
| Dropped | 987 | 77.9% |
| Priority-1 passes scheduled | 63 / 65 | 96.9% |

### Dropped-pass breakdown

Of the 987 dropped passes:

- **688** have an alternative contact later in the window — median wait **464.9 minutes**.
- **299** have no alternative at all within the 48-hour window.

The two dropped priority-1 passes (METOP-B-031 and METOP-B-032) were
blocked by NOAA 20 (JPSS-1), also priority 1. Both fall near the end of
the 48-hour window with no alternative inside the planning horizon.
They appear as OVERRIDE items at the top of the triage output.

### Granite triage

The top 15 dropped passes are surfaced for operator review, ranked by:

1. Priority ascending (lower number = higher urgency)
2. Absence of alternative first (no fallback = more urgent)
3. Delay descending (longer wait = more impact)

**Action is computed deterministically in Python** (`decide_action` in `src/triage.py`) — Granite writes only the one-sentence explanation of why the pass was dropped. The model is `granite3.3:2b` running locally via Ollama; the project requires no API key and no cloud account.
