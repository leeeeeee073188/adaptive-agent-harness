from __future__ import annotations

import unittest

from adaptive_harness.evidence_workspace import EvidenceWorkspace


class EvidenceWorkspaceTests(unittest.TestCase):
    def test_large_result_preserves_middle_constraints_as_archived_excerpt(self) -> None:
        content = "\n".join(
            [
                "preamble " + "x" * 1200,
                "REQUIRED COLUMNS: week, visits, quality_score",
                "ERROR: null trend rows must be excluded",
                "tail " + "y" * 1200,
            ]
        )
        workspace = EvidenceWorkspace.from_messages(
            [
                {"role": "user", "content": "Audit trend data and preserve required columns."},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call-1", "name": "read_file", "arguments": {"path": "/task/workspace/audit.txt"}}
                    ],
                },
                {"role": "tool", "tool_call_id": "call-1", "content": content},
            ],
            task_terms="Audit trend data required columns null rows",
            max_full_chars=240,
            max_excerpt_chars=360,
        )

        snapshot = workspace.snapshot()
        resource = snapshot.resources[0]
        self.assertTrue(resource.archived)
        rendered = str(workspace.to_context_block())
        self.assertIn("workspace/audit.txt", rendered)
        self.assertIn("REQUIRED COLUMNS", rendered)
        self.assertIn("null trend rows", rendered)
        self.assertNotIn("preamble " + "x" * 400, rendered)
        self.assertNotIn("tail " + "y" * 400, rendered)

    def test_cross_tool_same_resource_aggregates_access_and_tools(self) -> None:
        messages = [
            {"role": "user", "content": "Inspect input.json."},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "read-1", "name": "read_file", "arguments": {"path": "/task/input.json"}}
                ],
            },
            {"role": "tool", "tool_call_id": "read-1", "content": '{"ok": true}'},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "bash-1", "name": "bash", "arguments": {"cmd": "cat /task/input.json"}}
                ],
            },
            {"role": "tool", "tool_call_id": "bash-1", "content": '{"ok": true}'},
        ]

        snapshot = EvidenceWorkspace.from_messages(messages).snapshot()

        self.assertEqual(len(snapshot.resources), 1)
        resource = snapshot.resources[0]
        self.assertEqual(resource.resource, "input.json")
        self.assertEqual(resource.access_count, 2)
        self.assertEqual(resource.tools, ("bash", "read_file"))
        self.assertEqual(resource.distinct_results, 1)
        self.assertEqual(snapshot.repeated_results, 1)

    def test_secrets_are_scrubbed_from_dashboard_and_excerpts(self) -> None:
        secret = "sk-" + "abcdefghijklmnop"
        workspace = EvidenceWorkspace.from_messages(
            [
                {"role": "user", "content": "Read config."},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "name": "read_file",
                            "arguments": {
                                "path": "/task/config.txt",
                                "authorization": f"Bearer {secret}",
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": f"password=letmein\napi_key={secret}\nTITLE: safe config evidence",
                },
            ],
            max_full_chars=80,
        )

        rendered = str(workspace.to_context_block())
        self.assertNotIn("sk-", rendered.lower())
        self.assertNotIn("authorization", rendered.lower())
        self.assertNotIn("password", rendered.lower())
        self.assertIn("safe config evidence", rendered)

    def test_scrub_keeps_noncredential_token_budget_constraint(self) -> None:
        secret = "sk-" + "secretvalue123456"
        workspace = EvidenceWorkspace.from_messages(
            [
                {"role": "user", "content": "Read constraints."},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "name": "read_file",
                            "arguments": {"path": "/task/constraints.txt"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": (
                        "CONSTRAINT: keep token budget under control\n"
                        f"Authorization: Bearer {secret}\n"
                        "TITLE: cost control"
                    ),
                },
            ],
            max_full_chars=120,
        )

        rendered = str(workspace.to_context_block())
        self.assertIn("token budget", rendered)
        self.assertIn("cost control", rendered)
        self.assertNotIn("authorization", rendered.lower())
        self.assertNotIn("sk-", rendered.lower())

    def test_scrub_removes_specific_token_and_client_secret_credentials(self) -> None:
        workspace = EvidenceWorkspace.from_messages(
            [
                {"role": "user", "content": "Read auth config while preserving token budget note."},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "name": "read_file",
                            "arguments": {
                                "url": "https://example.test/config?access_token=secret123456&safe=true",
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": "\n".join(
                        [
                            '{"access_token": "secret123456"}',
                            "refresh_token=refreshsecret",
                            "github_token=ghp_secretvalue",
                            "client_secret=clientsecret",
                            "normal line: token budget remains a task constraint",
                            "TITLE: public auth config facts",
                        ]
                    ),
                },
            ],
            max_full_chars=200,
        )

        rendered = str(workspace.to_context_block()).lower()
        self.assertNotIn("access_token", rendered)
        self.assertNotIn("refresh_token", rendered)
        self.assertNotIn("github_token", rendered)
        self.assertNotIn("client_secret", rendered)
        self.assertNotIn("secret123456", rendered)
        self.assertIn("safe=true", rendered)
        self.assertIn("token budget", rendered)
        self.assertIn("public auth config facts", rendered)

    def test_dashboard_redacts_non_sk_credentials_and_url_userinfo(self) -> None:
        workspace = EvidenceWorkspace.from_messages(
            [
                {"role": "user", "content": "Read config."},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "name": "read_file",
                            "arguments": {
                                "url": "https://user:pass@example.com/data?password=hidden&key=public#frag",
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": (
                        "password=plainpass\n"
                        "api_key=plain-api-key\n"
                        "Authorization: Bearer plainbearer123\n"
                        "TITLE: token budget is a safe constraint"
                    ),
                },
            ],
            max_full_chars=200,
        )

        rendered = str(workspace.to_context_block()).lower()
        self.assertNotIn("plainpass", rendered)
        self.assertNotIn("plain-api-key", rendered)
        self.assertNotIn("plainbearer123", rendered)
        self.assertNotIn("user:pass", rendered)
        self.assertNotIn("password=hidden", rendered)
        self.assertNotIn("frag", rendered)
        self.assertIn("https://example.com/data?key=public", rendered)
        self.assertIn("token budget", rendered)

    def test_invalid_url_resource_does_not_fallback_to_raw_dashboard_value(self) -> None:
        workspace = EvidenceWorkspace.from_messages(
            [
                {"role": "user", "content": "Read config."},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "name": "read_file",
                            "arguments": {
                                "url": "https://user:pass@example.com:bad/data?api_key=plain-key",
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call-1", "content": "TITLE: safe fact"},
            ],
            max_full_chars=200,
        )

        rendered = str(workspace.to_context_block()).lower()
        self.assertNotIn("user:pass", rendered)
        self.assertNotIn("plain-key", rendered)
        self.assertNotIn(":bad", rendered)
        self.assertIn("<redacted-resource>", rendered)

    def test_parameter_validation_rejects_invalid_limits(self) -> None:
        messages = [{"role": "user", "content": "No tools."}]
        with self.assertRaisesRegex(ValueError, "max_full_chars"):
            EvidenceWorkspace.from_messages(messages, max_full_chars=-1)
        with self.assertRaisesRegex(ValueError, "max_excerpt_chars"):
            EvidenceWorkspace.from_messages(messages, max_excerpt_chars=0)
        workspace = EvidenceWorkspace.from_messages(messages)
        with self.assertRaisesRegex(ValueError, "excerpt_limit"):
            workspace.to_context_block(excerpt_limit=-1)
        with self.assertRaisesRegex(ValueError, "excerpt limit"):
            workspace.evidence_excerpts(-1)

    def test_duplicate_results_keep_one_excerpt_in_working_set(self) -> None:
        duplicate = "TITLE: inventory\nrequired sku exists"
        workspace = EvidenceWorkspace.from_messages(
            [
                {"role": "user", "content": "Check inventory."},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "read-1", "name": "read_file", "arguments": {"path": "/task/inventory.txt"}}
                    ],
                },
                {"role": "tool", "tool_call_id": "read-1", "content": duplicate},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "read-2", "name": "read_file", "arguments": {"path": "/task/inventory.txt"}}
                    ],
                },
                {"role": "tool", "tool_call_id": "read-2", "content": duplicate},
            ]
        )

        block = workspace.to_context_block()
        self.assertEqual(block["resources"][0]["access_count"], 2)
        self.assertEqual(len(block["evidence_excerpts"]), 1)
        self.assertEqual(block["stats"]["repeated_results"], 1)


if __name__ == "__main__":
    unittest.main()
