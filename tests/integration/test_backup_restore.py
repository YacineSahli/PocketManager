"""Integration tests for backup and restore with data integrity verification.

This test module spins up a **dedicated** PocketBase instance, populates it
with fake data, creates a backup, mutates the data, restores the backup, and
verifies that the original data is fully intact after restore.

The test does **not** touch any existing / production PocketBase instances.

NOTE: The ``PocketBaseRunner`` from conftest.py starts PocketBase with piped
stdout/stderr.  Many API calls generate log output that can fill the pipe
buffer (typically 64 KB on Linux), causing the PocketBase process to block on
write and become unresponsive.  We work around this by spawning background
drain threads for the process pipes.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest
import requests


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drain_pipes(proc: subprocess.Popen) -> list[threading.Thread]:
    """Spawn daemon threads that continuously drain stdout/stderr of *proc*.

    This prevents the OS pipe buffer from filling up and blocking the
    PocketBase process.  Returns the drain threads (already started).
    """
    threads: list[threading.Thread] = []

    def _drain(pipe):
        try:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    break
        except Exception:
            pass

    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            t = threading.Thread(target=_drain, args=(pipe,), daemon=True)
            t.start()
            threads.append(t)

    return threads


def _wait_healthy(port: int, timeout: int = 30) -> bool:
    """Poll the health endpoint until it returns 200 or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(f"http://localhost:{port}/api/health", timeout=2)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _create_collection(base_url: str, token: str, name: str, schema: list[dict]) -> dict:
    """Create a PocketBase collection with the given schema fields."""
    resp = requests.post(
        f"{base_url}/api/collections",
        json={"name": name, "type": "base", "fields": schema},
        headers={"Authorization": token},
        timeout=15,
    )
    assert resp.status_code == 200, f"Failed to create collection '{name}': {resp.status_code} {resp.text}"
    return resp.json()


def _create_record(base_url: str, token: str, collection: str, record: dict) -> dict:
    """Create a single record in a collection."""
    resp = requests.post(
        f"{base_url}/api/collections/{collection}/records",
        json=record,
        headers={"Authorization": token},
        timeout=15,
    )
    assert resp.status_code == 200, f"Failed to create record in '{collection}': {resp.status_code} {resp.text}"
    return resp.json()


def _list_records(base_url: str, token: str, collection: str) -> list[dict]:
    """List all records in a collection (auto-paginates)."""
    all_items: list[dict] = []
    page = 1

    while True:
        resp = requests.get(
            f"{base_url}/api/collections/{collection}/records",
            params={"page": page, "perPage": 200},
            headers={"Authorization": token},
            timeout=15,
        )
        assert resp.status_code == 200, f"Failed to list records: {resp.status_code} {resp.text}"
        data = resp.json()
        items = data.get("items", [])
        all_items.extend(items)
        total_pages = data.get("totalPages", 1)
        if page >= total_pages:
            break
        page += 1

    return all_items


def _update_record(
    base_url: str, token: str, collection: str, record_id: str, updates: dict
) -> dict:
    """Update a record. Returns the API response."""
    resp = requests.patch(
        f"{base_url}/api/collections/{collection}/records/{record_id}",
        json=updates,
        headers={"Authorization": token},
        timeout=15,
    )
    assert resp.status_code == 200, f"Failed to update record: {resp.status_code} {resp.text}"
    return resp.json()


def _delete_record(base_url: str, token: str, collection: str, record_id: str) -> None:
    """Delete a record by ID."""
    resp = requests.delete(
        f"{base_url}/api/collections/{collection}/records/{record_id}",
        headers={"Authorization": token},
        timeout=15,
    )
    assert resp.status_code == 204, f"Failed to delete record: {resp.status_code} {resp.text}"


def _restart_pocketbase(pb_runner) -> None:
    """Restart the PocketBase subprocess after a restore.

    PocketBase restores the backup to disk but keeps serving old data from
    memory. When running as a bare subprocess (not via systemd) we need to
    stop and restart it to pick up the restored data.
    """
    # Give PocketBase a moment to finish writing the restored data to disk
    time.sleep(2)

    # Stop the old process (it may still be running)
    pb_runner.stop()

    # Small delay to ensure the port is released
    time.sleep(1)

    # Start a fresh process (reads restored pb_data from disk)
    pb_runner.start(timeout=30)

    # Re-drain the new process's pipes
    if pb_runner.process is not None:
        _drain_pipes(pb_runner.process)


