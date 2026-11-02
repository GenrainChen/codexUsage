# Persistent usage cache implementation plan

**Goal:** Restore parsed history across restarts while producing the same usage and output totals as a full scan.

**Architecture:** SQLite stores content-free per-file events and parser checkpoints, partitioned by normalized Codex directory. The reader restores this state, validates source files, parses only changes, then runs the existing global deduplication and aggregation. Prices remain outside the cache.

**Tech stack:** Python 3.11+ standard library, sqlite3, unittest, existing project `.venv`.

**Spec:** User-approved design in this task: `.local/usage-cache.sqlite3`, startup cached preview, incremental reads, transactional checkpoints, safe rebuild, diagnostics, and graceful fallback.

## Constraints

- Preserve pre-existing uncommitted work; work in the current checkout.
- Never write Codex source logs or authentication files.
- Do not store messages, patches, file contents, or credentials.
- Preserve cumulative counters, FAST tiers, fork deduplication, output metadata, and fixed UTC+8 reporting.
- No new dependencies; use `.venv/Scripts/python.exe` for all checks.
- No commit, PR, or deployment is part of this request.

## Tasks

1. **SQLite store** (`usage_note/cache.py`, `tests/test_cache_store.py`). Load per-source records and a revision. Save changed/deleted records in one transaction only if the revision and parser version still match. Handle corrupt, locked, and unavailable storage with warnings and safe fallback. Tests cover persistence, isolation, stale writers, versions, and rollback.
2. **Reader and metadata codec** (`usage_note/reader.py`, `usage_note/cache_codec.py`, `tests/test_persistent_reader.py`). Restore parser state and unmerged events. Validate fingerprints; read to a fixed byte boundary; stop before incomplete lines. Stage file changes and publish only a complete scan. Reuse the existing aggregation. Tests compare warm and cold results for restarts, appends, resets, copies, moves, rewrites, deletion, and failures.
3. **App and UI** (`app.py`, `usage_note/ui.py`, cache-specific UI/CLI tests). Enable the project cache by default, add `--rebuild-cache` and `--no-cache`, show an interim historical report without ending the worker's busy state, expose rebuild and diagnostics, and mark failed scans stale.
4. **Verification and docs** (`README.md`). Run targeted red/green tests and the full suite, independently review cache correctness, and compare cold/warm scan metrics. Document cache location, limitations, and recovery.

## Fixed interfaces

```python
UsageCache(path, codex_home, *, parser_version)
cache.load()  # -> (dict[str, dict], revision: int)
cache.save(changed, removed, expected_revision)  # -> new_revision | None
UsageReader(codex_home, *, tz=BEIJING_TZ, cache_path=None)
reader.load_cached()  # -> historical report | None
reader.scan(rebuild=False)  # -> report
```

Reports add `cache_status` (`cached`, `updated`, `stale`), `cache_path`, and `cache_stats` with reused/appended/reread files, bytes read, and elapsed milliseconds.

## Verification commands

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_cache*.py' -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_persistent_reader.py' -v
.\.venv\Scripts\python.exe -m unittest discover -s tests
git diff --check
```

## Progress

- [x] Store persistence and recovery
- [x] Reader restoration and incremental correctness
- [x] UI and CLI integration
- [x] Independent review, final checks, documentation

## Results

- Full suite: 174 tests, 156 passed and 18 opt-in GUI tests skipped.
- Independent review found a cache-write-failure fallback defect; fixed with a regression that verifies memory reuse and eventual persistence.
- Actual local scan: 549 files, approximately 2.3 GiB; initial scan 22.773 seconds, restored reader 3.236 seconds (545 reused, 4 appended, 530,710 source bytes read). The source logs remained active during measurement.
- Initial SQLite cache size: 26.402 MiB. Measurements are local observations, not a general performance guarantee.
- No source logs, account credentials, global dependencies, or existing unrelated changes were modified.
