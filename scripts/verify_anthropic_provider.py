from __future__ import annotations

import json
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.integrations import llm_client

source = Path(REPO_ROOT / "app" / "integrations" / "llm_client.py").read_text()
assert "OPENAI_API_KEY" not in source, "OpenAI-specific environment variable reference is still present"
assert "_try_anthropic_family_scope" in source, "Anthropic provider helper is missing"


class _FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, payload: dict):
        self.content = [_FakeTextBlock(json.dumps(payload))]


class _FakeMessages:
    def create(self, **kwargs):
        assert kwargs["model"] == "claude-3-5-haiku-latest"
        assert kwargs["messages"][0]["role"] == "user"
        return _FakeResponse(
            {
                "product_family": "pcb_pcba",
                "confidence": "high",
                "reasoning": "The description refers to an automotive control board.",
                "extracted_facts": {},
                "candidate_families": ["pcb_pcba", "ic_asic"],
            }
        )


class _FakeAnthropic:
    def __init__(self, api_key: str):
        assert api_key == "test-anthropic-key"
        self.messages = _FakeMessages()


fake_module = types.ModuleType("anthropic")
fake_module.Anthropic = _FakeAnthropic
sys.modules["anthropic"] = fake_module

llm_client.ANTHROPIC_API_KEY = "test-anthropic-key"
result = llm_client._try_anthropic_family_scope("Automotive control board for an ECU")
assert result is not None, "Anthropic helper returned no result"
assert result["product_family"] == "pcb_pcba"
assert result["candidate_families"][0] == "pcb_pcba"

print("Anthropic advisory-provider verification passed.")
