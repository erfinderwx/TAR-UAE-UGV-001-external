"""Regression cases use disposable Git repositories; project files are untouched."""
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from check_changes import validate


class ChangeDisciplineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run_git("init", "-q")
        self.run_git("config", "user.name", "Document test")
        self.run_git("config", "user.email", "test@example.invalid")
        self.run_git("config", "core.autocrlf", "false")
        self.write("docs/00_总览/01_记录.md", self.document())
        self.write("docs/README.md", "# 导读\n")
        self.write("docs/INDEX.md", "# 索引\n")
        self.write("AGENTS.md", "# AI 指引\n")
        self.write("CLAUDE.md", "# AI 指引\n")
        self.original = b"original evidence\n"
        self.write("sources/originals/evidence.txt", self.original)
        self.record = {"id": "SRC-001", "path": "sources/originals/evidence.txt",
                       "original_filename": "evidence.txt", "bytes": len(self.original),
                       "sha256": hashlib.sha256(self.original).hexdigest()}
        self.inventory([self.record])
        self.run_git("add", ".")
        self.run_git("commit", "-qm", "baseline")
        self.base = self.run_git("rev-parse", "HEAD").strip()

    def run_git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True,
                              check=True).stdout.decode("utf-8")

    def write(self, path, data):
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))

    def inventory(self, records):
        self.write("sources/records/source-manifest.json", json.dumps({"files": records}))

    @staticmethod
    def document(version="v1.0", updated="2026-10-31", status="草稿", extra="", body="原正文"):
        return (f"---\ntitle: 记录\nowner: 测试负责人\nstatus: {status}\nversion: {version}\n"
                f"updated: '{updated}'\nsummary: 变更测试\n{extra}---\n\n# 记录\n\n{body}\n")

    def errors(self):
        return validate(self.root, self.base)["errors"]

    def assert_error(self, fragment):
        errors = self.errors()
        self.assertTrue(any(fragment in message for message in errors), errors)

    def test_unchanged_baseline_passes(self):
        self.assertEqual([], self.errors())

    def test_windows_checkout_newlines_do_not_require_version_bump(self):
        self.write("docs/00_总览/01_记录.md", self.document().replace("\n", "\r\n"))
        self.assertEqual([], self.errors())

    def test_changed_body_without_version_bump_fails(self):
        self.write("docs/00_总览/01_记录.md", self.document(body="新正文"))
        self.assert_error("must increase version")

    def test_same_day_version_bump_passes(self):
        self.write("docs/00_总览/01_记录.md", self.document(version="v1.1", body="新正文"))
        self.assertEqual([], self.errors())

    def test_equivalent_version_with_extra_zero_is_not_a_bump(self):
        self.write("docs/00_总览/01_记录.md", self.document(version="v1.0.0", body="新正文"))
        self.assert_error("must increase version")

    def test_backdated_update_fails(self):
        self.write("docs/00_总览/01_记录.md", self.document(version="v1.1", updated="2026-10-30"))
        self.assert_error("must not move backwards")

    def test_untracked_new_document_passes(self):
        self.write("docs/00_总览/02_新记录.md", self.document(version="v0.1"))
        result = validate(self.root, self.base)
        self.assertEqual(2, result["documents"])
        self.assertEqual([], result["errors"])

    def test_readme_index_and_templates_are_exempt_from_version_bumps(self):
        for path in ["README.md", "docs/README.md", "docs/INDEX.md", "docs/_templates/模板.md"]:
            self.write(path, "# 更新后的导读或模板\n")
        self.assertEqual([], self.errors())

    def test_changing_original_and_its_manifest_hash_together_fails(self):
        changed = b"rewritten evidence\n"
        self.write(self.record["path"], changed)
        self.inventory([{**self.record, "sha256": hashlib.sha256(changed).hexdigest(), "bytes": len(changed)}])
        self.assert_error("immutable")
        self.assert_error("differ from base")

    def test_deleting_original_and_manifest_entry_fails(self):
        (self.root / self.record["path"]).unlink()
        self.inventory([])
        self.assert_error("must remain in the manifest")
        self.assert_error("original is missing")

    def test_new_original_is_allowed_while_old_one_is_preserved(self):
        self.write("sources/originals/new.txt", b"new")
        new = {"id": "SRC-002", "path": "sources/originals/new.txt", "original_filename": "new.txt",
               "bytes": 3, "sha256": hashlib.sha256(b"new").hexdigest()}
        self.inventory([self.record, new])
        self.assertEqual([], self.errors())

    def test_source_id_cannot_be_reassigned(self):
        self.inventory([{**self.record, "id": "SRC-002"}])
        self.assert_error("id is immutable")

    def test_invalid_date_and_version_fail_without_a_base(self):
        self.write("docs/00_总览/01_记录.md", self.document(updated="2026-02-30"))
        self.assertTrue(any("valid calendar date" in error for error in validate(self.root)["errors"]))
        self.write("docs/00_总览/01_记录.md", self.document(version="final"))
        self.assertTrue(any("version must be" in error for error in validate(self.root)["errors"]))

    def test_ai_entrypoints_must_match(self):
        self.write("CLAUDE.md", "# 另一份指引\n")
        self.assert_error("byte-identical")

    def test_final_status_requires_confirmation_fields(self):
        self.write("docs/00_总览/01_记录.md", self.document(version="v1.1", status="定稿"))
        self.assert_error("approved_by")

    def test_final_confirmation_reference_accepts_existing_file(self):
        self.write("docs/审批记录.md", self.document())
        fields = "approved_by: 测试审阅人\napproved_on: '2026-10-31'\napproval_ref: docs/审批记录.md\n"
        self.write("docs/00_总览/01_记录.md", self.document(version="v1.1", status="定稿", extra=fields))
        self.assertEqual([], self.errors())

    def test_final_confirmation_reference_rejects_missing_file(self):
        fields = "approved_by: 测试审阅人\napproved_on: '2026-10-31'\napproval_ref: docs/missing.md\n"
        self.write("docs/00_总览/01_记录.md", self.document(version="v1.1", status="定稿", extra=fields))
        self.assert_error("approval_ref")

    def test_invalid_base_fails_instead_of_skipping_comparison(self):
        self.assertTrue(validate(self.root, "no-such-ref")["errors"])


if __name__ == "__main__":
    unittest.main()
