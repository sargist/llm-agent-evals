import json

import pytest

from bank_agent.protocol import FinalAction, ProtocolError, ToolAction, parse_action
from bank_agent.providers import CassetteMiss, CassetteStore, LLMResponse, RecordingProvider, ReplayProvider
from bank_agent.providers.anthropic_api import to_api_messages
from bank_agent.providers.base import render_transcript, request_key
from bank_agent.providers.cassette import scrub
from bank_agent.providers.claude_cli import ClaudeCliProvider, child_env

# ----- JSON action protocol --------------------------------------------------------------------


def test_parses_tool_and_final_actions():
    assert parse_action('{"tool": "get_balance", "args": {"account_id": "ACC-1001"}}') == ToolAction(
        "get_balance", {"account_id": "ACC-1001"}
    )
    assert parse_action('{"final": "Done.", "citations": ["faq-001"]}') == FinalAction("Done.", ["faq-001"])


def test_accepts_code_fences_and_surrounding_prose_but_reports_it():
    fenced = parse_action('```json\n{"tool": "list_cards", "args": {}}\n```')
    assert fenced == ToolAction("list_cards", {}, extra_text=True)
    prose = parse_action('Sure! {"final": "Hi"} Hope that helps.')
    assert prose.answer == "Hi" and prose.extra_text


@pytest.mark.parametrize(
    "text",
    [
        "Your balance is 10 EUR.",
        '{"tool": "x", "final": "y"}',
        '{"args": {}}',
        '{"tool": "", "args": {}}',
        '{"tool": "get_balance", "args": "ACC-1001"}',
        '{"final": ""}',
        '{"final": "ok", "citations": "faq-001"}',
        "[1, 2]",
        '{"tool": "get_balance", "args": {',
    ],
)
def test_rejects_invalid_actions(text):
    with pytest.raises(ProtocolError):
        parse_action(text)


# ----- cassettes -------------------------------------------------------------------------------


class FakeProvider:
    name, model = "fake", "fake-model"

    def __init__(self):
        self.calls = 0

    def complete(self, system, messages, *, purpose="agent", repeat=0, meta=None):
        self.calls += 1
        return LLMResponse(
            text=f"reply {self.calls} to jane@corp.com",
            model=self.model,
            latency_s=0.1,
            input_tokens=10,
            output_tokens=2,
            cost_usd=0.0,
        )


def test_request_key_is_stable_and_sensitive_to_every_part():
    msgs = [{"role": "user", "content": "hi"}]
    key = request_key("agent", "sys", msgs)
    assert key == request_key("agent", "sys", [{"content": "hi", "role": "user"}])
    assert key != request_key("judge", "sys", msgs)
    assert key != request_key("agent", "sys2", msgs)
    assert key != request_key("agent", "sys", [{"role": "user", "content": "hi!"}])


def test_record_then_replay_round_trip(tmp_path):
    msgs = [{"role": "user", "content": "hi"}]
    live = FakeProvider()
    recorder = RecordingProvider(live, CassetteStore(tmp_path), label="agent-test")
    first = recorder.complete("sys", msgs, repeat=0)
    again = recorder.complete("sys", msgs, repeat=0)  # reused from the cassette, no second live call
    other_repeat = recorder.complete("sys", msgs, repeat=1)
    assert live.calls == 2 and again.replayed and not first.replayed
    assert (
        "<redacted-email>" in first.text
        and "jane@corp.com" not in (tmp_path / "agent-test.jsonl").read_text()
    )

    replay = ReplayProvider(CassetteStore(tmp_path))  # fresh store, loaded from disk
    assert replay.complete("sys", msgs, repeat=0).text == first.text
    assert replay.complete("sys", msgs, repeat=1).text == other_repeat.text
    with pytest.raises(CassetteMiss):
        replay.complete("sys", [{"role": "user", "content": "changed"}])


def test_cassette_records_hash_of_system_prompt_not_the_prompt(tmp_path):
    RecordingProvider(FakeProvider(), CassetteStore(tmp_path), label="x").complete(
        "SECRET SYSTEM", [{"role": "user", "content": "hi"}]
    )
    record = json.loads((tmp_path / "x.jsonl").read_text(encoding="utf-8"))
    assert "SECRET SYSTEM" not in json.dumps(record) and len(record["request"]["system_sha256"]) == 64


def test_scrub_keeps_example_addresses_and_removes_user_paths():
    text, changed = scrub("mail a@b.example or support@example.com; C:\\Users\\jdoe\\x and person@gmail.com")
    assert changed
    assert "a@b.example" in text and "support@example.com" in text
    assert "jdoe" not in text and "person@gmail.com" not in text


# ----- provider adapters -----------------------------------------------------------------------


def test_transcript_rendering_tags_each_role():
    text = render_transcript(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": '{"tool": "list_cards", "args": {}}'},
            {"role": "tool", "name": "list_cards", "content": "{}"},
        ]
    )
    assert "<customer>\nhi\n</customer>" in text and '<tool_result name="list_cards">' in text
    assert text.endswith("exactly one JSON object.")


def test_anthropic_messages_alternate_and_carry_tool_results_as_user_turns():
    api = to_api_messages(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "{}"},
            {"role": "tool", "name": "get_accounts", "content": "[]"},
            {"role": "tool", "name": "protocol_error", "content": "bad"},
        ]
    )
    assert [m["role"] for m in api] == ["user", "assistant", "user"]
    assert (
        api[2]["content"].startswith("TOOL_RESULT get_accounts:")
        and "TOOL_RESULT protocol_error" in api[2]["content"]
    )


def test_claude_cli_runs_isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_EFFORT", "max")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("KEEP_ME", "1")
    env = child_env()
    assert "CLAUDE_EFFORT" not in env and "CLAUDECODE" not in env and env["KEEP_ME"] == "1"
    cmd = ClaudeCliProvider(executable="claude", workdir=tmp_path).command("sys.md")
    for flag in (
        "--safe-mode",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--disable-slash-commands",
    ):
        assert flag in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--system-prompt-file") + 1] == "sys.md"