# ---------------------------------------------------------------------------
# Test data factories
# ---------------------------------------------------------------------------

# Schema for a "products" collection
PRODUCTS_SCHEMA = [
    {"name": "name", "type": "text", "required": True},
    {"name": "price", "type": "number", "required": True},
    {"name": "description", "type": "text", "required": False},
    {"name": "in_stock", "type": "bool", "required": False},
]

# Schema for a "tasks" collection
TASKS_SCHEMA = [
    {"name": "title", "type": "text", "required": True},
    {"name": "done", "type": "bool", "required": False},
    {"name": "priority", "type": "number", "required": False},
]

PRODUCTS_DATA = [
    {"name": "Widget Alpha", "price": 29.99, "description": "A fine widget", "in_stock": True},
    {"name": "Gadget Beta", "price": 49.95, "description": "Premium gadget", "in_stock": True},
    {"name": "Doohickey Gamma", "price": 12.50, "description": "Budget doohickey", "in_stock": False},
    {"name": "Thingamajig Delta", "price": 99.99, "description": "Deluxe thingamajig", "in_stock": True},
    {"name": "Whatchamacallit Epsilon", "price": 5.00, "description": None, "in_stock": True},
]

TASKS_DATA = [
    {"title": "Set up CI pipeline", "done": False, "priority": 1},
    {"title": "Write documentation", "done": False, "priority": 2},
    {"title": "Fix login bug", "done": True, "priority": 1},
    {"title": "Add dark mode", "done": False, "priority": 3},
    {"title": "Deploy to staging", "done": False, "priority": 2},
    {"title": "Review pull request", "done": True, "priority": 2},
    {"title": "Update dependencies", "done": False, "priority": 3},
]


# ---------------------------------------------------------------------------
# Fixture: PocketBase runner with drained pipes
# ---------------------------------------------------------------------------


