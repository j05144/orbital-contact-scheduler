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
| Total passes | 1 281 | 100% |
| Scheduled | 289 | 22.6% |
| Dropped | 992 | 77.4% |
| Priority-1 passes scheduled | 60 / 60 | 100% |

### Dropped-pass breakdown

Of the 992 dropped passes:

- **694** have an alternative contact later in the window — median wait **383.6 minutes**.
- **298** have no alternative at all within the 48-hour window.

### Granite triage

The top 15 dropped passes are surfaced for operator review, ranked by:

1. Priority ascending (lower number = higher urgency)
2. Absence of alternative first (no fallback = more urgent)
3. Delay descending (longer wait = more impact)

**Action is computed deterministically in Python** (`decide_action` in `src/triage.py`) — Granite writes only the one-sentence explanation of why the pass was dropped. The model is `granite3.3:2b` running locally via Ollama; the project requires no API key and no cloud account.
