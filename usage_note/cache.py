"""SQLite persistence for explicit, JSON-compatible usage reader states."""

import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4


_SCHEMA_VERSION = 1
_CORRUPTION_MARKERS = ("file is not a database", "database disk image is malformed")


class _UnsupportedSchema(Exception):
    pass


class _NeedsWrite(Exception):
    pass


class UsageCache:
    def __init__(self, path: Path, codex_home: Path, *, parser_version: int):
        self.path = Path(path)
        self.source = os.path.normcase(str(Path(codex_home).resolve()))
        self.parser_version = parser_version
        self.warnings: list[str] = []

    def _connect(self, *, readonly=False):
        if readonly:
            connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro",
                                         uri=True, timeout=0.05)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=0.05)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
        except sqlite3.Error:
            connection.close()
            raise
        return connection

    @staticmethod
    def _schema(connection):
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > _SCHEMA_VERSION:
            raise _UnsupportedSchema("缓存数据库由较新版本创建")
        connection.execute("""CREATE TABLE IF NOT EXISTS sources (
            source TEXT PRIMARY KEY,
            parser_version INTEGER NOT NULL,
            revision INTEGER NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS entries (
            source TEXT NOT NULL,
            path TEXT NOT NULL,
            state TEXT NOT NULL,
            PRIMARY KEY (source, path),
            FOREIGN KEY (source) REFERENCES sources(source) ON DELETE CASCADE
        )""")
        if version == 0:
            connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    def _has_sidecars(self):
        return any(self.path.with_name(self.path.name + suffix).exists()
                   for suffix in ("-journal", "-wal", "-shm"))

    def _recover(self, *, had_sidecar=False):
        """Quarantine only an unchanged, inactive file under a recovery marker."""
        if had_sidecar:
            return False
        marker = self.path.with_name(self.path.name + ".recover.lock")
        try:
            descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError:
            return False
        try:
            os.close(descriptor)
            if not self.path.exists():
                return True
            original = self.path.stat()
            if self._has_sidecars():
                return False
            # Another process may have completed recovery while we acquired the marker.
            try:
                connection = self._connect(readonly=True)
                try:
                    result = connection.execute("PRAGMA quick_check").fetchone()
                    if result == ("ok",):
                        return True
                finally:
                    connection.close()
            except sqlite3.DatabaseError as error:
                if not self._is_corrupt(error):
                    return False
            current = self.path.stat()
            if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (
                    original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns):
                return True
            if self._has_sidecars():
                return False
            quarantine = self.path.with_name(self.path.name + ".corrupt-" + uuid4().hex)
            os.replace(self.path, quarantine)
            self.warnings.append(f"用量缓存损坏，已隔离为 {quarantine.name} 并重建")
            return True
        except OSError as error:
            self.warnings.append(f"用量缓存损坏，但无法安全隔离：{error}")
            return False
        finally:
            try:
                marker.unlink()
            except OSError:
                pass

    @staticmethod
    def _is_corrupt(error):
        return any(marker in str(error).lower() for marker in _CORRUPTION_MARKERS)

    def _run_write(self, action, fallback):
        for attempt in range(2):
            connection = None
            had_sidecar = self._has_sidecars()
            try:
                connection = self._connect()
                connection.execute("BEGIN IMMEDIATE")
                self._schema(connection)
                result = action(connection)
                connection.commit()
                return result
            except (OSError, sqlite3.Error, _UnsupportedSchema, ValueError, TypeError) as error:
                if connection is not None:
                    try:
                        connection.rollback()
                    except sqlite3.Error:
                        pass
                    connection.close()
                    connection = None
                if isinstance(error, sqlite3.DatabaseError) and self._is_corrupt(error) and attempt == 0:
                    if self._recover(had_sidecar=had_sidecar):
                        continue
                self.warnings.append(f"用量缓存不可用：{error}")
                return fallback
            finally:
                if connection is not None:
                    connection.close()
        return fallback

    def load(self) -> tuple[dict[str, dict], int]:
        def read():
            connection = self._connect(readonly=True)
            try:
                connection.execute("BEGIN")
                schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
                if schema_version > _SCHEMA_VERSION:
                    raise _UnsupportedSchema("缓存数据库由较新版本创建")
                if schema_version < _SCHEMA_VERSION:
                    raise _NeedsWrite
                row = connection.execute(
                    "SELECT parser_version, revision FROM sources WHERE source = ?",
                    (self.source,),
                ).fetchone()
                if row is None or row[0] < self.parser_version:
                    raise _NeedsWrite
                if row[0] > self.parser_version:
                    raise _UnsupportedSchema("该来源的缓存由较新解析器创建")
                revision = row[1]
                states, damaged = {}, []
                for path, payload in connection.execute(
                        "SELECT path, state FROM entries WHERE source = ?", (self.source,)):
                    try:
                        state = json.loads(payload)
                        if not isinstance(state, dict):
                            raise ValueError("缓存记录必须是 JSON 对象")
                        states[path] = state
                    except (ValueError, TypeError):
                        damaged.append(path)
                return states, revision, damaged
            finally:
                connection.close()

        def initialize(connection):
            row = connection.execute(
                "SELECT parser_version, revision FROM sources WHERE source = ?", (self.source,)
            ).fetchone()
            if row is None:
                connection.execute("INSERT INTO sources VALUES (?, ?, 0)",
                                   (self.source, self.parser_version))
                return 0
            version, revision = row
            if version > self.parser_version:
                raise _UnsupportedSchema("该来源的缓存由较新解析器创建")
            if version < self.parser_version:
                connection.execute("DELETE FROM entries WHERE source = ?", (self.source,))
                revision += 1
                connection.execute(
                    "UPDATE sources SET parser_version = ?, revision = ? WHERE source = ?",
                    (self.parser_version, revision, self.source),
                )
            return revision

        for attempt in range(2):
            had_sidecar = self._has_sidecars()
            try:
                states, revision, damaged = read()
                if not damaged:
                    return states, revision
                self.warnings.append(f"用量缓存有 {len(damaged)} 条损坏记录，已跳过")

                def repair(connection):
                    row = connection.execute(
                        "SELECT parser_version, revision FROM sources WHERE source = ?",
                        (self.source,),
                    ).fetchone()
                    if row != (self.parser_version, revision):
                        raise ValueError("缓存修订号已变化")
                    connection.executemany("DELETE FROM entries WHERE source = ? AND path = ?",
                                           ((self.source, path) for path in damaged))
                    connection.execute("UPDATE sources SET revision = ? WHERE source = ?",
                                       (revision + 1, self.source))
                    return revision + 1

                repaired = self._run_write(repair, None)
                return states, repaired if repaired is not None else revision
            except _NeedsWrite:
                if self._run_write(initialize, None) is None:
                    return {}, 0
            except (OSError, sqlite3.Error, _UnsupportedSchema, ValueError, TypeError) as error:
                if isinstance(error, sqlite3.DatabaseError) and self._is_corrupt(error) and attempt == 0:
                    if self._recover(had_sidecar=had_sidecar):
                        continue
                if isinstance(error, sqlite3.OperationalError) and not self.path.exists() and attempt == 0:
                    if self._run_write(initialize, None) is not None:
                        continue
                self.warnings.append(f"用量缓存不可用：{error}")
                return {}, 0
        return {}, 0

    def save(self, changed: dict[str, dict], removed: set[str], expected_revision: int) -> int | None:
        try:
            payloads = [(path, json.dumps(state, ensure_ascii=False, allow_nan=False,
                                           separators=(",", ":")))
                        for path, state in changed.items()]
            if any(not isinstance(path, str) or not isinstance(changed[path], dict)
                   for path, _ in payloads):
                raise ValueError("缓存记录必须是路径到 JSON 对象的映射")
        except (TypeError, ValueError) as error:
            self.warnings.append(f"用量缓存记录无法序列化：{error}")
            return None

        def write(connection):
            row = connection.execute(
                "SELECT parser_version, revision FROM sources WHERE source = ?", (self.source,)
            ).fetchone()
            if row is None:
                if expected_revision != 0:
                    raise ValueError("缓存修订号已变化")
                connection.execute("INSERT INTO sources VALUES (?, ?, 0)",
                                   (self.source, self.parser_version))
                revision = 0
            else:
                version, revision = row
                if version != self.parser_version or revision != expected_revision:
                    raise ValueError("缓存解析器版本或修订号已变化")
            if not payloads and not removed:
                return revision
            connection.executemany("DELETE FROM entries WHERE source = ? AND path = ?",
                                   ((self.source, path) for path in removed))
            connection.executemany("""INSERT INTO entries (source, path, state) VALUES (?, ?, ?)
                ON CONFLICT(source, path) DO UPDATE SET state = excluded.state""",
                ((self.source, path, state) for path, state in payloads))
            revision += 1
            connection.execute("UPDATE sources SET revision = ? WHERE source = ?",
                               (revision, self.source))
            return revision

        return self._run_write(write, None)
