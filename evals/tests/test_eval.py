from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from evals import eval as harness


class EvaluationHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = harness.load_and_validate_fixtures()
        cls.rules_text = harness.read_text(harness.RULES_PATH)
        cls.executable = Path("C:/test/claude.exe")

    def test_frozen_fixture_set_is_disclosed_and_valid(self) -> None:
        self.assertEqual(len(self.bundle["fixtures"]), 12)
        self.assertEqual(harness.fixture_validation_errors(self.bundle), [])
        for fixture in self.bundle["fixtures"]:
            self.assertEqual(fixture["provenance"]["kind"], "synthetic")
            self.assertTrue(fixture["provenance"]["not_a_recorded_exchange"])
            self.assertTrue(fixture["allowed_facts"])
            self.assertTrue(fixture["unsupported_conclusions"])

    def test_surface_metrics_are_literal_and_deterministic(self) -> None:
        text = (
            "Great question! Let's break this down.\n\n"
            "## Recommendation\n\n"
            "- One item—really.\n\n"
            "Let me know if you'd like more!"
        )
        first = harness.surface_metrics(text)
        second = harness.surface_metrics(text)
        self.assertEqual(first, second)
        self.assertEqual(first["headings_surface"], 1)
        self.assertEqual(first["list_items_surface"], 1)
        self.assertEqual(first["punctuation"]["em_dash"], 1)
        self.assertEqual(first["punctuation"]["exclamation"], 2)
        self.assertEqual(first["pattern_matches"]["praise_opener"], 1)
        self.assertEqual(first["pattern_matches"]["template_roadmap"], 1)
        self.assertEqual(first["pattern_matches"]["closing_offer"], 1)

    def test_command_builder_changes_only_by_treatment_append(self) -> None:
        prompt = self.bundle["fixtures"][0]["prompt"]
        baseline = harness.build_claude_argv(
            self.executable,
            prompt=prompt,
            model="model-under-test",
            effort="low",
            condition="baseline",
            rules_text=self.rules_text,
        )
        treatment = harness.build_claude_argv(
            self.executable,
            prompt=prompt,
            model="model-under-test",
            effort="low",
            condition="treatment",
            rules_text=self.rules_text,
        )
        self.assertNotIn("--append-system-prompt", baseline)
        self.assertIn("--append-system-prompt", treatment)
        append_index = treatment.index("--append-system-prompt")
        self.assertEqual(treatment[append_index + 1], self.rules_text)
        self.assertEqual(baseline[-1], prompt)
        self.assertEqual(treatment[-1], prompt)
        for required in (
            "--safe-mode",
            "--no-session-persistence",
            "--no-chrome",
            "--strict-mcp-config",
            "--tools",
        ):
            self.assertIn(required, baseline)

    def test_call_plan_has_two_randomized_conditions_per_fixture(self) -> None:
        plan = harness.make_call_plan(
            self.bundle["fixtures"],
            seed=7,
            executable=self.executable,
            model="model-under-test",
            effort="low",
            rules_text=self.rules_text,
        )
        self.assertEqual(len(plan), 24)
        for fixture in self.bundle["fixtures"]:
            calls = [item for item in plan if item["fixture_id"] == fixture["id"]]
            self.assertEqual({item["condition"] for item in calls}, {"baseline", "treatment"})
            self.assertEqual({item["blind_label"] for item in calls}, {"A", "B"})

    def _complete_fake_run(self) -> tuple[dict, list[dict]]:
        fixtures_text = harness.read_text(harness.DEFAULT_FIXTURES_PATH)
        plan = harness.make_call_plan(
            self.bundle["fixtures"],
            seed=11,
            executable=self.executable,
            model="model-under-test",
            effort="low",
            rules_text=self.rules_text,
        )
        manifest = {
            "schema_version": 1,
            "run_id": "test-run",
            "kind": "captured_claude_cli_pair_run",
            "status": "completed",
            "capture_started_at_utc": "2026-01-01T00:00:00Z",
            "capture_completed_at_utc": "2026-01-01T00:01:00Z",
            "repo": {"commit": "a" * 40, "dirty": False},
            "fixtures_sha256": harness.sha256_text(fixtures_text),
            "fixtures_text": fixtures_text,
            "fixture_bundle": self.bundle,
            "rules_sha256": harness.sha256_text(self.rules_text),
            "rules_text": self.rules_text,
            "claude_cli": {
                "executable": str(self.executable),
                "version_call": {
                    "argv": [str(self.executable), "--version"],
                    "stdout": "test-cli 1.0",
                    "stderr": "",
                    "return_code": 0,
                },
            },
            "settings": {
                "requested_model": "model-under-test",
                "effort": "low",
                "timeout_seconds_per_call": 180,
                "random_seed": 11,
                "safe_mode": True,
                "new_empty_working_directory_per_call": True,
                "session_persistence": False,
                "tools": "disabled",
                "chrome": False,
                "strict_mcp_config": True,
                "prompt_suggestions": False,
                "output_format": "json",
                "condition_baseline": "Claude Code built-in prompt under the recorded CLI invocation",
                "condition_treatment": "Baseline plus exact rules_text through --append-system-prompt",
            },
            "planned_calls": plan,
            "attempted_call_count": 24,
            "successful_capture_count": 24,
        }
        fixture_by_id = {item["id"]: item for item in self.bundle["fixtures"]}
        records = []
        for call in plan:
            fixture = fixture_by_id[call["fixture_id"]]
            response = f"Captured {call['blind_label']} for {call['fixture_id']}."
            parsed = {
                "result": response,
                "is_error": False,
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "modelUsage": {"returned-model-id": {"outputTokens": 5}},
            }
            raw_stdout = json.dumps(parsed, separators=(",", ":"))
            base_record = {
                "started_at_utc": "2026-01-01T00:00:00Z",
                "ended_at_utc": "2026-01-01T00:00:01Z",
                "duration_ms": 1000,
                "isolated_working_directory": {
                    "path_during_call": "C:/Users/example/AppData/Local/Temp/eval",
                    "was_new_and_empty": True,
                    "removed_after_call": True,
                },
                "process": {
                    "launched": True,
                    "return_code": 0,
                    "timed_out": False,
                    "timeout_seconds": 180,
                },
                "launch_error": None,
                "raw_stdout": raw_stdout,
                "raw_stderr": "",
            }
            records.append(
                harness.recomputed_record(
                    base_record,
                    manifest=manifest,
                    fixture=fixture,
                    planned_call=call,
                )
            )
        return manifest, records

    def test_review_template_hides_conditions_and_is_not_a_review(self) -> None:
        manifest, records = self._complete_fake_run()
        template = harness.make_review_template(manifest, records)
        self.assertEqual(template["status"], "template_not_reviewed")
        self.assertEqual({item["blind_label"] for item in template["pairs"][0]["outputs"]}, {"A", "B"})
        self.assertTrue(all("condition" not in item for item in template["pairs"][0]["outputs"]))
        errors = harness.review_validation_errors(template, manifest, records)
        self.assertTrue(any("status" in error for error in errors))
        self.assertTrue(any("answered_request" in error for error in errors))

    def _completed_review(self, manifest: dict, records: list[dict]) -> dict:
        review = harness.make_review_template(manifest, records)
        review["status"] = "completed"
        review["reviewer"] = "test reviewer"
        review["reviewed_at_utc"] = "2026-01-01T01:00:00Z"
        for pair in review["pairs"]:
            pair["preferred_output"] = "tie"
            pair["preference_reason"] = "Test fixture."
            for output in pair["outputs"]:
                output["answered_request"] = "yes"
                output["depth"] = "appropriate"
                for point in output["required_points"]:
                    point["met"] = True
                for conclusion in output["unsupported_conclusions"]:
                    conclusion["present"] = False
        return review

    def test_integrity_validation_detects_changed_response(self) -> None:
        manifest, records = self._complete_fake_run()
        fixture_by_id = {item["id"]: item for item in self.bundle["fixtures"]}
        changed = copy.deepcopy(records[0])
        changed["response_text"] = "Edited after capture."
        errors = harness.validate_record_integrity(
            changed, manifest=manifest, fixture_by_id=fixture_by_id
        )
        self.assertTrue(any("response_text differs from recomputed evidence" in error for error in errors))

    def test_record_mutations_are_rejected(self) -> None:
        manifest, records = self._complete_fake_run()
        fixture_by_id = {item["id"]: item for item in self.bundle["fixtures"]}
        treatment_index = next(
            index for index, record in enumerate(records) if record["condition"] == "treatment"
        )
        mutations = {
            "full argv": lambda item: item["argv"].append("--unexpected"),
            "argv hash": lambda item: item.__setitem__("argv_sha256", "0" * 64),
            "blind label": lambda item: item.__setitem__(
                "blind_label", "B" if item["blind_label"] == "A" else "A"
            ),
            "capture status": lambda item: item.__setitem__("capture_ok", False),
            "usage": lambda item: item.__setitem__("usage", {"output_tokens": 999}),
            "returned models": lambda item: item.__setitem__(
                "returned_model_identifiers", ["forged-model"]
            ),
            "response hash": lambda item: item.__setitem__(
                "response_text_sha256", "f" * 64
            ),
            "rules hash": lambda item: item.__setitem__("rules_sha256", "e" * 64),
            "parsed json": lambda item: item.__setitem__("parsed_json", None),
            "model usage": lambda item: item.__setitem__("model_usage", {}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                index = treatment_index if name == "rules hash" else 0
                changed = copy.deepcopy(records[index])
                mutate(changed)
                errors = harness.validate_record_integrity(
                    changed, manifest=manifest, fixture_by_id=fixture_by_id
                )
                self.assertTrue(errors, name)

    def test_manifest_and_plan_mutations_are_rejected(self) -> None:
        manifest, records = self._complete_fake_run()
        mutations = {
            "plan argv hash": lambda item: item["planned_calls"][0].__setitem__(
                "argv_sha256", "0" * 64
            ),
            "plan blind label": lambda item: item["planned_calls"][0].__setitem__(
                "blind_label", "B" if item["planned_calls"][0]["blind_label"] == "A" else "A"
            ),
            "requested model": lambda item: item["settings"].__setitem__(
                "requested_model", "forged-model"
            ),
            "effort": lambda item: item["settings"].__setitem__("effort", "high"),
            "executable": lambda item: item["claude_cli"].__setitem__(
                "executable", "C:/forged/claude.exe"
            ),
            "rules hash": lambda item: item.__setitem__("rules_sha256", "0" * 64),
            "attempt count": lambda item: item.__setitem__("attempted_call_count", 23),
            "success count": lambda item: item.__setitem__("successful_capture_count", 0),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                changed = copy.deepcopy(manifest)
                mutate(changed)
                errors, _warnings = harness.run_validation_errors(
                    changed, records, require_complete=False
                )
                self.assertTrue(errors, name)

    def test_duplicate_human_review_pair_is_rejected(self) -> None:
        manifest, records = self._complete_fake_run()
        review = self._completed_review(manifest, records)
        review["pairs"].append(copy.deepcopy(review["pairs"][0]))
        errors = harness.review_validation_errors(review, manifest, records)
        self.assertTrue(any("duplicate fixture ids" in error for error in errors))

    def test_launch_errors_become_explicit_failed_records(self) -> None:
        fixture = self.bundle["fixtures"][0]
        call = harness.make_call_plan(
            [fixture],
            seed=3,
            executable=self.executable,
            model="model-under-test",
            effort="low",
            rules_text=self.rules_text,
        )[0]
        failures = (
            FileNotFoundError(2, "missing executable"),
            PermissionError(13, "permission denied"),
            OSError(193, "invalid executable format"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(harness.subprocess, "run", side_effect=failure):
                    record = harness.capture_one(
                        run_id="launch-test",
                        call=call,
                        fixture=fixture,
                        executable=self.executable,
                        model="model-under-test",
                        effort="low",
                        rules_text=self.rules_text,
                        rules_sha256=harness.sha256_text(self.rules_text),
                        timeout_seconds=180,
                    )
                self.assertFalse(record["capture_ok"])
                self.assertEqual(record["launch_error"]["type"], type(failure).__name__)
                self.assertFalse(record["process"]["launched"])
                self.assertIsNone(record["process"]["return_code"])
                self.assertEqual(record["raw_stdout"], "")

    def test_capture_finalizes_manifest_after_permission_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary) / "runs"
            args = SimpleNamespace(
                fixtures=str(harness.DEFAULT_FIXTURES_PATH),
                model="model-under-test",
                effort="low",
                claude="claude",
                seed=17,
                timeout=180,
                run_id="permission-failure-run",
                dry_run=False,
                allow_dirty=True,
            )
            with (
                mock.patch.object(harness, "RUNS_DIR", run_root),
                mock.patch.object(harness, "resolve_cli", return_value=self.executable),
                mock.patch.object(
                    harness,
                    "cli_version",
                    return_value={
                        "argv": [str(self.executable), "--version"],
                        "stdout": "test-cli 1.0",
                        "stderr": "",
                        "return_code": 0,
                    },
                ),
                mock.patch.object(
                    harness,
                    "git_repo_state",
                    return_value={"commit": "a" * 40, "dirty": False, "status_porcelain": ""},
                ),
                mock.patch.object(
                    harness.subprocess,
                    "run",
                    side_effect=PermissionError(13, "permission denied"),
                ),
                mock.patch("builtins.print"),
            ):
                result = harness.command_capture(args)
            self.assertEqual(result, 1)
            manifest, records = harness.load_run(run_root / args.run_id)
            self.assertEqual(manifest["status"], "completed_with_failures")
            self.assertEqual(manifest["attempted_call_count"], 24)
            self.assertEqual(manifest["successful_capture_count"], 0)
            self.assertEqual(len(records), 24)
            self.assertTrue(all(record["launch_error"] for record in records))
            errors, warnings = harness.run_validation_errors(
                manifest, records, require_complete=False
            )
            self.assertEqual(errors, [])
            self.assertTrue(warnings)

    def test_public_copy_removes_paths_and_secret_scan_refuses_keys(self) -> None:
        manifest, records = self._complete_fake_run()
        review = self._completed_review(manifest, records)
        public_manifest, public_records = harness.public_run_copy(
            manifest,
            harness.canonical_records(manifest, records),
            source_manifest_sha256="1" * 64,
            source_responses_sha256="2" * 64,
        )
        errors, warnings = harness.run_validation_errors(
            public_manifest, public_records, require_complete=True
        )
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(harness.publication_findings(
            {"manifest": public_manifest, "records": public_records, "review": review}
        ), [])
        suspicious = harness.publication_findings(
            {"raw_stdout": "api_key=sk-ant-abcdefghijklmnopqrstuv"}
        )
        self.assertTrue(any(item["kind"] == "secret" for item in suspicious))

    def test_report_keeps_deterministic_and_human_sections_separate(self) -> None:
        manifest, records = self._complete_fake_run()
        report = harness.build_report_markdown(manifest, records, None)
        self.assertIn("## Deterministic surface measurements", report)
        self.assertIn("## Human truth and depth review", report)
        self.assertIn("No completed human review was supplied", report)
        self.assertIn("## Captured outputs", report)


if __name__ == "__main__":
    unittest.main()