@pytest.fixture()
def drained_pb(pb_runner):
    """Return a pb_runner whose stdout/stderr pipes are continuously drained.

    This prevents the OS pipe buffer from filling up and blocking the
    PocketBase process (which would make it unresponsive to HTTP requests).
    """
    if pb_runner.process is not None:
        _drain_pipes(pb_runner.process)
    return pb_runner


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBackupRestoreDataIntegrity:
    """End-to-end backup/restore test verifying data survives a round-trip."""

    def test_backup_restore_preserves_data(self, drained_pb, isolated_env):
        """Full round-trip: seed data -> backup -> mutate -> restore -> verify.

        Steps
        -----
        1. Start a fresh PocketBase instance (handled by drained_pb fixture).
        2. Create a superuser and authenticate.
        3. Create two collections (products, tasks) with realistic schemas.
        4. Insert fake records into both collections.
        5. Snapshot all data (the "expected" state).
        6. Create a backup via the PocketManager backup module.
        7. Mutate data: update some records, delete others, insert new ones.
        8. Verify the data is different from the snapshot.
        9. Restore the backup.
        10. Verify the data matches the original snapshot exactly.
        """
        from pocketmanager.core import backup as backup_mod

        base_url = drained_pb.url

        # ------------------------------------------------------------------
        # 1-2. Create superuser & authenticate
        # ------------------------------------------------------------------
        email = "backup-test@example.com"
        password = "backup-test-password-12345"

        result = drained_pb.create_superuser(email, password)
        assert result is True, "Superuser creation failed"

        token = backup_mod.authenticate(base_url, email, password)
        assert token is not None, "Authentication failed"

        # ------------------------------------------------------------------
        # 3. Create collections
        # ------------------------------------------------------------------
        _create_collection(base_url, token, "products", PRODUCTS_SCHEMA)
        _create_collection(base_url, token, "tasks", TASKS_SCHEMA)

        # ------------------------------------------------------------------
        # 4. Insert fake records
        # ------------------------------------------------------------------
        created_products = []
        for product in PRODUCTS_DATA:
            record = _create_record(base_url, token, "products", product)
            created_products.append(record)

        created_tasks = []
        for task in TASKS_DATA:
            record = _create_record(base_url, token, "tasks", task)
            created_tasks.append(record)

        # ------------------------------------------------------------------
        # 5. Snapshot the data (this is our "expected" post-restore state)
        # ------------------------------------------------------------------
        products_before = _list_records(base_url, token, "products")
        tasks_before = _list_records(base_url, token, "tasks")

        # Build a lookup by ID for precise comparison later
        products_by_id_before = {r["id"]: r for r in products_before}
        tasks_by_id_before = {r["id"]: r for r in tasks_before}

        assert len(products_by_id_before) == len(PRODUCTS_DATA), (
            f"Expected {len(PRODUCTS_DATA)} products, got {len(products_by_id_before)}"
        )
        assert len(tasks_by_id_before) == len(TASKS_DATA), (
            f"Expected {len(TASKS_DATA)} tasks, got {len(tasks_by_id_before)}"
        )

        # ------------------------------------------------------------------
        # 6. Create backup (auto-generate name)
        # ------------------------------------------------------------------
        backup_ok = backup_mod.create_backup(base_url, auth_token=token)
        assert backup_ok is True, "Backup creation failed"

        backups = backup_mod.list_backups(base_url, auth_token=token)
        assert len(backups) >= 1, "Expected at least one backup"

        # Use the most recent backup
        backups.sort(key=lambda b: b.get("modified", ""), reverse=True)
        backup_key = backups[0]["key"]

        # ------------------------------------------------------------------
        # 7. Mutate data (to prove restore actually reverts changes)
        # ------------------------------------------------------------------

        # 7a. Update a product - change price
        product_to_mutate = created_products[0]
        _update_record(
            base_url, token, "products", product_to_mutate["id"],
            {"price": 0.01, "description": "MUTATED"},
        )

        # 7b. Update a task - mark as done
        task_to_mutate = created_tasks[0]
        _update_record(
            base_url, token, "tasks", task_to_mutate["id"],
            {"done": True, "priority": 99},
        )

        # 7c. Delete a product
        product_to_delete = created_products[2]
        _delete_record(base_url, token, "products", product_to_delete["id"])

        # 7d. Delete a task
        task_to_delete = created_tasks[3]
        _delete_record(base_url, token, "tasks", task_to_delete["id"])

        # 7e. Insert a new product (should disappear after restore)
        _create_record(
            base_url, token, "products",
            {"name": "IMPOSTER PRODUCT", "price": 999.99, "description": "Should vanish"},
        )

        # 7f. Insert a new task (should disappear after restore)
        _create_record(
            base_url, token, "tasks",
            {"title": "IMPOSTER TASK", "done": False, "priority": 0},
        )

        # ------------------------------------------------------------------
        # 8. Verify data IS different from snapshot
        # ------------------------------------------------------------------
        products_after_mutation = _list_records(base_url, token, "products")
        tasks_after_mutation = _list_records(base_url, token, "tasks")

        # We should have: original 5 - 1 deleted + 1 new = 5 products
        assert len(products_after_mutation) == len(PRODUCTS_DATA), (
            f"Product count mismatch after mutation: "
            f"expected {len(PRODUCTS_DATA)}, got {len(products_after_mutation)}"
        )
        # We should have: original 7 - 1 deleted + 1 new = 7 tasks
        assert len(tasks_after_mutation) == len(TASKS_DATA), (
            f"Task count mismatch after mutation: "
            f"expected {len(TASKS_DATA)}, got {len(tasks_after_mutation)}"
        )

        # Verify the mutated product has different values
        mutated_product = next(
            r for r in products_after_mutation if r["id"] == product_to_mutate["id"]
        )
        assert float(mutated_product["price"]) == 0.01, "Mutation should have changed price"
        assert mutated_product["description"] == "MUTATED", "Mutation should have changed description"

        # Verify the deleted product is gone
        product_ids_after = {r["id"] for r in products_after_mutation}
        assert product_to_delete["id"] not in product_ids_after, "Deleted product should be gone"

        # Verify the imposter product exists
        imposter_names = [r["name"] for r in products_after_mutation]
        assert "IMPOSTER PRODUCT" in imposter_names, "Imposter product should exist pre-restore"

        # ------------------------------------------------------------------
        # 9. Restore backup
        # ------------------------------------------------------------------
        restored = backup_mod.restore_backup(base_url, backup_key, auth_token=token)
        assert restored is True, "Backup restore failed"

        # PocketBase restores to disk but keeps serving old data - restart
        _restart_pocketbase(drained_pb)

        # ------------------------------------------------------------------
        # 10. Verify data matches original snapshot
        # ------------------------------------------------------------------

        # Re-authenticate (new process = new token)
        token = backup_mod.authenticate(base_url, email, password)
        assert token is not None, "Re-authentication after restore failed"

        products_restored = _list_records(base_url, token, "products")
        tasks_restored = _list_records(base_url, token, "tasks")

        # --- Product verification ---
        products_by_id_restored = {r["id"]: r for r in products_restored}

        # Same number of products
        assert len(products_restored) == len(products_before), (
            f"Product count mismatch after restore: "
            f"expected {len(products_before)}, got {len(products_restored)}"
        )

        # Same set of IDs
        assert set(products_by_id_restored.keys()) == set(products_by_id_before.keys()), (
            "Product IDs don't match after restore"
        )

        # Field-by-field comparison for each product
        for pid, original in products_by_id_before.items():
            restored_rec = products_by_id_restored[pid]
            assert restored_rec["name"] == original["name"], (
                f"Product {pid}: name mismatch - '{original['name']}' vs '{restored_rec['name']}'"
            )
            assert float(restored_rec["price"]) == float(original["price"]), (
                f"Product {pid}: price mismatch - {original['price']} vs {restored_rec['price']}"
            )
            assert restored_rec.get("description") == original.get("description"), (
                f"Product {pid}: description mismatch"
            )
            assert restored_rec.get("in_stock") == original.get("in_stock"), (
                f"Product {pid}: in_stock mismatch"
            )

        # No imposter product
        restored_product_names = {r["name"] for r in products_restored}
        assert "IMPOSTER PRODUCT" not in restored_product_names, (
            "Imposter product should not exist after restore"
        )

        # --- Task verification ---
        tasks_by_id_restored = {r["id"]: r for r in tasks_restored}

        # Same number of tasks
        assert len(tasks_restored) == len(tasks_before), (
            f"Task count mismatch after restore: "
            f"expected {len(tasks_before)}, got {len(tasks_restored)}"
        )

        # Same set of IDs
        assert set(tasks_by_id_restored.keys()) == set(tasks_by_id_before.keys()), (
            "Task IDs don't match after restore"
        )

        # Field-by-field comparison for each task
        for tid, original in tasks_by_id_before.items():
            restored_rec = tasks_by_id_restored[tid]
            assert restored_rec["title"] == original["title"], (
                f"Task {tid}: title mismatch"
            )
            assert restored_rec.get("done") == original.get("done"), (
                f"Task {tid}: done mismatch"
            )
            assert restored_rec.get("priority") == original.get("priority"), (
                f"Task {tid}: priority mismatch"
            )

        # No imposter task
        restored_task_titles = {r["title"] for r in tasks_restored}
        assert "IMPOSTER TASK" not in restored_task_titles, (
            "Imposter task should not exist after restore"
        )

    def test_backup_download_and_restore(
        self, drained_pb, isolated_env
    ):
        """Test the full download -> restore pipeline via downloaded backup file.

        This verifies the download_backup function works correctly and that
        the downloaded file is a valid backup archive.
        """
        from pocketmanager.core import backup as backup_mod

        base_url = drained_pb.url

        # Setup: create superuser, authenticate
        email = "download-test@example.com"
        password = "download-test-password-12345"

        result = drained_pb.create_superuser(email, password)
        assert result is True, "Superuser creation failed"

        token = backup_mod.authenticate(base_url, email, password)
        assert token is not None, "Authentication failed"

        # Create a collection and insert data
        _create_collection(
            base_url, token, "notes",
            [
                {"name": "content", "type": "text", "required": True},
                {"name": "important", "type": "bool", "required": False},
            ],
        )

        notes = [
            {"content": "Buy groceries", "important": False},
            {"content": "Submit report", "important": True},
            {"content": "Call dentist", "important": False},
        ]
        for note in notes:
            _create_record(base_url, token, "notes", note)

        # Snapshot
        notes_before = _list_records(base_url, token, "notes")
        assert len(notes_before) == 3

        # Create backup (auto-generate name)
        backup_mod.create_backup(base_url, auth_token=token)
        backups = backup_mod.list_backups(base_url, auth_token=token)

        assert len(backups) >= 1, "Expected at least one backup"
        dl_backup = backups[0]

        # Download the backup
        dest_path = str(isolated_env["home"] / "test_backup.zip")
        downloaded = backup_mod.download_backup(
            base_url, dl_backup["key"], dest_path, auth_token=token
        )
        assert downloaded is True, "Download failed"

        dest_file = Path(dest_path)
        assert dest_file.exists(), "Downloaded file should exist"
        assert dest_file.stat().st_size > 0, "Downloaded file should not be empty"

        # Verify it looks like a zip archive (backup archives are zip files)
        import zipfile

        assert zipfile.is_zipfile(dest_path), "Backup should be a valid zip archive"

        # Mutate data
        _delete_record(base_url, token, "notes", notes_before[0]["id"])
        _update_record(
            base_url, token, "notes", notes_before[1]["id"],
            {"content": "MUTATED"},
        )

        # Verify mutation
        notes_mutated = _list_records(base_url, token, "notes")
        assert len(notes_mutated) == 2
        mutated_note = next(r for r in notes_mutated if r["id"] == notes_before[1]["id"])
        assert mutated_note["content"] == "MUTATED"

        # Restore from the backup (using the server-side key, not the file)
        restored = backup_mod.restore_backup(
            base_url, dl_backup["key"], auth_token=token
        )
        assert restored is True, "Restore failed"

        # PocketBase restores to disk but keeps serving old data - restart
        _restart_pocketbase(drained_pb)

        # Re-authenticate
        token = backup_mod.authenticate(base_url, email, password)
        assert token is not None, "Re-authentication after restore failed"

        # Verify data is restored
        notes_restored = _list_records(base_url, token, "notes")
        assert len(notes_restored) == 3, (
            f"Expected 3 notes after restore, got {len(notes_restored)}"
        )

        notes_by_id = {r["id"]: r for r in notes_restored}
        for original in notes_before:
            restored_note = notes_by_id.get(original["id"])
            assert restored_note is not None, f"Note {original['id']} missing after restore"
            assert restored_note["content"] == original["content"], (
                f"Note content mismatch: '{original['content']}' vs '{restored_note['content']}'"
            )
            assert restored_note.get("important") == original.get("important"), (
                "Note important flag mismatch"
            )

    def test_list_backups_returns_correct_metadata(
        self, drained_pb, isolated_env
    ):
        """Verify that list_backups returns correct key, modified, and size."""
        from pocketmanager.core import backup as backup_mod

        base_url = drained_pb.url
        email = "meta-test@example.com"
        password = "meta-test-password-12345"

        result = drained_pb.create_superuser(email, password)
        assert result is True

        token = backup_mod.authenticate(base_url, email, password)
        assert token is not None

        # No backups initially
        backups = backup_mod.list_backups(base_url, auth_token=token)
        assert backups == [], f"Expected no backups, got {backups}"

        # Create a backup (auto-generate name)
        backup_mod.create_backup(base_url, auth_token=token)

        backups = backup_mod.list_backups(base_url, auth_token=token)
        assert len(backups) == 1

        b = backups[0]
        assert "key" in b
        assert "modified" in b, "Backup should have 'modified' field"
        assert "size" in b, "Backup should have 'size' field"
        assert b["size"] > 0, "Backup size should be > 0"

    def test_delete_backup_removes_from_list(
        self, drained_pb, isolated_env
    ):
        """Verify that delete_backup actually removes the backup."""
        from pocketmanager.core import backup as backup_mod

        base_url = drained_pb.url
        email = "delete-test@example.com"
        password = "delete-test-password-12345"

        result = drained_pb.create_superuser(email, password)
        assert result is True

        token = backup_mod.authenticate(base_url, email, password)
        assert token is not None

        # Create two backups with a small delay to avoid name collisions
        backup_mod.create_backup(base_url, auth_token=token)
        time.sleep(1.5)
        backup_mod.create_backup(base_url, auth_token=token)

        backups = backup_mod.list_backups(base_url, auth_token=token)
        assert len(backups) == 2, f"Expected 2 backups, got {len(backups)}"

        to_delete = backups[0]
        to_keep = backups[1]

        # Delete one
        deleted = backup_mod.delete_backup(
            base_url, to_delete["key"], auth_token=token
        )
        assert deleted is True, "Delete failed"

        # Verify only one remains
        backups_after = backup_mod.list_backups(base_url, auth_token=token)
        assert len(backups_after) == 1
        assert backups_after[0]["key"] == to_keep["key"], (
            f"Expected keep backup to remain, got {backups_after[0]['key']}"
        )
