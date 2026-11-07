import concurrent.futures
import sqlite3
import pytest

from storage.sqlite import connect_sqlite


def test_sqlite_wal_and_busy_timeout_configured(tmp_path):
    """Verify that connect_sqlite configures WAL mode and busy timeout."""
    db_file = tmp_path / "test_config.db"
    conn = connect_sqlite(db_file, enable_foreign_keys=True, timeout=5.0)

    with conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]

    assert journal_mode.lower() == "wal"
    assert busy_timeout == 5000
    assert foreign_keys == 1


def test_sqlite_foreign_key_enforcement(tmp_path):
    """Verify foreign key enforcement when enable_foreign_keys=True."""
    db_file = tmp_path / "test_fk.db"

    # Initialize tables with foreign key relationship
    with connect_sqlite(db_file, enable_foreign_keys=True) as conn:
        conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute(
            "CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER, "
            "FOREIGN KEY(parent_id) REFERENCES parent(id) ON DELETE CASCADE)"
        )
        conn.execute("INSERT INTO parent VALUES (1, 'Alice')")
        conn.execute("INSERT INTO child VALUES (10, 1)")

    # Inserting invalid parent_id must fail
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        with connect_sqlite(db_file, enable_foreign_keys=True) as conn:
            conn.execute("INSERT INTO child VALUES (20, 999)")


def test_people_db_foreign_keys_enforced(tmp_path, monkeypatch):
    """BUG-P4-01 Regression: people_registry.db now enforces foreign keys."""
    from knowledge import people

    test_db = tmp_path / "people_test_fk.db"
    monkeypatch.setattr(people, "PEOPLE_DB", test_db)
    people.initialize_people_registry()

    # Attempting to insert a face_embedding for a non-existent person must raise IntegrityError
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        with people._connect() as conn:
            conn.execute(
                "INSERT INTO face_embeddings (embedding_id, person_id, embedding_json, created_at) "
                "VALUES ('emb1', 'non_existent_person', '[]', '2026-11-07T00:00:00Z')"
            )


def test_sqlite_concurrent_readers_and_writers(tmp_path):
    """CRIT-P4-02: Concurrent readers and writers do not block with database is locked."""
    db_file = tmp_path / "test_concur.db"

    # Setup database
    with connect_sqlite(db_file) as conn:
        conn.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, val TEXT)")
        for i in range(20):
            conn.execute("INSERT INTO records VALUES (?, ?)", (i, f"val_{i}"))

    num_writers = 10
    num_readers = 10

    def write_worker(idx):
        with connect_sqlite(db_file, timeout=10.0) as conn:
            conn.execute("INSERT INTO records VALUES (?, ?)", (100 + idx, f"worker_{idx}"))
        return True

    def read_worker(_):
        with connect_sqlite(db_file, timeout=10.0) as conn:
            rows = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            assert rows >= 20
        return True

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_writers + num_readers) as executor:
        writer_futs = [executor.submit(write_worker, i) for i in range(num_writers)]
        reader_futs = [executor.submit(read_worker, i) for i in range(num_readers)]

        all_futs = writer_futs + reader_futs
        results = [f.result() for f in concurrent.futures.as_completed(all_futs)]

    assert len(results) == num_writers + num_readers
    assert all(results)

    # Verify final count
    with connect_sqlite(db_file) as conn:
        total = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    assert total == 20 + num_writers


def test_sqlite_managed_connection_closes_cleanly(tmp_path):
    """Verify that ManagedConnection closes cleanly on exit and avoids leaked handles."""
    db_file = tmp_path / "test_managed.db"

    conn = connect_sqlite(db_file)
    with conn as c:
        c.execute("CREATE TABLE t (x INT)")
        c.execute("INSERT INTO t VALUES (42)")

    # Outside the block, executing must raise ProgrammingError because it's closed
    with pytest.raises(sqlite3.ProgrammingError, match="Cannot operate on a closed database"):
        conn.execute("SELECT * FROM t")
