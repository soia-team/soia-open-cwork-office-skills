import argparse
import asyncio
import importlib.util
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "soia-cwork-processon-diagrams" / "scripts" / "processon_archive_batch.py"
RUNNER_DIR = SCRIPT.parent
import sys

sys.path.insert(0, str(RUNNER_DIR))
SPEC = importlib.util.spec_from_file_location("processon_archive_batch", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class MenuLocator:
    def __init__(self, visible):
        self.visible = visible

    def filter(self, **_kwargs):
        return self

    def nth(self, _index):
        return self

    async def count(self):
        return 1 if self.visible else 0

    async def is_visible(self):
        return self.visible


class MenuPage:
    def __init__(self, visible_labels):
        self.visible_labels = set(visible_labels)

    def get_by_text(self, label, *, exact):
        assert exact is True
        return MenuLocator(label in self.visible_labels)

    async def wait_for_timeout(self, _milliseconds):
        return None


class ExactTitleNodes:
    def __init__(self, row, count):
        self.row = row
        self.node_count = count

    def filter(self, **_kwargs):
        return self

    async def count(self):
        return self.node_count

    def nth(self, _index):
        return self.row


class ExactTitleRow:
    def __init__(self, index, duplicate_text_nodes=1):
        self.index = index
        self.duplicate_text_nodes = duplicate_text_nodes

    async def is_visible(self):
        return True

    def get_by_text(self, _title, *, exact):
        assert exact is True
        return ExactTitleNodes(self, self.duplicate_text_nodes)


class ExactTitleRows:
    def __init__(self, count, duplicate_text_nodes=1):
        self.rows = [
            ExactTitleRow(index, duplicate_text_nodes) for index in range(count)
        ]

    def filter(self, **_kwargs):
        return self

    async def count(self):
        return len(self.rows)

    def nth(self, index):
        return self.rows[index]


class ExactTitlePage:
    def __init__(self, count, duplicate_text_nodes=1):
        self.rows = ExactTitleRows(count, duplicate_text_nodes)

    def locator(self, selector):
        assert selector == "div.file_list_item"
        return self.rows


class FlowchartIconLocator:
    def __init__(self, present):
        self.present = present

    async def count(self):
        return 1 if self.present else 0


class FlowchartTitleRow(ExactTitleRow):
    def locator(self, selector):
        return FlowchartIconLocator(selector == ".icon-a-222_huaban1")


class FlowchartTitleRows(ExactTitleRows):
    def __init__(self):
        self.rows = [FlowchartTitleRow(0)]


class FlowchartTitlePage(ExactTitlePage):
    def __init__(self):
        self.rows = FlowchartTitleRows()


class DelayedEditorLocator(MenuLocator):
    def __init__(self, page, label, visible):
        super().__init__(visible)
        self.page = page
        self.label = label

    async def click(self, *, timeout):
        self.page.clicked.append((self.label, timeout))
        if self.label == MODULE.EDITOR_FILE_MENU:
            self.page.file_clicked = True

    def current_visible(self):
        if self.label == MODULE.EDITOR_FILE_MENU:
            return self.page.has_file and self.page.waits >= self.page.file_visible_after
        if self.label == MODULE.EDITOR_EXPORT_MENU:
            return self.page.file_clicked or self.page.direct_mindmap_export
        if self.label == "VISIO文件":
            return self.page.file_clicked
        if self.label == "Xmind文件":
            return self.page.direct_mindmap_export
        if self.label == "XMind文件":
            return self.page.direct_mindmap_export_variant
        return False

    async def count(self):
        return 1 if self.current_visible() else 0

    async def is_visible(self):
        return self.current_visible()


class DelayedEditorPage:
    url = "https://www.processon.com/diagraming/example"

    def __init__(
        self,
        *,
        file_visible_after=0,
        has_file=True,
        direct_mindmap_export=False,
        direct_mindmap_export_variant=False,
        attribute_only_labels=(),
    ):
        self.file_visible_after = file_visible_after
        self.has_file = has_file
        self.direct_mindmap_export = direct_mindmap_export
        self.direct_mindmap_export_variant = direct_mindmap_export_variant
        self.attribute_only_labels = set(attribute_only_labels)
        self.waits = 0
        self.file_clicked = False
        self.clicked = []

    def get_by_text(self, label, *, exact):
        assert exact is True
        if label in self.attribute_only_labels:
            return MenuLocator(False)
        return DelayedEditorLocator(self, label, False)

    def locator(self, selector):
        for label in self.attribute_only_labels:
            if label in selector:
                return DelayedEditorLocator(self, label, False)
        return MenuLocator(False)

    async def wait_for_timeout(self, _milliseconds):
        self.waits += 1
        await asyncio.sleep(0.001)


class ProcessOnArchiveBatchTests(unittest.TestCase):
    def entry(self, artifact_id="a", collision="none_detected"):
        return {
            "artifact_id": artifact_id,
            "confirmation_required": False,
            "type": "flowchart",
            "collision_risk": collision,
            "source_directory": "root/folder",
            "source_path": f"root/folder/{artifact_id}",
            "title": artifact_id,
            "primary_format": "vsdx",
            "primary_menu": "VISIO文件",
        }

    def test_parallel_selection_skips_collision_risk(self):
        plan = {"entries": [self.entry("safe"), self.entry("collision", "duplicate_title")]}
        progress = {"completed": [], "failed": [], "blocked": []}
        selected = MODULE.choose_entries(plan, progress, 10, workers=2)
        self.assertEqual([item["artifact_id"] for item in selected], ["safe"])
        serial = MODULE.choose_entries(plan, progress, 10, workers=1)
        self.assertEqual([item["artifact_id"] for item in serial], ["safe"])
        deferred = MODULE.deferred_collision_entries(plan, progress)
        self.assertEqual([item["artifact_id"] for item in deferred], ["collision"])

    def test_explicit_blocked_retry_selects_only_named_current_block(self):
        blocked = self.entry("blocked")
        plan = {"entries": [blocked, self.entry("other")]}
        progress = {
            "completed": [],
            "failed": [],
            "blocked": [{"artifact_id": "blocked"}],
        }
        selected = MODULE.choose_entries(
            plan,
            progress,
            10,
            workers=1,
            retry_blocked=True,
            artifact_ids=["blocked"],
        )
        self.assertEqual([item["artifact_id"] for item in selected], ["blocked"])

    def test_reconciliation_skips_unselected_terminal_states(self):
        progress = {
            "completed": [{"artifact_id": "completed"}],
            "failed": [{"artifact_id": "failed"}],
            "blocked": [{"artifact_id": "blocked"}],
        }
        skipped = MODULE.reconciliation_skip_ids(
            progress, explicitly_retried_ids={"failed"}
        )
        self.assertEqual(skipped, {"completed", "blocked"})

    def test_reconciliation_never_reopens_completed_state(self):
        progress = {
            "completed": [{"artifact_id": "completed"}],
            "failed": [],
            "blocked": [],
        }
        skipped = MODULE.reconciliation_skip_ids(
            progress, explicitly_retried_ids={"completed"}
        )
        self.assertEqual(skipped, {"completed"})

    def test_recreated_move_source_is_quarantined_inside_private_run_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_id = "artifact"
            download_dir = root / "managed" / "run"
            source = download_dir / artifact_id / "document.pos"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"new retry export")
            progress = root / "run" / "artifacts" / "download-progress.json"
            progress.parent.mkdir(parents=True)
            result = MODULE.quarantine_recreated_move_source(
                source,
                artifact_id=artifact_id,
                archived_sha256=MODULE.hashlib.sha256(b"archived export").hexdigest(),
                manifest={"operation": "move"},
                args=argparse.Namespace(download_dir=download_dir, progress=progress),
            )
            self.assertIsNotNone(result)
            self.assertFalse(source.exists())
            quarantine = Path(result["quarantine_path"])
            self.assertTrue(quarantine.is_file())
            self.assertEqual(quarantine.read_bytes(), b"new retry export")
            self.assertEqual(
                quarantine.parent,
                progress.parent / "retry-residuals" / artifact_id,
            )

    def test_recreated_move_source_outside_artifact_staging_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "outside" / "document.pos"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"changed")
            with self.assertRaisesRegex(MODULE.BatchError, "outside the artifact staging"):
                MODULE.quarantine_recreated_move_source(
                    source,
                    artifact_id="artifact",
                    archived_sha256=MODULE.hashlib.sha256(b"archived").hexdigest(),
                    manifest={"operation": "move"},
                    args=argparse.Namespace(
                        download_dir=root / "managed" / "run",
                        progress=root / "run" / "artifacts" / "download-progress.json",
                    ),
                )

    def test_empty_source_retry_recovers_exact_audited_quarantine_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = self.entry("empty")
            metadata = root / "_quarantine" / "run" / "empty" / "metadata.yml"
            metadata.parent.mkdir(parents=True)
            metadata.write_text(
                "\n".join(
                    [
                        'artifact_id: "empty"',
                        'source_path: "root/folder/empty"',
                        'source_url: "https://www.processon.com/diagraming/remote-empty"',
                        'remote_id: "remote-empty"',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            blocked = {
                "artifact_id": "empty",
                "reason": "editor snapshot shows an empty canvas",
                "revalidation": {
                    "quarantine_files": [
                        {
                            "quarantine_path": str(metadata),
                            "bytes": metadata.stat().st_size,
                            "sha256": MODULE.sha256(metadata),
                        }
                    ]
                },
            }
            recovered = MODULE.retry_blocked_empty_source_binding_from_evidence(
                entry,
                blocked,
                output_root=root,
            )
            self.assertEqual(
                recovered["source_url"],
                "https://www.processon.com/diagraming/remote-empty",
            )
            self.assertEqual(recovered["remote_id"], "remote-empty")

    def test_empty_source_promotion_requires_live_zero_text_export(self):
        entry = self.entry("empty")
        inspection = {
            "kind": "visio-vsdx",
            "text_count": 0,
            "semantic_status": "source_binding_missing",
        }
        promoted = MODULE.apply_observed_source_binding(
            inspection,
            browser_result={
                "source_url": "https://www.processon.com/diagraming/remote-empty",
                "remote_id": "remote-empty",
                "empty_source_confirmed": True,
            },
            entry=entry,
        )
        self.assertEqual(promoted["content_semantic_status"], "empty_source_confirmed")
        self.assertEqual(
            promoted["semantic_match_method"],
            "live_editor_empty_canvas_and_observed_remote_id",
        )
        self.assertTrue(MODULE.visible_editor_confirms_empty_canvas("图形：0"))
        self.assertFalse(MODULE.visible_editor_confirms_empty_canvas("图形：1"))

    def test_live_empty_canvas_waits_for_rendered_shape_counter(self):
        class Page:
            def __init__(self):
                self.values = iter(("loading", "图形：0"))

            def locator(self, selector):
                self.assert_selector = selector
                return self

            async def inner_text(self, *, timeout):
                self.assert_timeout = timeout
                return next(self.values)

            async def wait_for_timeout(self, _milliseconds):
                return None

        page = Page()
        asyncio.run(MODULE.wait_for_live_empty_canvas(page, 1000))
        self.assertEqual(page.assert_selector, "body")

    def test_failed_retry_recovers_one_audited_source_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            progress_path = root / "artifacts" / "download-progress.json"
            evidence = root / "artifacts" / "evidence" / "case" / "receipt.json"
            evidence.parent.mkdir(parents=True)
            entry = self.entry("a")
            entry["title"] = "same"
            entry["source_path"] = "root/folder/same"
            receipt = {
                "schema_version": 1,
                "pending": [
                    {
                        "artifact_id": "a",
                        "title": "same",
                        "source_path": "root/folder/same",
                        "source_url": "https://www.processon.com/diagraming/remote-a",
                    }
                ],
            }
            evidence.write_text(json.dumps(receipt), encoding="utf-8")
            progress = {
                "failed": [
                    {
                        "artifact_id": "a",
                        "evidence_files": [
                            {
                                "archived_path": str(evidence),
                                "bytes": evidence.stat().st_size,
                                "sha256": MODULE.sha256(evidence),
                            }
                        ],
                    }
                ]
            }

            selected, bound_ids = MODULE.bind_retry_failed_source_evidence(
                [entry],
                progress,
                progress_path=progress_path,
            )

            self.assertEqual(bound_ids, ["a"])
            self.assertEqual(selected[0]["remote_id"], "remote-a")
            self.assertEqual(
                selected[0]["_direct_source_url"],
                "https://www.processon.com/diagraming/remote-a",
            )
            self.assertEqual(
                selected[0]["_source_lookup_method"],
                "audited_failed_receipt_source_url",
            )

    def test_failed_retry_rejects_source_route_for_wrong_document_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            progress_path = root / "artifacts" / "download-progress.json"
            evidence = root / "artifacts" / "evidence" / "case" / "receipt.json"
            evidence.parent.mkdir(parents=True)
            entry = self.entry("a")
            entry["title"] = "same"
            entry["source_path"] = "root/folder/same"
            receipt = {
                "schema_version": 1,
                "pending": [
                    {
                        "artifact_id": "a",
                        "title": "same",
                        "source_path": "root/folder/same",
                        "source_url": "https://www.processon.com/mindmap/remote-a",
                    }
                ],
            }
            evidence.write_text(json.dumps(receipt), encoding="utf-8")
            failed = {
                "artifact_id": "a",
                "evidence_files": [
                    {
                        "archived_path": str(evidence),
                        "bytes": evidence.stat().st_size,
                        "sha256": MODULE.sha256(evidence),
                    }
                ],
            }

            with self.assertRaisesRegex(MODULE.BatchError, "route differs"):
                MODULE.retry_failed_source_binding_from_evidence(
                    entry,
                    failed,
                    progress_path=progress_path,
                )

    def test_collision_confirmation_is_plan_bound_ordered_and_single_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self.entry("first", "duplicate_title")
            second = self.entry("second", "duplicate_title")
            for entry in (first, second):
                entry["title"] = "same"
                entry["source_path"] = "root/folder/same"
            plan = {"schema_version": 1, "entries": [first, second]}
            plan_path = root / "archive-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            progress = {
                "plan": {"sha256": MODULE.sha256(plan_path)},
                "completed": [],
                "failed": [],
                "blocked": [],
            }
            confirmation_path = root / "collision-confirmation.json"
            confirmation = {
                "schema_version": 1,
                "kind": "processon_collision_confirmation",
                "confirmation_method": "inventory_order",
                "plan_sha256": progress["plan"]["sha256"],
                "entries": [
                    {
                        "artifact_id": "first",
                        "source_directory": "root/folder",
                        "title": "same",
                        "occurrence": 0,
                        "group_size": 2,
                    },
                    {
                        "artifact_id": "second",
                        "source_directory": "root/folder",
                        "title": "same",
                        "occurrence": 1,
                        "group_size": 2,
                    },
                ],
            }
            confirmation_path.write_text(json.dumps(confirmation), encoding="utf-8")
            loaded = MODULE.load_collision_confirmation(
                confirmation_path,
                plan_path=plan_path,
                plan=plan,
                progress=progress,
            )
            selected = MODULE.choose_entries(
                plan,
                progress,
                10,
                workers=1,
                collision_confirmations=loaded,
            )
            self.assertEqual([item["artifact_id"] for item in selected], ["first", "second"])
            self.assertEqual(selected[1]["_collision_occurrence"], 1)
            with self.assertRaisesRegex(MODULE.BatchError, "workers 1"):
                MODULE.choose_entries(
                    plan,
                    progress,
                    10,
                    workers=2,
                    collision_confirmations=loaded,
                )
            confirmation["entries"][1]["occurrence"] = 0
            confirmation_path.write_text(json.dumps(confirmation), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.BatchError, "order or group metadata"):
                MODULE.load_collision_confirmation(
                    confirmation_path,
                    plan_path=plan_path,
                    plan=plan,
                    progress=progress,
                )

    def test_confirmed_collision_selects_exact_visible_occurrence(self):
        selected = asyncio.run(
            MODULE.find_title(
                ExactTitlePage(2, duplicate_text_nodes=2),
                "same",
                100,
                occurrence=1,
                expected_matches=2,
            )
        )
        self.assertEqual(selected.index, 1)

    def test_current_red_flowchart_icon_is_async_safe(self):
        selected = asyncio.run(
            MODULE.find_title(
                FlowchartTitlePage(),
                "same",
                100,
                document_type="flowchart",
            )
        )
        self.assertEqual(selected.index, 0)

    def test_confirmed_collision_can_select_from_current_superset(self):
        selected = asyncio.run(
            MODULE.find_title(
                ExactTitlePage(3),
                "same",
                100,
                occurrence=1,
                expected_matches=2,
                allow_additional_matches=True,
            )
        )
        self.assertEqual(selected.index, 1)
        with self.assertRaisesRegex(MODULE.BatchError, "exceeds confirmation"):
            asyncio.run(
                MODULE.find_title(
                    ExactTitlePage(3),
                    "same",
                    100,
                    occurrence=1,
                    expected_matches=2,
                )
            )

    def test_relative_update_matches_only_adjacent_month_drift(self):
        self.assertTrue(MODULE.relative_update_matches("10月前", "11月前"))
        self.assertTrue(MODULE.relative_update_matches("11月前", "10月前"))
        self.assertTrue(MODULE.relative_update_matches("1年前", "1年前"))
        self.assertFalse(MODULE.relative_update_matches("10月前", "1年前"))
        self.assertFalse(MODULE.relative_update_matches("10月前", "12月前"))

    def test_confirmed_collision_binding_promotes_runtime_source_identity(self):
        entry = self.entry("a" * 64, "duplicate_title")
        entry.update(
            {
                "title": "未命名文件",
                "source_path": "root/folder/未命名文件",
                "_collision_occurrence": 1,
                "_collision_group_size": 2,
                "_collision_confirmation_method": "inventory_order",
            }
        )
        result = {
            "source_url": "https://www.processon.com/diagraming/runtime-id",
            "remote_id": "runtime-id",
            "collision_binding": {
                "confirmation_method": "inventory_order",
                "occurrence": 1,
                "group_size": 2,
            },
        }
        promoted = MODULE.apply_confirmed_collision_binding(
            {"kind": "visio-vsdx", "semantic_status": "source_binding_missing"},
            browser_result=result,
            entry=entry,
        )
        self.assertEqual(promoted["semantic_status"], "matched")
        self.assertEqual(
            promoted["semantic_match_method"],
            "confirmed_inventory_order_and_observed_remote_id",
        )
        result["collision_binding"]["occurrence"] = 0
        with self.assertRaisesRegex(MODULE.BatchError, "differs from the confirmed plan order"):
            MODULE.apply_confirmed_collision_binding(
                {"kind": "visio-vsdx", "semantic_status": "source_binding_missing"},
                browser_result=result,
                entry=entry,
            )

    def test_failed_retry_requires_explicit_unique_failed_artifact_ids(self):
        plan = {"entries": [self.entry("pending"), self.entry("failed"), self.entry("blocked")]}
        progress = {
            "completed": [],
            "failed": [{"artifact_id": "failed"}],
            "blocked": [{"artifact_id": "blocked"}],
        }
        selected = MODULE.choose_entries(
            plan,
            progress,
            10,
            workers=2,
            retry_failed=True,
            artifact_ids=["failed"],
        )
        self.assertEqual([item["artifact_id"] for item in selected], ["failed"])
        with self.assertRaisesRegex(MODULE.BatchError, "requires one or more"):
            MODULE.choose_entries(plan, progress, 10, workers=1, retry_failed=True)
        with self.assertRaisesRegex(MODULE.BatchError, "requires --retry-failed"):
            MODULE.choose_entries(plan, progress, 10, workers=1, artifact_ids=["failed"])
        with self.assertRaisesRegex(MODULE.BatchError, "must be unique"):
            MODULE.choose_entries(
                plan,
                progress,
                10,
                workers=1,
                retry_failed=True,
                artifact_ids=["failed", "failed"],
            )
        with self.assertRaisesRegex(MODULE.BatchError, "currently in progress.failed"):
            MODULE.choose_entries(
                plan,
                progress,
                10,
                workers=1,
                retry_failed=True,
                artifact_ids=["pending"],
            )
        collision_plan = {"entries": [self.entry("collision", "duplicate_title")]}
        collision_progress = {
            "completed": [],
            "failed": [{"artifact_id": "collision"}],
            "blocked": [],
        }
        with self.assertRaisesRegex(MODULE.BatchError, "collision-risk"):
            MODULE.choose_entries(
                collision_plan,
                collision_progress,
                10,
                workers=1,
                retry_failed=True,
                artifact_ids=["collision"],
            )

    def test_staging_receipt_binds_one_artifact_isolated_download(self):
        artifact_id = "a" * 64
        entry = self.entry(artifact_id)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            progress = root / "run" / "artifacts" / "download-progress.json"
            download_root = root / "staging"
            source = download_root / artifact_id / "diagram.vsdx"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"staged-by-test")
            result = {
                "artifact_id": artifact_id,
                "source_path": entry["source_path"],
                "title": entry["title"],
                "requested_format": "vsdx",
                "source_url": "https://www.processon.com/diagraming/remote-id",
                "source_title": f"{entry['title']}-ProcessOn",
                "remote_id": "remote-id",
                "download_menu": "VISIO文件",
                "download": {
                    "path": str(source),
                    "bytes": source.stat().st_size,
                    "suggested_filename": source.name,
                },
            }
            receipt = MODULE.write_staging_receipt(progress, result)
            loaded = MODULE.load_staging_result(
                receipt,
                entry,
                args=argparse.Namespace(download_dir=download_root),
            )
            self.assertEqual(loaded["remote_id"], "remote-id")
            self.assertEqual(
                loaded["download"]["path"], str(source.resolve(strict=False))
            )
            MODULE.remove_staging_receipt(progress, artifact_id)
            self.assertFalse(receipt.exists())

    def test_vsdx_download_menu_prefers_all_canvases(self):
        label, _locator = asyncio.run(
            MODULE.find_download_menu(
                MenuPage({"VISIO文件", "导出全部画布 (.vsdx)"}),
                self.entry("multi-canvas"),
                timeout_ms=100,
            )
        )
        self.assertEqual(label, "导出全部画布 (.vsdx)")

    def test_vsdx_download_menu_falls_back_to_legacy_plan_label(self):
        label, _locator = asyncio.run(
            MODULE.find_download_menu(
                MenuPage({"VISIO文件"}),
                self.entry("single-canvas"),
                timeout_ms=100,
            )
        )
        self.assertEqual(label, "VISIO文件")

    def test_vsdx_download_menu_accepts_current_editor_fullwidth_label(self):
        label, _locator = asyncio.run(
            MODULE.find_download_menu(
                MenuPage({"导出全部画布 （.vsdx）"}),
                self.entry("editor-current"),
                timeout_ms=100,
            )
        )
        self.assertEqual(label, "导出全部画布 （.vsdx）")

    def test_editor_export_menu_waits_for_file_control_then_exports(self):
        page = DelayedEditorPage(file_visible_after=2)
        label, _locator = asyncio.run(
            MODULE.open_editor_export_menu(page, self.entry("delayed"), timeout_ms=100)
        )
        self.assertEqual(label, "VISIO文件")
        self.assertEqual([label for label, _timeout in page.clicked[:2]], ["文件", "导出为"])

    def test_editor_export_menu_reports_structured_unavailable_controls(self):
        page = DelayedEditorPage(has_file=False)
        with self.assertRaises(MODULE.BatchError) as caught:
            asyncio.run(MODULE.open_editor_export_menu(page, self.entry("missing"), timeout_ms=5))
        diagnostic = json.loads(str(caught.exception))
        self.assertEqual(diagnostic["kind"], "editor_export_controls_unavailable")
        self.assertEqual(diagnostic["phase"], "file_menu")
        self.assertEqual(diagnostic["editor_route"], "diagraming")
        self.assertFalse(diagnostic["controls"]["文件"])

    def test_mindmap_editor_exports_directly_without_file_menu(self):
        page = DelayedEditorPage(has_file=False, direct_mindmap_export=True)
        entry = self.entry("mindmap")
        entry.update(
            {
                "type": "mindmap",
                "primary_format": "xmind",
                "primary_menu": "Xmind文件",
            }
        )
        label, _locator = asyncio.run(
            MODULE.open_editor_export_menu(page, entry, timeout_ms=100)
        )
        self.assertEqual(label, "Xmind文件")
        self.assertEqual([label for label, _timeout in page.clicked], ["导出为"])

    def test_mindmap_editor_accepts_current_provider_xmind_capitalization(self):
        page = DelayedEditorPage(
            has_file=False,
            direct_mindmap_export=True,
            direct_mindmap_export_variant=True,
        )
        entry = self.entry("mindmap-xmind-capital-m")
        entry.update(
            {
                "type": "mindmap",
                "primary_format": "xmind",
                "primary_menu": "Xmind文件",
            }
        )
        label, _locator = asyncio.run(
            MODULE.open_editor_export_menu(page, entry, timeout_ms=100)
        )
        # Keep the canonical plan label in the receipt while matching the
        # provider's current visible ``XMind文件`` spelling.
        self.assertEqual(label, "Xmind文件")

    def test_mindmap_download_menu_uses_plan_authorized_pos_fallback(self):
        entry = self.entry("mindmap-pos")
        entry.update(
            {
                "type": "mindmap",
                "primary_format": "xmind",
                "primary_menu": "Xmind文件",
                "fallback_formats": ["pos"],
            }
        )
        label, _locator = asyncio.run(
            MODULE.find_download_menu(MenuPage({"POS文件"}), entry, timeout_ms=100)
        )
        self.assertEqual(label, "POS文件")
        self.assertEqual(
            MODULE.download_menu_format(entry, label),
            ("pos", True, "primary_export_menu_unavailable"),
        )

    def test_pos_fallback_replays_title_and_structure(self):
        entry = self.entry("mindmap-pos")
        entry.update(
            {
                "title": "业务流程",
                "type": "mindmap",
                "primary_format": "xmind",
                "fallback_formats": ["pos"],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "业务流程.pos"
            source.write_text(
                json.dumps(
                    {
                        "meta": {
                            "version": "5.0",
                            "diagramInfo": {"title": "业务流程", "category": "mind_free"},
                        },
                        "diagram": {"elements": {"title": "业务流程", "children": []}},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            inspection = MODULE.inspect_download(source, entry, "pos")
        self.assertEqual(inspection["kind"], "processon-pos")
        self.assertEqual(inspection["semantic_status"], "matched")
        self.assertEqual(inspection["title"], "业务流程")

    def test_xmind_accepts_verified_blank_sheet_title_without_root_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "未命名文件.xmind"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "content.json",
                    json.dumps(
                        [
                            {
                                "title": "未命名文件",
                                "rootTopic": {"children": {"attached": []}},
                            }
                        ],
                        ensure_ascii=False,
                    ),
                )
            inspected = MODULE.inspect_xmind(path, "未命名文件")
        self.assertEqual(inspected["semantic_status"], "matched_empty_root")
        self.assertTrue(inspected["empty_root"])
        self.assertEqual(inspected["sheet_title"], "未命名文件")

    def test_provider_filename_binding_accepts_url_style_space_encoding(self):
        self.assertTrue(
            MODULE.provider_suggested_filename_matches(
                "副本 改造涉及点", "副本+改造涉及点.pos"
            )
        )
        self.assertFalse(
            MODULE.provider_suggested_filename_matches(
                "副本 改造涉及点", "其他文件.pos"
            )
        )

    def test_mindmap_editor_accepts_attribute_only_export_control(self):
        page = DelayedEditorPage(
            has_file=False,
            direct_mindmap_export=True,
            attribute_only_labels=[MODULE.EDITOR_EXPORT_MENU],
        )
        entry = self.entry("mindmap-attribute")
        entry.update(
            {
                "type": "mindmap",
                "primary_format": "xmind",
                "primary_menu": "Xmind文件",
            }
        )
        label, _locator = asyncio.run(
            MODULE.open_editor_export_menu(page, entry, timeout_ms=100)
        )
        self.assertEqual(label, "Xmind文件")
        self.assertEqual([label for label, _timeout in page.clicked], ["导出为"])

    def test_processon_editor_url_requires_diagram_identifier(self):
        self.assertTrue(
            MODULE.is_processon_editor_url(
                "https://www.processon.com/diagraming/64240bcee72b0b460f0e96f9"
            )
        )
        self.assertFalse(
            MODULE.is_processon_editor_url(
                "https://www.processon.com/org/teams/614bf1b1e0b34d2b8d3a67a1"
            )
        )

    def test_vsdx_semantic_title_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "数据服务平台.vsdx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("visio/document.xml", "<VisioDocument />")
                archive.writestr(
                    "visio/pages/page1.xml",
                    "<PageContents><Shapes><Shape><Text>数据服务平台 exchange</Text></Shape></Shapes></PageContents>",
                )
            inspected = MODULE.inspect_vsdx(
                path, "《斛斗4.0数据服务平台&保单验真(exchange)部署架构图-生产环境》"
            )
            self.assertEqual(inspected["semantic_status"], "matched")
            self.assertIn("exchange", inspected["matched_title_signals"])
            unbound = MODULE.inspect_vsdx(path, "《风险管理系统-测试环境-部署图》")
            self.assertEqual(unbound["semantic_status"], "source_binding_missing")
            self.assertEqual(unbound["matched_title_signals"], [])

    def test_vsdx_requires_short_chinese_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "订单系统部署架构图.vsdx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("visio/document.xml", "<VisioDocument />")
                archive.writestr(
                    "visio/pages/page1.xml",
                    "<PageContents><Shapes><Shape><Text>完全无关内容</Text></Shape></Shapes></PageContents>",
                )
            self.assertEqual(MODULE.title_signals("订单系统部署架构图"), ["订单"])
            inspected = MODULE.inspect_vsdx(path, "订单系统部署架构图")
            self.assertEqual(inspected["semantic_status"], "source_binding_missing")

    def test_vsdx_accepts_two_non_overlapping_chinese_bigram_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "数字化柜面状态流传.vsdx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("visio/document.xml", "<VisioDocument />")
                archive.writestr(
                    "visio/pages/page1.xml",
                    "<PageContents><Shapes><Shape><Text>任务状态</Text></Shape>"
                    "<Shape><Text>柜面视频身份核验标识</Text></Shape></Shapes></PageContents>",
                )
            inspected = MODULE.inspect_vsdx(path, "数字化柜面状态流传")
            self.assertEqual(inspected["semantic_status"], "matched")
            self.assertEqual(inspected["semantic_match_method"], "chinese_bigram_pair")
            self.assertEqual(inspected["matched_title_signals"], ["柜面", "状态"])

    def test_vsdx_reports_missing_binding_for_one_or_overlapping_chinese_bigram_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "compound.vsdx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("visio/document.xml", "<VisioDocument />")
                archive.writestr(
                    "visio/pages/page1.xml",
                    "<PageContents><Shapes><Shape><Text>柜面服务</Text></Shape></Shapes></PageContents>",
                )
            inspected = MODULE.inspect_vsdx(path, "数字化柜面状态流传")
            self.assertEqual(inspected["semantic_status"], "source_binding_missing")
            self.assertEqual(MODULE.matched_chinese_bigram_pair("数字化", "数字字化"), [])

    def test_vsdx_blocks_plaintext_credentials_without_echoing_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "MongoDB使用关系图.vsdx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("visio/document.xml", "<VisioDocument />")
                archive.writestr(
                    "visio/pages/page1.xml",
                    "<PageContents><Shapes><Shape><Text>MongoDB 密码：do-not-log-this</Text></Shape>"
                    "<Shape><Text>password=also-secret</Text></Shape></Shapes></PageContents>",
                )
            with self.assertRaises(MODULE.BatchError) as caught:
                MODULE.inspect_vsdx(path, "MongoDB使用关系图")
            message = str(caught.exception)
            self.assertIn("security review required", message)
            self.assertIn("chinese_password_assignment=1", message)
            self.assertIn("english_password_assignment=1", message)
            self.assertNotIn("do-not-log-this", message)
            self.assertNotIn("also-secret", message)

    def test_secret_scan_does_not_block_non_assignment_security_terms(self):
        self.assertEqual(
            MODULE.sensitive_text_findings(["token验证", "password policy", "修改密码"]),
            [],
        )

    def test_vsdx_blocks_presigned_object_storage_urls_without_echoing_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "文件上传流程.vsdx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("visio/document.xml", "<VisioDocument />")
                archive.writestr(
                    "visio/pages/page1.xml",
                    "<PageContents><Shapes><Shape><Text>"
                    "https://object.example/file?X-Amz-Credential=opaque-value&amp;X-Amz-Signature=opaque-signature"
                    "</Text></Shape></Shapes></PageContents>",
                )
            with self.assertRaises(MODULE.BatchError) as caught:
                MODULE.inspect_vsdx(path, "文件上传流程")
            message = str(caught.exception)
            self.assertIn("security review required", message)
            self.assertIn("aws_presigned_url_parameter=2", message)
            self.assertNotIn("opaque-value", message)
            self.assertNotIn("opaque-signature", message)

    def test_vsdx_security_redaction_preserves_original_and_passes_rescan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.vsdx"
            destination = root / "sanitized.vsdx"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("visio/document.xml", "<VisioDocument />")
                archive.writestr(
                    "visio/pages/page1.xml",
                    "<PageContents><Shapes><Shape><Text>MongoDB使用关系图 密码：private-one</Text></Shape>"
                    "<Shape><Text>password=private-two</Text></Shape>"
                    "<Shape><Text>https://object.example/file?X-Amz-Credential=private-three&amp;"
                    "X-Amz-Signature=private-four</Text></Shape></Shapes></PageContents>",
                )
            original_sha = MODULE.sha256(source)
            report = MODULE.sanitize_vsdx_sensitive_text(source, destination)
            self.assertEqual(MODULE.sha256(source), original_sha)
            self.assertEqual(report["status"], "sanitized_derivative")
            self.assertEqual(
                report["redaction_counts"],
                {
                    "chinese_password_assignment": 1,
                    "english_password_assignment": 1,
                    "aws_presigned_url_parameter": 2,
                },
            )
            inspected = MODULE.inspect_vsdx(destination, "MongoDB使用关系图")
            self.assertEqual(inspected["semantic_status"], "matched")
            sanitized_bytes = destination.read_bytes()
            for secret in (b"private-one", b"private-two", b"private-three", b"private-four"):
                self.assertNotIn(secret, sanitized_bytes)

    def test_dotted_release_number_separates_chinese_title_signals(self):
        self.assertEqual(
            MODULE.title_signals("《磐石4.0短信系统部署架构图-生产环境》"),
            ["磐石", "短信"],
        )

    def test_concurrency_requires_matching_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = {
                "entries": [
                    {
                        "artifact_id": "a",
                        "title": "Alpha",
                        "source_url": "https://www.processon.com/diagraming/a",
                        "remote_id": "a",
                        "primary_format": "vsdx",
                    },
                    {
                        "artifact_id": "b",
                        "title": "Beta",
                        "source_url": "https://www.processon.com/diagraming/b",
                        "remote_id": "b",
                        "primary_format": "vsdx",
                    },
                ]
            }
            completed = []
            samples = []
            for artifact_id, title in (("a", "Alpha"), ("b", "Beta")):
                folder = root / artifact_id
                folder.mkdir()
                destination = folder / f"{title}.vsdx"
                with zipfile.ZipFile(destination, "w") as archive:
                    archive.writestr("visio/document.xml", "<VisioDocument />")
                    archive.writestr(
                        "visio/pages/page1.xml",
                        f"<PageContents><Shapes><Shape><Text>{title}</Text></Shape></Shapes></PageContents>",
                    )
                digest = MODULE.sha256(destination)
                source_url = f"https://www.processon.com/diagraming/{artifact_id}"
                (folder / "metadata.yml").write_text(
                    "\n".join(
                        [
                            f'artifact_id: "{artifact_id}"',
                            f'title: "{title}"',
                            f'source_url: "{source_url}"',
                            f'remote_id: "{artifact_id}"',
                            f'sha256: "{digest}"',
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
                completed.append(
                    {
                        "artifact_id": artifact_id,
                        "archive_destination": str(destination),
                        "sha256": digest,
                    }
                )
                samples.append(
                    {
                        "artifact_id": artifact_id,
                        "title": title,
                        "source_url": source_url,
                        "remote_id": artifact_id,
                        "sha256": digest,
                        "semantic_status": "matched",
                    }
                )
            progress = {"plan": {"sha256": "abc"}, "completed": completed}
            with self.assertRaises(MODULE.BatchError):
                MODULE.validate_concurrency_proof(
                    None, workers=2, plan=plan, progress=progress
                )
            proof = root / "proof.json"
            proof.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "passed",
                        "plan_sha256": "abc",
                        "max_workers": 2,
                        "samples": samples,
                        "lifecycle": {
                            "scoped_pages_opened": 2,
                            "scoped_pages_closed": 2,
                            "worker_pages_opened": 2,
                            "worker_pages_closed": 2,
                            "pages_remaining": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = MODULE.validate_concurrency_proof(
                proof, workers=2, plan=plan, progress=progress
            )
            self.assertEqual(result["max_workers"], 2)
            payload = json.loads(proof.read_text(encoding="utf-8"))
            payload["samples"][0]["source_url"] = "https://www.processon.com/diagraming/evil"
            payload["samples"][0]["remote_id"] = "evil"
            proof.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(MODULE.BatchError):
                MODULE.validate_concurrency_proof(
                    proof, workers=2, plan=plan, progress=progress
                )
            payload["samples"][0]["source_url"] = "https://www.processon.com/diagraming/a"
            payload["samples"][0]["remote_id"] = "a"
            payload["lifecycle"]["worker_pages_opened"] = 1
            proof.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(MODULE.BatchError):
                MODULE.validate_concurrency_proof(
                    proof, workers=2, plan=plan, progress=progress
                )
            payload["lifecycle"]["worker_pages_opened"] = 2
            proof.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(MODULE.BatchError):
                MODULE.validate_concurrency_proof(
                    proof, workers=3, plan=plan, progress=progress
                )

    def test_concurrency_rejects_duplicate_samples(self):
        progress = {"plan": {"sha256": "abc"}}
        with tempfile.TemporaryDirectory() as tmp:
            proof = Path(tmp) / "proof.json"
            sample = {
                "artifact_id": "same",
                "source_url": "https://www.processon.com/diagraming/same",
                "sha256": "same-sha",
                "semantic_status": "matched",
            }
            proof.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "passed",
                        "plan_sha256": "abc",
                        "max_workers": 2,
                        "samples": [sample, sample],
                        "lifecycle": {
                            "scoped_pages_opened": 2,
                            "scoped_pages_closed": 2,
                            "worker_pages_closed": 2,
                            "pages_remaining": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(MODULE.BatchError):
                MODULE.validate_concurrency_proof(
                    proof, workers=2, plan={"entries": []}, progress=progress
                )

    def test_source_popup_identity_must_match_plan(self):
        entry = self.entry("a")
        entry["remote_id"] = "remote-a"
        entry["source_url"] = "https://www.processon.com/diagraming/remote-a"
        observed = "https://www.processon.com/diagraming/remote-a/"
        self.assertEqual(MODULE.verify_source_identity(entry, observed), "remote-a")
        with self.assertRaises(MODULE.BatchError):
            MODULE.verify_source_identity(
                entry, "https://www.processon.com/diagraming/remote-b"
            )

    def test_zip_member_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unsafe.vsdx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("../escape", "no")
                archive.writestr("visio/document.xml", "<VisioDocument />")
                archive.writestr("visio/pages/page1.xml", "<PageContents />")
            with self.assertRaises(MODULE.BatchError):
                MODULE.inspect_vsdx(path, "订单系统部署架构图")

    @unittest.skipIf(os.name == "nt", "symlink privileges vary on Windows")
    def test_lock_rejects_symlink_without_touching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            victim = root / "victim.txt"
            victim.write_text("KEEP-ME", encoding="utf-8")
            lock = root / "lock"
            lock.symlink_to(victim)
            with self.assertRaises(MODULE.BatchError):
                with MODULE.exclusive_lock(lock):
                    pass
            self.assertEqual(victim.read_text(encoding="utf-8"), "KEEP-ME")

    @unittest.skipIf(os.name == "nt", "hard-link semantics vary on Windows")
    def test_lock_rejects_hardlink_without_touching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            victim = root / "victim.txt"
            victim.write_text("KEEP-ME", encoding="utf-8")
            lock = root / "lock"
            os.link(victim, lock)
            with self.assertRaises(MODULE.BatchError):
                with MODULE.exclusive_lock(lock):
                    pass
            self.assertEqual(victim.read_text(encoding="utf-8"), "KEEP-ME")

    def test_progress_mirror_reports_complete_and_waiting_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "progress.yml"
            plan = {"entries": [], "counts": {"total_entries": 0}}
            progress = {
                "plan": {"sha256": "abc"},
                "counts": {
                    "planned_known": 1,
                    "completed": 1,
                    "failed": 0,
                    "blocked": 0,
                    "remaining_known": 0,
                    "unknown_pending_confirmation": 0,
                },
                "completed": [],
                "blocked": [],
            }
            MODULE.write_progress_mirror(path, plan=plan, progress=progress, run_id="run")
            self.assertIn('status: "asset_archive_completed"', path.read_text(encoding="utf-8"))
            progress["counts"].update(
                {"blocked": 1, "remaining_known": 1, "unknown_pending_confirmation": 1}
            )
            MODULE.write_progress_mirror(path, plan=plan, progress=progress, run_id="run")
            self.assertIn('status: "asset_archive_running"', path.read_text(encoding="utf-8"))

    def test_source_link_conflict_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source-links.yml"
            path.write_text(
                'schema_version: 1\nentries:\n  - artifact_id: "a"\n    source_url: "https://www.processon.com/diagraming/one"\n',
                encoding="utf-8",
            )
            entry = self.entry("a")
            with self.assertRaises(MODULE.BatchError):
                MODULE.append_source_link(
                    path,
                    entry,
                    {
                        "source_url": "https://www.processon.com/diagraming/two",
                        "remote_id": "two",
                    },
                )

    def test_source_link_rejects_same_url_for_another_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source-links.yml"
            path.write_text(
                'schema_version: 1\nentries:\n  - artifact_id: "a"\n'
                '    source_url: "https://www.processon.com/diagraming/one"\n',
                encoding="utf-8",
            )
            entry = self.entry("b")
            with self.assertRaisesRegex(MODULE.BatchError, "another artifact"):
                MODULE.append_source_link(
                    path,
                    entry,
                    {
                        "source_url": "https://www.processon.com/diagraming/one/",
                        "remote_id": "one",
                    },
                )

    def test_output_folder_contains_collision_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = self.entry("abcdef012345", "duplicate_title")
            target = MODULE.output_folder(Path(tmp), entry)
            self.assertEqual(target.name, "abcdef012345--abcdef01")

    def test_output_folder_treats_title_separator_as_one_escaped_component(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = self.entry("abcdef012345")
            entry["source_directory"] = "team/system"
            entry["source_path"] = "team/system/中介/银保手续费"
            entry["title"] = "中介/银保手续费"
            entry["collision_risk"] = "none_detected"
            target = MODULE.output_folder(Path(tmp), entry)
            self.assertEqual(target.parent.name, "system")
            self.assertEqual(target.name, "中介_银保手续费--abcdef01")

    def test_same_download_name_uses_artifact_specific_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = MODULE.safe_download_path(Path(tmp), "artifact-a", "未命名文件.vsdx")
            second = MODULE.safe_download_path(Path(tmp), "artifact-b", "未命名文件.vsdx")
            self.assertNotEqual(first.parent, second.parent)
            self.assertEqual(first.name, second.name)
            self.assertEqual(first.parent.name, "artifact-a")
            self.assertEqual(second.parent.name, "artifact-b")

    def test_finalize_result_moves_from_managed_staging_without_payload_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            managed = root / "staging"
            output = root / "output"
            manifests = root / "manifests"
            plan_path = root / "archive-plan.json"
            progress_path = root / "download-progress.json"
            entry = self.entry("deployment")
            archive_plan = {
                "schema_version": 1,
                "plan_type": "processon-artifact-archive",
                "archive_status": "known_ready",
                "ready_for_known_artifacts": True,
                "ready_for_archive": True,
                "counts": {
                    "total": 1,
                    "flowchart": 1,
                    "mindmap": 0,
                    "unknown": 0,
                    "pending_confirmation": 0,
                },
                "entries": [entry],
            }
            plan_path.write_text(json.dumps(archive_plan), encoding="utf-8")
            MODULE.run_json(
                [
                    sys.executable,
                    str(MODULE.FINALIZER),
                    "paths",
                    "--temp-dir",
                    str(managed),
                    "--output-dir",
                    str(output),
                    "--manifest-dir",
                    str(manifests),
                    "--ensure",
                ]
            )
            MODULE.run_json(
                [
                    sys.executable,
                    str(MODULE.ARCHIVE_STATE),
                    "init",
                    "--plan",
                    str(plan_path),
                    "--progress",
                    str(progress_path),
                ]
            )
            source = managed / "run" / entry["artifact_id"] / "deployment.vsdx"
            source.parent.mkdir(parents=True)
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types />")
                archive.writestr("visio/document.xml", "<VisioDocument />")
                archive.writestr("visio/pages/pages.xml", "<Pages />")
                archive.writestr(
                    "visio/pages/page1.xml",
                    "<PageContents><Shapes><Shape><Text>deployment</Text></Shape></Shapes></PageContents>",
                )
            source_inode = source.stat().st_ino
            args = argparse.Namespace(
                output_root=output,
                manifest_dir=manifests,
                managed_temp_root=managed,
                team_url="https://www.processon.com/org/teams/team-id",
                source_links=None,
                plan=plan_path,
                progress=progress_path,
            )
            result = MODULE.finalize_result(
                {
                    "download": {"path": str(source)},
                    "source_url": "https://www.processon.com/diagraming/remote-id",
                    "remote_id": "remote-id",
                    "download_menu": "导出全部画布 (.vsdx)",
                },
                entry,
                args=args,
            )
            destination = Path(result["destination"])
            self.assertFalse(source.exists())
            self.assertTrue(destination.is_file())
            self.assertEqual(destination.stat().st_ino, source_inode)
            self.assertTrue(Path(result["metadata"]).is_file())
            self.assertIn(
                'download_menu: "导出全部画布 (.vsdx)"',
                Path(result["metadata"]).read_text(encoding="utf-8"),
            )
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["operation"], "move")
            self.assertEqual(manifest["transfer_mode"], "hardlink_then_unlink")
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
            self.assertEqual(progress["counts"]["completed"], 1)
            self.assertEqual(progress["completed"][0]["download_source"], str(source.resolve()))

    def test_structurally_valid_unbound_vsdx_is_blocked_with_private_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_path = root / "archive-plan.json"
            progress_path = root / "download-progress.json"
            entry = self.entry("unbound")
            archive_plan = {
                "schema_version": 1,
                "plan_type": "processon-artifact-archive",
                "archive_status": "known_ready",
                "ready_for_known_artifacts": True,
                "ready_for_archive": True,
                "counts": {
                    "total": 1,
                    "flowchart": 1,
                    "mindmap": 0,
                    "unknown": 0,
                    "pending_confirmation": 0,
                },
                "entries": [entry],
            }
            plan_path.write_text(json.dumps(archive_plan), encoding="utf-8")
            MODULE.run_json(
                [
                    sys.executable,
                    str(MODULE.ARCHIVE_STATE),
                    "init",
                    "--plan",
                    str(plan_path),
                    "--progress",
                    str(progress_path),
                ]
            )
            source = root / "staging" / "unbound" / "unbound.vsdx"
            source.parent.mkdir(parents=True)
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("visio/document.xml", "<VisioDocument />")
                archive.writestr(
                    "visio/pages/page1.xml",
                    "<PageContents><Shapes><Shape><Text>unrelated content</Text></Shape></Shapes></PageContents>",
                )
            inspection = MODULE.inspect_download(source, entry)
            self.assertEqual(inspection["semantic_status"], "source_binding_missing")
            blocked = MODULE.block_structurally_valid_unbound_vsdx(
                {
                    "source_url": "https://www.processon.com/diagraming/runtime-only-id",
                    "remote_id": "runtime-only-id",
                    "download": {
                        "path": str(source),
                        "suggested_filename": "unbound.vsdx",
                    },
                },
                entry,
                inspection,
                args=argparse.Namespace(plan=plan_path, progress=progress_path),
            )
            self.assertEqual(blocked["status"], "blocked")
            self.assertTrue(source.is_file())
            diagnostic = json.loads(Path(blocked["diagnostic"]).read_text(encoding="utf-8"))
            self.assertFalse(diagnostic["source_identity_plan_bound"])
            self.assertEqual(
                diagnostic["kind"], "content_structure_verified_source_binding_missing"
            )
            state = json.loads(progress_path.read_text(encoding="utf-8"))
            self.assertEqual(state["counts"]["completed"], 0)
            self.assertEqual(state["counts"]["blocked"], 1)
            evidence = state["blocked"][0]["evidence_files"]
            self.assertEqual(len(evidence), 2)
            self.assertTrue(all(Path(item["archived_path"]).is_file() for item in evidence))
            audit = MODULE.run_json(
                [
                    sys.executable,
                    str(MODULE.ARCHIVE_STATE),
                    "audit",
                    "--plan",
                    str(plan_path),
                    "--progress",
                    str(progress_path),
                ]
            )
            self.assertEqual(audit["status"], "passed")

    def test_legacy_flat_download_review_revalidates_every_flat_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            downloads = home / "Downloads"
            downloads.mkdir()
            progress = {
                "completed": [
                    {
                        "artifact_id": "a",
                        "source_path": "root/a",
                        "download_source": str(downloads / "未命名文件 (2).vsdx"),
                        "archive_destination": "/archive/a",
                    },
                    {
                        "artifact_id": "b",
                        "source_path": "root/b",
                        "download_source": str(downloads / "唯一名称.vsdx"),
                        "archive_destination": "/archive/b",
                    },
                    {
                        "artifact_id": "c",
                        "source_path": "root/c",
                        "download_source": str(home / "managed" / "同名.vsdx"),
                        "archive_destination": "/archive/c",
                    },
                ]
            }
            with patch.object(Path, "home", return_value=home):
                review = MODULE.legacy_flat_download_review(progress)
            self.assertEqual(review["flat_downloads_completed_count"], 2)
            self.assertEqual(review["revalidation_required_count"], 2)
            self.assertEqual(review["numbered_suffix_review_count"], 1)
            self.assertEqual(review["trusted_completed_count"], 1)
            self.assertEqual(review["claim_status"], "revalidation_required")
            self.assertEqual(
                [item["artifact_id"] for item in review["revalidation_items"]], ["a", "b"]
            )
            self.assertEqual(review["numbered_suffix_items"][0]["artifact_id"], "a")

    def test_progress_mirror_excludes_legacy_numbered_download_from_trusted_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            downloads = home / "Downloads"
            downloads.mkdir()
            mirror = home / "archive-progress.yml"
            plan = {"entries": [], "counts": {"total_entries": 2}}
            progress = {
                "plan": {"sha256": "abc"},
                "counts": {
                    "planned_known": 2,
                    "completed": 2,
                    "failed": 0,
                    "blocked": 0,
                    "remaining_known": 0,
                    "unknown_pending_confirmation": 0,
                },
                "completed": [
                    {
                        "artifact_id": "unsafe",
                        "source_path": "root/unsafe",
                        "actual_format": "vsdx",
                        "download_source": str(downloads / "未命名文件 (2).vsdx"),
                        "archive_destination": str(home / "archive" / "unsafe.vsdx"),
                    },
                    {
                        "artifact_id": "safe",
                        "source_path": "root/safe",
                        "actual_format": "vsdx",
                        "download_source": str(home / "managed" / "safe" / "同名.vsdx"),
                        "archive_destination": str(home / "archive" / "safe.vsdx"),
                    },
                ],
                "blocked": [],
            }
            with patch.object(Path, "home", return_value=home):
                MODULE.write_progress_mirror(mirror, plan=plan, progress=progress, run_id="run")
            text = mirror.read_text(encoding="utf-8")
            self.assertIn("completed: 1", text)
            self.assertIn("completed_recorded: 2", text)
            self.assertIn("revalidation_pending: 1", text)
            self.assertIn("legacy_flat_revalidation_pending: 1", text)
            self.assertIn("remaining_known: 1", text)
            self.assertIn("remaining_known_recorded: 0", text)
            self.assertIn('artifact_id: "unsafe"', text)

    def test_progress_mirror_does_not_double_count_explicit_revalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mirror = root / "archive-progress.yml"
            plan = {"entries": [], "counts": {"total_entries": 2}}
            progress = {
                "plan": {"sha256": "abc"},
                "counts": {
                    "planned_known": 2,
                    "completed": 1,
                    "failed": 0,
                    "blocked": 0,
                    "revalidation_pending": 1,
                    "remaining_known": 1,
                    "unknown_pending_confirmation": 0,
                },
                "completed": [
                    {
                        "artifact_id": "safe",
                        "source_path": "root/safe",
                        "actual_format": "vsdx",
                        "download_source": str(root / "staging" / "safe" / "safe.vsdx"),
                        "archive_destination": str(root / "archive" / "safe.vsdx"),
                    }
                ],
                "revalidation_pending": [
                    {
                        "artifact_id": "reopen",
                        "source_path": "root/reopen",
                        "reason": "legacy flat source",
                        "prior_completion": {
                            "download_source": str(root / "Downloads" / "same.vsdx")
                        },
                    }
                ],
                "blocked": [],
            }
            MODULE.write_progress_mirror(mirror, plan=plan, progress=progress, run_id="run")
            text = mirror.read_text(encoding="utf-8")
            self.assertIn("completed: 1", text)
            self.assertIn("revalidation_pending: 1", text)
            self.assertIn("explicit_revalidation_pending: 1", text)
            self.assertIn("legacy_flat_revalidation_pending: 0", text)
            self.assertIn("remaining_known: 1", text)

    def test_provider_title_suffix_is_deliberately_narrow(self):
        title = "企业知识库"
        self.assertTrue(MODULE.source_title_matches(title, title))
        self.assertTrue(MODULE.source_title_matches(title, f"{title}-ProcessOn"))
        self.assertFalse(MODULE.source_title_matches(title, f"{title}-副本"))

    def test_provider_filename_sanitization_is_deliberately_narrow(self):
        title = "《蚁窠-中介/银保手续费/可用费用-系统交互图》"
        self.assertEqual(
            MODULE.provider_safe_filename_stem(title),
            "《蚁窠-中介_银保手续费_可用费用-系统交互图》",
        )
        self.assertNotEqual(
            MODULE.provider_safe_filename_stem(title), title.replace("中介", "中介平台")
        )

    def test_provider_filename_sanitization_handles_observed_pipe_replacement(self):
        title = "《5.登出流程（3.0||4.0令牌失效）》"
        self.assertEqual(
            MODULE.provider_safe_filename_stem(title),
            "《5.登出流程（3.0__4.0令牌失效）》",
        )


if __name__ == "__main__":
    unittest.main()
