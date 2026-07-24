import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "skills"
    / "soia-cwork-processon-diagrams"
    / "scripts"
    / "processon_archive_supervisor.py"
)
RUNNER_DIR = SCRIPT.parent
sys.path.insert(0, str(RUNNER_DIR))
SPEC = importlib.util.spec_from_file_location("processon_archive_supervisor", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ProcessOnArchiveSupervisorTests(unittest.TestCase):
    artifact_id = "a" * 64

    def args(self, root: Path) -> argparse.Namespace:
        plan = root / "archive-plan.json"
        progress = root / "download-progress.json"
        config = root / "config.yml"
        profile = root / "browser-profile"
        proof = root / "concurrency-proof.json"
        plan.write_text("{}", encoding="utf-8")
        progress.write_text("{}", encoding="utf-8")
        config.write_text("{}", encoding="utf-8")
        proof.write_text("{}", encoding="utf-8")
        profile.mkdir()
        return argparse.Namespace(
            plan=plan,
            progress=progress,
            team_url="https://www.processon.com/org/teams/example",
            config=config,
            profile_dir=profile,
            concurrency_proof=proof,
            workers=2,
            limit=8,
            timeout_ms=15_000,
            max_batches=2,
            state_file=root / "archive-supervisor-state.json",
        )

    def test_xmind_pending_requires_exact_reason_and_valid_artifact_id(self):
        payload = {
            "pending": [
                {
                    "artifact_id": self.artifact_id,
                    "error": MODULE.XMind_MENU_ABSENCE,
                },
                {"artifact_id": "b" * 64, "error": "other"},
            ]
        }
        self.assertEqual(MODULE.known_xmind_pending_ids(payload), [self.artifact_id])
        with self.assertRaisesRegex(MODULE.SupervisorError, "valid artifact_id"):
            MODULE.known_xmind_pending_ids(
                {"pending": [{"artifact_id": "short", "error": MODULE.XMind_MENU_ABSENCE}]}
            )

    def test_workers_above_one_require_concurrency_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp))
            args.concurrency_proof = None
            with self.assertRaisesRegex(MODULE.SupervisorError, "requires --concurrency-proof"):
                MODULE.validate_args(args)

    def test_supervisor_marks_only_known_xmind_then_continues_to_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = self.args(root)
            receipt = root / "batch-receipt.json"
            receipt.write_text("{}", encoding="utf-8")
            batch_partial = {
                "status": "partial",
                "receipt_file": str(receipt),
                "selected": 1,
                "completed_count": 0,
                "blocked_count": 0,
                "pending_count": 1,
                "pending": [
                    {
                        "artifact_id": self.artifact_id,
                        "error": MODULE.XMind_MENU_ABSENCE,
                    }
                ],
            }
            batch_done = {"status": "nothing_to_do", "pending": []}
            audit = {"status": "passed", "counts": {"completed": 1}}
            marks = []

            def fake_run_json(_command, *, label, allow_nonzero=False):
                if label == "archive batch":
                    return (batch_partial if not marks else batch_done), 1
                self.assertEqual(label, "archive state audit")
                self.assertTrue(allow_nonzero)
                return audit, 0

            with patch.object(MODULE, "run_json", side_effect=fake_run_json), patch.object(
                MODULE,
                "mark_known_xmind_failure",
                side_effect=lambda _args, artifact_id, receipt_file: marks.append(
                    (artifact_id, receipt_file)
                ) or {"status": "failed"},
            ):
                result = MODULE.supervise(args)

            self.assertEqual(result["status"], "nothing_to_do")
            self.assertEqual(marks, [(self.artifact_id, str(receipt))])
            persisted = json.loads(args.state_file.read_text(encoding="utf-8"))
            self.assertEqual(persisted["status"], "nothing_to_do")
            self.assertEqual(len(persisted["history"]), 2)
