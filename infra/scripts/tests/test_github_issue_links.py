"""Hermetic tests through the public helper and its external gh boundary."""

from __future__ import annotations

import copy
import contextlib
import io
import json
import subprocess
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

from infra.scripts.github_issue_links import ensure_link, main

REPO = "tarantela73/complect"


def issue(number: int) -> dict:
    return {
        "id": number + 1000,
        "number": number,
        "repository_url": "https://api.github.com/repos/" + REPO,
        "title": "issue",
        "body": "private body",
        "state": "open",
        "labels": [{"name": "ready-for-agent"}],
    }


class FakeGh:
    def __init__(self):
        self.calls = []
        self.issues = {n: issue(n) for n in (53, 54, 57, 99)}
        self.children = []
        self.blockers = []
        self.auth_ok = True
        self.other_parent = None
        self.post_status = 201
        self.timeout_after_apply = False
        self.readback_failure = False
        self.ignore_post = False
        self.change_metadata = False
        self.change_source_id = False

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if args[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(
                args, 0 if self.auth_ok else 1, "authenticated", "raw auth secret"
            )
        method = args[args.index("--method") + 1]
        raw_path = next(x for x in args if x.startswith("repos/"))
        path = raw_path.split("?", 1)[0]
        page = int(parse_qs(raw_path.partition("?")[2]).get("page", ["1"])[0])
        prefix = "repos/" + REPO + "/issues/"
        self.assert_path = path.startswith(prefix)
        if not self.assert_path:
            raise AssertionError(path)
        tail = path[len(prefix) :].split("/")
        number = int(tail[0])
        posted = any("POST" in a for a in self.calls)
        if method == "GET" and posted and self.readback_failure:
            return subprocess.CompletedProcess(
                args,
                1,
                'HTTP/2.0 503 unavailable\n\n{"message":"raw private error"}',
                "",
            )
        if method == "POST" and self.post_status >= 400:
            return subprocess.CompletedProcess(
                args,
                1,
                f"HTTP/2.0 {self.post_status} error\n\n"
                + json.dumps({"message": "raw private error"}),
                "",
            )
        status = 200
        if len(tail) == 1:
            data = self.issues[number]
        elif tail[1:] == ["sub_issues"]:
            if method == "POST":
                fields = [args[i + 1] for i, x in enumerate(args) if x == "-F"]
                if "sub_issue_id=1054" not in fields:
                    raise AssertionError(fields)
                if not self.ignore_post:
                    if not any(row["id"] == 1054 for row in self.children):
                        self.children.append(self.issues[54])
                status = self.post_status
                data = self.issues[54]
            else:
                data = self.children[(page - 1) * 100 : page * 100]
        elif tail[1:] == ["parent"]:
            linked = any(
                row["id"] == self.issues[number]["id"] for row in self.children
            )
            data = (
                self.issues[self.other_parent]
                if self.other_parent
                else self.issues[53]
                if linked
                else {"message": "Not Found"}
            )
            status = 200 if linked or self.other_parent else 404
        elif tail[1:] == ["dependencies", "blocked_by"]:
            if method == "POST":
                fields = [args[i + 1] for i, x in enumerate(args) if x == "-F"]
                if "issue_id=1054" not in fields:
                    raise AssertionError(fields)
                if not self.ignore_post:
                    if not any(row["id"] == 1054 for row in self.blockers):
                        self.blockers.append(self.issues[54])
                status = self.post_status
                data = self.issues[54]
            else:
                data = self.blockers[(page - 1) * 100 : page * 100]
        else:
            raise AssertionError(path)
        if method == "POST" and self.change_metadata:
            self.issues[number]["body"] = "changed concurrently"
        if method == "POST" and self.change_source_id:
            self.issues[number]["id"] = 9999
        if method == "POST" and self.timeout_after_apply:
            raise subprocess.TimeoutExpired(args, 30)
        return subprocess.CompletedProcess(
            args,
            0 if status < 400 else 1,
            f"HTTP/2.0 {status} result\nContent-Type: application/json\n\n"
            + json.dumps(data),
            "",
        )


class GitHubIssueLinksTests(unittest.TestCase):
    def test_malformed_unrelated_relation_blocks_confirmation(self):
        for kind, source in (("sub-issue", 53), ("blocked-by", 57)):
            for bad in (
                {"id": 9999},
                {**issue(99), "number": -1},
                {
                    **issue(99),
                    "repository_url": "https://api.github.com/repos/other/repo",
                },
                {**issue(99), "pull_request": {}},
            ):
                with self.subTest(kind=kind, bad=bad):
                    fake = FakeGh()
                    rows = [fake.issues[54], bad]
                    if kind == "sub-issue":
                        fake.children = rows
                    else:
                        fake.blockers = rows
                    with patch("subprocess.run", side_effect=fake):
                        result = ensure_link(kind, REPO, source, 54)
                    self.assertNotEqual(result["status"], "PASS", result)
                    self.assertFalse(result["write_attempted"])

    def test_readback_source_id_must_match_the_preflight_identity(self):
        fake = FakeGh()
        fake.change_source_id = True
        with patch("subprocess.run", side_effect=fake):
            result = ensure_link("blocked-by", REPO, 57, 54)
        self.assertEqual(result["status"], "FAIL", result)
        self.assertTrue(result["write_attempted"])

    def test_unreadable_or_malformed_preflight_never_attempts_a_write(self):
        for case in (
            "http503",
            "bad_header",
            "bad_json",
            "missing_labels",
            "invalid_rows",
        ):
            with self.subTest(case=case):
                fake = FakeGh()
                if case == "missing_labels":
                    fake.issues[53].pop("labels")

                def corrupt(args, **kwargs):
                    response = fake(args, **kwargs)
                    if args[1] != "api" or "GET" not in args:
                        return response
                    path = next(x for x in args if x.startswith("repos/"))
                    if case == "http503":
                        return subprocess.CompletedProcess(
                            args, 1, "HTTP/2.0 503 error\n\n{}", "private error"
                        )
                    if case == "bad_header":
                        return subprocess.CompletedProcess(
                            args, 0, "not-http\n\n{}", ""
                        )
                    if case == "bad_json":
                        return subprocess.CompletedProcess(
                            args, 0, "HTTP/2.0 200 ok\n\nnot-json", ""
                        )
                    if case == "invalid_rows" and "/sub_issues" in path:
                        return subprocess.CompletedProcess(
                            args, 0, 'HTTP/2.0 200 ok\n\n[{"id":true}]', ""
                        )
                    return response

                with patch("subprocess.run", side_effect=corrupt):
                    result = ensure_link("sub-issue", REPO, 53, 54)
                self.assertNotEqual(result["status"], "PASS")
                self.assertFalse(any("POST" in a for a in fake.calls))

    def test_parent_fields_and_unrelated_links_are_preserved(self):
        fake = FakeGh()
        fake.children = [fake.issues[99]]
        before = copy.deepcopy(fake.issues[53])
        with patch("subprocess.run", side_effect=fake):
            result = ensure_link("sub-issue", REPO, 53, 54)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(fake.issues[53], before)
        self.assertEqual({r["id"] for r in fake.children}, {1099, 1054})
        writes = [a for a in fake.calls if "POST" in a]
        self.assertIn("replace_parent=false", writes[0])
        self.assertTrue(
            all(("GET" in a or "POST" in a) for a in fake.calls if a[1] == "api")
        )

    def test_cli_reports_confirmation_failure_block_and_unknown_separately(self):
        for kind, args in (
            ("sub-issue", ["--parent", "53", "--child", "54"]),
            ("blocked-by", ["--issue", "57", "--blocker", "54"]),
        ):
            for case, expected_code, expected_status in (
                ("pass", 0, "PASS"),
                ("auth", 2, "BLOCKED"),
                ("http422", 1, "FAIL"),
                ("timeout", 3, "UNKNOWN"),
            ):
                with self.subTest(kind=kind, case=case):
                    fake = FakeGh()
                    if case == "auth":
                        fake.auth_ok = False
                    if case == "http422":
                        fake.post_status = 422
                    if case == "timeout":
                        fake.timeout_after_apply = True
                    output = io.StringIO()
                    with (
                        patch("subprocess.run", side_effect=fake),
                        contextlib.redirect_stdout(output),
                    ):
                        code = main([kind, "--repo", REPO, *args])
                    self.assertEqual(code, expected_code)
                    self.assertEqual(
                        json.loads(output.getvalue())["status"], expected_status
                    )
                    self.assertLess(len(output.getvalue()), 4096)
                    self.assertNotIn("raw", output.getvalue())

    def test_existing_relation_on_later_page_does_not_cause_a_write(self):
        for kind, source in (("sub-issue", 53), ("blocked-by", 57)):
            with self.subTest(kind=kind):
                fake = FakeGh()
                rows = [issue(n) for n in range(100, 200)] + [fake.issues[54]]
                if kind == "sub-issue":
                    fake.children = rows
                else:
                    fake.blockers = rows
                before = copy.deepcopy(rows)
                with patch("subprocess.run", side_effect=fake):
                    result = ensure_link(kind, REPO, source, 54)
                self.assertEqual(result["status"], "PASS", result)
                self.assertFalse(result["write_attempted"])
                self.assertFalse(any("POST" in a for a in fake.calls))
                self.assertTrue(
                    any("page=2" in token for a in fake.calls for token in a)
                )
                self.assertEqual(rows, before)

    def test_write_errors_and_unconfirmed_readback_are_not_success(self):
        for case, expected in (
            ("http422", "FAIL"),
            ("timeout", "UNKNOWN"),
            ("readback", "UNKNOWN"),
            ("not_applied", "UNKNOWN"),
            ("metadata", "FAIL"),
        ):
            with self.subTest(case=case):
                fake = FakeGh()
                if case == "http422":
                    fake.post_status = 422
                if case == "timeout":
                    fake.timeout_after_apply = True
                if case == "readback":
                    fake.readback_failure = True
                if case == "not_applied":
                    fake.ignore_post = True
                if case == "metadata":
                    fake.change_metadata = True
                with patch("subprocess.run", side_effect=fake):
                    result = ensure_link("sub-issue", REPO, 53, 54)
                self.assertEqual(result["status"], expected, result)
                self.assertTrue(result["write_attempted"])
                post_index = next(i for i, a in enumerate(fake.calls) if "POST" in a)
                self.assertTrue(any("GET" in a for a in fake.calls[post_index + 1 :]))
                self.assertEqual(sum("POST" in a for a in fake.calls), 1)
                self.assertNotIn("raw private error", json.dumps(result))
                if case == "http422":
                    self.assertEqual(result["http_status"], 422)
                if case == "timeout":
                    with patch("subprocess.run", side_effect=fake):
                        retry = ensure_link("sub-issue", REPO, 53, 54)
                    self.assertEqual(retry["status"], "PASS")
                    self.assertFalse(retry["write_attempted"])
                    self.assertEqual(sum("POST" in a for a in fake.calls), 1)

    def test_invalid_request_or_foreign_issue_identity_never_writes(self):
        for kind, repo, source, target in (
            ("bad", REPO, 53, 54),
            ("sub-issue", "../bad", 53, 54),
            ("sub-issue", REPO, True, 54),
            ("sub-issue", REPO, 0, 54),
            ("blocked-by", REPO, 54, 54),
        ):
            with self.subTest(kind=kind, repo=repo, source=source, target=target):
                fake = FakeGh()
                with patch("subprocess.run", side_effect=fake):
                    result = ensure_link(kind, repo, source, target)
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(fake.calls, [])
        for key, value in (
            ("id", True),
            ("id", 1053),
            ("number", 99),
            ("repository_url", "https://api.github.com/repos/other/repo"),
            ("pull_request", {}),
        ):
            with self.subTest(key=key, value=value):
                fake = FakeGh()
                fake.issues[54][key] = value
                with patch("subprocess.run", side_effect=fake):
                    result = ensure_link("sub-issue", REPO, 53, 54)
                self.assertNotEqual(result["status"], "PASS")
                self.assertFalse(any("POST" in a for a in fake.calls))

    def test_failed_auth_or_other_parent_is_not_repaired_by_writes(self):
        fake = FakeGh()
        fake.auth_ok = False
        with patch("subprocess.run", side_effect=fake):
            result = ensure_link("sub-issue", REPO, 53, 54)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(len(fake.calls), 1)
        self.assertNotIn("raw auth secret", json.dumps(result))
        fake = FakeGh()
        fake.other_parent = 99
        with patch("subprocess.run", side_effect=fake):
            result = ensure_link("sub-issue", REPO, 53, 54)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["error"], "PARENT_CONFLICT")
        self.assertFalse(any("POST" in a for a in fake.calls))

    def test_both_relations_are_idempotent_and_auth_precedes_reads(self):
        for kind, source in (("sub-issue", 53), ("blocked-by", 57)):
            with self.subTest(kind=kind):
                fake = FakeGh()
                with patch("subprocess.run", side_effect=fake):
                    first = ensure_link(kind, REPO, source, 54)
                    second = ensure_link(kind, REPO, source, 54)
                self.assertEqual((first["status"], second["status"]), ("PASS", "PASS"))
                self.assertTrue(first["write_attempted"])
                self.assertFalse(second["write_attempted"])
                posts = [a for a in fake.calls if "POST" in a]
                self.assertEqual(len(posts), 1)
                self.assertIn(
                    "sub_issue_id=1054" if kind == "sub-issue" else "issue_id=1054",
                    posts[0],
                )
                self.assertEqual(fake.calls[0][:3], ["gh", "auth", "status"])
                self.assertTrue(
                    all(
                        "--hostname" in a and "github.com" in a
                        for a in fake.calls
                        if a[1] == "api"
                    )
                )
                self.assertNotIn("private body", json.dumps(first))

    def test_sub_issue_uses_database_id_and_preserves_parent(self):
        fake = FakeGh()
        before = copy.deepcopy(fake.issues[53])
        with patch("subprocess.run", side_effect=fake):
            result = ensure_link("sub-issue", REPO, 53, 54)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["write_attempted"], True)
        self.assertEqual(fake.issues[53], before)
        self.assertEqual(fake.children[0]["id"], 1054)
        posts = [a for a in fake.calls if "POST" in a]
        self.assertEqual(len(posts), 1)
        self.assertIn("sub_issue_id=1054", posts[0])
        self.assertNotIn("issue_id=1054", posts[0])


if __name__ == "__main__":
    unittest.main()
