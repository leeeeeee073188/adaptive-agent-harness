from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adaptive_harness.transform_manifest import (
    PathAccessKind,
    PythonTransformManifestScanner,
)


class TransformManifestTests(unittest.TestCase):
    def test_static_manifest_exposes_missing_read_and_declared_write(self) -> None:
        source = '''from pathlib import Path
SNAPSHOTS = Path(__file__).resolve().parent.parent.parent / "snapshots"

def load():
    with (SNAPSHOTS / "manifest.json").open(encoding="utf-8") as file:
        return file.read()

def save():
    output = Path(__file__).parent / "results.json"
    output.write_text("{}", encoding="utf-8")
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "workspace/analysis/audit.py"
            script.parent.mkdir(parents=True)
            (root / "workspace/snapshots").mkdir(parents=True)
            (root / "workspace/snapshots/manifest.json").write_text("{}", encoding="utf-8")
            script.write_text(source, encoding="utf-8")

            scan = PythonTransformManifestScanner().scan(root)

        self.assertEqual(scan.scanned_scripts, 1)
        manifest = scan.manifests[0]
        self.assertEqual(manifest.script_path, "workspace/analysis/audit.py")
        self.assertEqual(len(manifest.sha256), 64)
        reads = [access for access in manifest.accesses if access.kind is PathAccessKind.READ]
        writes = [access for access in manifest.accesses if access.kind is PathAccessKind.WRITE]
        self.assertEqual([(item.path, item.exists) for item in reads], [("snapshots/manifest.json", False)])
        self.assertEqual(
            [(item.path, item.exists) for item in writes],
            [("workspace/analysis/results.json", False)],
        )
        self.assertEqual(manifest.missing_reads, tuple(reads))

    def test_dynamic_path_is_counted_without_evaluation(self) -> None:
        source = '''from pathlib import Path
ROOT = Path(__file__).parent

def load(name):
    return (ROOT / f"{name}.json").read_text()
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "workspace/dynamic.py"
            script.parent.mkdir(parents=True)
            script.write_text(source, encoding="utf-8")

            scan = PythonTransformManifestScanner().scan(root)

        self.assertEqual(scan.manifests[0].accesses, ())
        self.assertEqual(scan.manifests[0].unresolved_access_count, 1)

    def test_dynamic_open_mode_is_unknown_not_misclassified_as_read(self) -> None:
        source = '''from pathlib import Path
TARGET = Path(__file__).parent / "data.json"

def access(mode):
    with open(TARGET, mode):
        pass
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "workspace/dynamic_mode.py"
            script.parent.mkdir(parents=True)
            script.write_text(source, encoding="utf-8")

            manifest = PythonTransformManifestScanner().scan(root).manifests[0]

        self.assertEqual(manifest.accesses, ())
        self.assertEqual(manifest.unresolved_access_count, 1)

    def test_access_symlink_and_private_target_are_redacted(self) -> None:
        source = '''from pathlib import Path
TARGET = Path(__file__).parent / "link" / "real_secret_name.txt"
TARGET.read_text()
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            private = root / "private"
            workspace.mkdir()
            private.mkdir()
            secret = private / "real_secret_name.txt"
            secret.write_text("secret", encoding="utf-8")
            (workspace / "link").symlink_to(private, target_is_directory=True)
            (workspace / "scan.py").write_text(source, encoding="utf-8")

            manifest = PythonTransformManifestScanner().scan(root).manifests[0]
            access = manifest.accesses[0]

        self.assertEqual(access.path, "<restricted-path>")
        self.assertFalse(access.exists)
        self.assertFalse(access.safe)
        self.assertNotIn("real_secret_name", access.path)
        self.assertEqual(manifest.missing_reads, ())
        self.assertEqual(manifest.unsafe_accesses, (access,))

    def test_scan_is_bounded_and_skips_symlink_and_oversized_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "a.py").write_text("x = 1\n", encoding="utf-8")
            (workspace / "b.py").write_text("x = 2\n", encoding="utf-8")
            (workspace / "oversized.py").write_text("x" * 100, encoding="utf-8")
            outside = root / "outside.py"
            outside.write_text("secret = True\n", encoding="utf-8")
            (workspace / "leak.py").symlink_to(outside)

            scan = PythonTransformManifestScanner(
                max_scripts=1,
                max_script_bytes=32,
            ).scan(root)
            secure_scan = PythonTransformManifestScanner(
                max_scripts=10,
                max_script_bytes=32,
            ).scan(root)

        self.assertEqual(scan.scanned_scripts, 1)
        self.assertTrue(scan.truncated)
        self.assertEqual(scan.manifests[0].script_path, "workspace/a.py")
        self.assertEqual(secure_scan.scanned_scripts, 2)
        self.assertEqual(secure_scan.skipped_scripts, 1)
        self.assertNotIn("workspace/leak.py", [item.script_path for item in secure_scan.manifests])

    def test_visited_path_limit_fails_closed_without_partial_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            for name in ("a.py", "b.py", "c.py"):
                (workspace / name).write_text("x = 1\n", encoding="utf-8")

            scan = PythonTransformManifestScanner(max_visited_paths=2).scan(root)

        self.assertEqual(scan.manifests, ())
        self.assertEqual(scan.visited_paths, 2)
        self.assertTrue(scan.truncated)

    def test_invalid_bounds_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "bounds must be positive"):
            PythonTransformManifestScanner(max_scripts=0)


if __name__ == "__main__":
    unittest.main()
