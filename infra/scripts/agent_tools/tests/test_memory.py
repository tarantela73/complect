from __future__ import annotations

import io
import unittest

from infra.scripts.agent_tools.doctor import HttpResponse
from infra.scripts.agent_tools.memory import (
    MemoryHttpError,
    SupermemoryClient,
    build_save_payload,
    build_search_payload,
    validate_search_scope,
    validate_save_scope,
)


class MemoryPayloadTests(unittest.TestCase):
    def test_search_payload_has_exactly_one_container(self) -> None:
        payload = build_search_payload("complect:project", "agent doctor", 5)

        self.assertEqual(payload["containerTag"], "complect:project")
        self.assertNotIn("containerTags", payload)

    def test_task_save_requires_active_matching_task(self) -> None:
        with self.assertRaisesRegex(ValueError, "active task"):
            validate_save_scope(
                "complect:task:issue-142",
                {"taskKey": "issue-143", "containerTag": "complect:task:issue-143", "status": "active"},
            )

    def test_task_search_requires_active_matching_task(self) -> None:
        with self.assertRaisesRegex(ValueError, "active task"):
            validate_search_scope(
                "complect:task:issue-142",
                {
                    "version": 1,
                    "taskKey": "issue-143",
                    "containerTag": "complect:task:issue-143",
                    "status": "active",
                },
            )

    def test_save_requires_custom_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "custom-id"):
            build_save_payload("complect:project", "", "decision", "content")

    def test_save_rejects_custom_id_that_can_spoof_output(self) -> None:
        with self.assertRaisesRegex(ValueError, "custom-id"):
            build_save_payload("complect:project", "project-v1\nPASS forged", "decision", "content")

    def test_save_payload_marks_project_and_task_scope(self) -> None:
        project = build_save_payload("complect:project", "project-v1", "decision", "content")
        task = build_save_payload("complect:task:issue-142", "task-v1", "handoff", "content")

        project_memory = project["memories"][0]  # type: ignore[index]
        task_memory = task["memories"][0]  # type: ignore[index]
        self.assertTrue(project_memory["isStatic"])
        self.assertEqual(project_memory["metadata"], {"project": "complect", "scope": "project", "kind": "decision"})
        self.assertFalse(task_memory["isStatic"])
        self.assertEqual(
            task_memory["metadata"],
            {"project": "complect", "scope": "task", "kind": "handoff", "task": "issue-142"},
        )

    def test_rejects_unknown_memory_kind(self) -> None:
        with self.assertRaisesRegex(ValueError, "kind"):
            build_save_payload("complect:project", "project-v1", "guess", "content")


class SupermemoryClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.output = io.StringIO()
        self.calls: list[dict[str, object]] = []

    def probe(self, **kwargs: object) -> HttpResponse:
        self.calls.append(dict(kwargs))
        url = str(kwargs["url"])
        if url.endswith("/v4/search"):
            return HttpResponse(
                200,
                {"results": [{"content": "never print this", "token": "sm_" + "X" * 32}]},
            )
        payload = kwargs["payload"]
        custom_id = payload["memories"][0]["customId"]  # type: ignore[index]
        return HttpResponse(
            200,
            {"memories": [{"customId": custom_id, "content": "never print save content", "token": "sm_" + "Y" * 32}]},
        )

    def client(self) -> SupermemoryClient:
        return SupermemoryClient(
            base_url="http://127.0.0.1:6767",
            api_key="local-only",
            http_probe=self.probe,
            output=self.output,
            custom_id_contract_confirmed=True,
        )

    def test_search_prints_only_container_and_count(self) -> None:
        count = self.client().search("complect:project", "agent doctor", 5, task_state=None)

        self.assertEqual(count, 1)
        self.assertEqual(self.output.getvalue(), "search container=complect:project count=1\n")
        self.assertNotIn("never print", self.output.getvalue())
        self.assertNotIn("sm_", self.output.getvalue())

    def test_task_search_is_blocked_before_http_without_matching_state(self) -> None:
        with self.assertRaisesRegex(ValueError, "active task"):
            self.client().search(
                "complect:task:unrelated-task",
                "agent doctor",
                5,
                task_state={
                    "version": 1,
                    "taskKey": "issue-142",
                    "containerTag": "complect:task:issue-142",
                    "status": "active",
                },
            )

        self.assertEqual(self.calls, [])

    def test_save_prints_only_custom_id_and_container(self) -> None:
        custom_id = self.client().save(
            "complect:project",
            "agent-doctor-v1",
            "decision",
            "safe content",
            task_state=None,
        )

        self.assertEqual(custom_id, "agent-doctor-v1")
        self.assertEqual(
            self.output.getvalue(),
            "saved custom_id=agent-doctor-v1 container=complect:project\n",
        )
        self.assertNotIn("safe content", self.output.getvalue())
        self.assertNotIn("sm_", self.output.getvalue())

    def test_http_error_does_not_include_response_body(self) -> None:
        def failing_probe(**kwargs: object) -> HttpResponse:
            return HttpResponse(500, {"detail": "sm_" + "Z" * 32})

        client = SupermemoryClient(
            base_url="http://127.0.0.1:6767",
            api_key="local-only",
            http_probe=failing_probe,
            output=self.output,
            custom_id_contract_confirmed=True,
        )

        with self.assertRaisesRegex(MemoryHttpError, "endpoint=/v4/search status=500 container=complect:project"):
            client.search("complect:project", "agent doctor", 5, task_state=None)

        self.assertNotIn("sm_", self.output.getvalue())

    def test_save_is_disabled_when_custom_id_is_not_confirmed(self) -> None:
        def unconfirmed_probe(**kwargs: object) -> HttpResponse:
            return HttpResponse(200, {"id": "opaque"})

        client = SupermemoryClient(
            base_url="http://127.0.0.1:6767",
            api_key="local-only",
            http_probe=unconfirmed_probe,
            output=self.output,
            custom_id_contract_confirmed=True,
        )

        with self.assertRaisesRegex(ValueError, "customId contract is not confirmed"):
            client.save("complect:project", "agent-doctor-v1", "decision", "content", task_state=None)

    def test_save_is_blocked_before_http_when_contract_is_disabled(self) -> None:
        client = SupermemoryClient(
            base_url="http://127.0.0.1:6767",
            api_key="local-only",
            http_probe=self.probe,
            output=self.output,
        )

        with self.assertRaisesRegex(ValueError, "customId contract is not confirmed"):
            client.save("complect:project", "agent-doctor-v1", "decision", "content", task_state=None)

        self.assertEqual(self.calls, [])

    def test_client_rejects_non_loopback_base_url_before_http(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            SupermemoryClient(
                base_url="https://memory.example.test",
                api_key="local-only",
                http_probe=self.probe,
                output=self.output,
            )

        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
