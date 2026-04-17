from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.main import app


def assert_true(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def main() -> None:
    with TestClient(app) as client:
        start = client.post(
            "/api/classify",
            json={
                "description": "Automotive control board for an ECU",
                "origin": "CN",
                "destination": "US",
            },
        )
        assert_true(start.status_code == 200, f"Expected 200 from /api/classify, got {start.status_code}: {start.text}")
        start_payload = start.json()

        questions = start_payload.get("pending_questions") or []
        assert_true(bool(questions), "Expected at least one clarification question from the initial response.")
        assert_true(start_payload.get("classification") is None, "Initial response should not include a classification.")
        assert_true(questions[0].get("fact_key") == "_family_confirm", f"Expected family confirmation question, got {questions[0].get('fact_key')!r}.")
        assert_true("printed circuit board" in questions[0].get("question", "").lower(), f"Expected PCB confirmation wording, got: {questions[0].get('question')!r}")
        assert_true(questions[0].get("options") == ["yes", "no"], f"Expected yes/no family confirmation options, got {questions[0].get('options')!r}.")

        clarify = client.post(
            "/api/clarify",
            json={
                "session_id": start_payload["session_id"],
                "answers": {"_family_confirm": "yes"},
            },
        )
        assert_true(clarify.status_code == 200, f"Expected 200 from /api/clarify, got {clarify.status_code}: {clarify.text}")
        clarify_payload = clarify.json()

        next_questions = clarify_payload.get("pending_questions") or []
        assert_true(clarify_payload.get("product_family") == "pcb_pcba", f"Expected confirmed family pcb_pcba, got {clarify_payload.get('product_family')!r}.")
        assert_true(clarify_payload.get("classification") is None, "Classification should remain empty until digit-locking facts are resolved.")
        assert_true(bool(next_questions), "Expected a next clarification question after family confirmation.")
        assert_true(next_questions[0].get("fact_key") == "bare_or_populated", f"Expected bare_or_populated question next, got {next_questions[0].get('fact_key')!r}.")

        print("Backend verification passed.")
        print(f"Initial question: {questions[0]['question']}")
        print(f"Next question: {next_questions[0]['question']}")


if __name__ == "__main__":
    main()
