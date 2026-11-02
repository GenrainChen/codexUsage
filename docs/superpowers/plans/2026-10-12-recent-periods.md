# Recent Periods Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement this cohesive task and review the final change. Steps use checkbox syntax for tracking.

**Goal:** Let the user switch the existing natural day/week/month views to rolling 24-hour/7-day/30-day views.

**Architecture:** Preserve the calendar APIs and add keyword-only rolling options. Publish precise content-free timestamp groups from the already deduplicated reader events, then use the same exact interval for totals, output and chart buckets. Add a remembered natural/recent selector to the existing Tk window.

**Tech Stack:** Python 3.13 project `.venv`, standard library, Tkinter, unittest, existing SQLite checkpoints.

**Spec:** User request in this chat, confirmed that recent month means the past 30 days. Recent day/week mean elapsed 24 hours/7 days. The feature adds a selectable mode and retains natural calendar navigation.

## Global Constraints

- Use `.venv/Scripts/python.exe`; install no dependencies.
- Preserve existing dirty changes; do not commit, branch, reset or modify unrelated features.
- Work in the current checkout so this local desktop application receives the change.
- Use Beijing UTC+8 for display; compute fixed elapsed durations using aware instants.
- Preserve existing calendar behavior, deduplication, FAST identity, exclusions and cache privacy.
- Reuse precise event timestamps already stored by the reader and cache; no schema migration.
- Run tests with TEMP and TMP set to `D:/genrain/codexUsage/.local/test-tmp` to avoid sandbox temporary-directory permissions.
- Keep CLI report shape compatible by removing the new exact views before JSON export.

### Task 1: Rolling interval selection and consistent statistics

**Files:** Modify `usage_note/reader.py`, `usage_note/output.py`, `usage_note/periods.py`, `usage_note/charts.py`, `usage_note/ui.py`, `app.py`, `README.md`; add `tests/test_recent_periods.py`, `tests/test_recent_ui.py` or extend appropriate existing tests.

**Interfaces:**

```python
def recent_bounds(mode, *, now=None, tz=BEIJING_TZ):
    # Return [now - duration, now), aware datetimes. Durations: 1, 7, 30 days.
    ...

def period_models(data, anchor, mode, *, recent=False, now=None, tz=BEIJING_TZ): ...
def period_buckets(data, anchor, mode, *, now=None, tz=BEIJING_TZ, recent=False): ...
def period_output(data, selected, mode, *, recent=False, now=None, tz=BEIJING_TZ): ...
```

`data['instants']` maps exact ISO timestamps to existing model rows. Build it in the `_report()` selected-event loop using `_add_event`, after `_covered_events` and exclusions. `data['output_instants']` maps exact ISO timestamps to `{added, deleted, files: [SHA256], turns}`; daily and exact views share chronological earliest-key-wins dedup. Neither exact view contains patches, event identities, log paths, working directories or conversations.

- [x] Write failing tests for exact rolling bounds and aggregates. A literal test example:

```python
now = datetime(2026, 10, 12, 15, 30, tzinfo=BEIJING_TZ)
data = {'instants': {
    '2026-10-11T15:29:59+08:00': {'m': {'input': 1000}},
    '2026-10-11T15:30:00+08:00': {'m': {'input': 10}},
    '2026-10-12T15:29:59+08:00': {'m': {'input': 20}},
    '2026-10-12T15:30:00+08:00': {'m': {'input': 1000}},
}}
assert period_models(data, now.date(), 'day', recent=True, now=now)['m']['input'] == 30
```

Include a 30-day leap/month boundary, same-hour events on opposite sides of the cutoff, FAST metadata, output file union and event dedup, and cached/restarted precise-view equivalence. Assert that each rolling chart's summed counts equal its summary.

- [x] Run new tests with `.venv/Scripts/python.exe -m unittest tests.test_recent_periods -v` and confirm the expected missing-feature failures before production changes.
- [x] Implement precise report views and rolling reducers. Keep intervals half-open. Align chart bins to real hours for day/week and Beijing dates for month; clip the first and last bins to the rolling interval so events outside the interval cannot leak in. Use the caller's single `now` for all components.
- [x] Add the natural/recent selector and persist `range_mode` (`calendar` default, `recent` opt-in). Recent unit labels are `最近一天`, `最近一周`, `最近一个月`. Recent mode displays the actual timestamp range and disables calendar date navigation; the current-time button recalculates the interval. Switching back restores normal calendar controls. Render hourly charts for recent day/week and daily charts for recent month, with correct ticks and clipped hover intervals. Describe the last recent bucket as ending at the current time rather than as a future interval.
- [x] Add real Tk tests with synthetic data for switch/refresh behavior, totals/output/chart agreement, persisted selection and all controls fitting 390x650. Existing GUI tests require `CODEX_NOTE_GUI_TESTS=1`; these are temporary test windows only.
- [x] Strip `instants` and `output_instants` from CLI reports before existing date filters. Update README with how to select the mode, fixed durations and timestamp attribution.
- [x] Run new tests and the full suite; resolve failures caused by this change. Request an independent review of this task, address findings, and rerun covering tests.

## Verification record

- Initial suite: unsandboxed fixture-only baseline passed all 174 tests (18 GUI skipped). Sandbox TEMP permissions first prevented fixture writes; project-local TEMP resolved that. Two shutdown tests require an unsandboxed launcher because sandbox Python emits a real-location warning to stderr.
- Existing combined Tk suite hit `Tcl_AsyncDelete` before implementation; the relevant minimum-width/navigation test passes in an isolated subprocess. Verify UI cases in isolated subprocesses rather than changing unrelated Tk lifecycle behavior.
- Review must compare against the pre-task working-copy snapshot, since HEAD includes neither this task nor earlier uncommitted features.
