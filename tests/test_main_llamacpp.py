import unittest
from unittest.mock import Mock, patch

import requests

import llm_backends
import main


class TestLlamaCppBackend(unittest.TestCase):
    def test_arg_parser_includes_llamacpp_backend(self) -> None:
        parser = main.build_arg_parser()
        backend_action = next(action for action in parser._actions if action.dest == "backend")
        self.assertIn("llamacpp", backend_action.choices)

    def test_send_to_llamacpp_returns_message_content(self) -> None:
        fake_response = Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "choices": [
                {"message": {"role": "assistant", "content": "hello from llama.cpp"}}
            ]
        }
        with patch.object(llm_backends.config, "LLAMACPP_BASE_URL", "http://localhost:8080"), patch.object(
            llm_backends.config, "LLAMACPP_MODEL", "nous-hermes-2-mixtral-8x7b-dpo-gguf"
        ), patch.object(llm_backends.requests, "post", return_value=fake_response) as mocked_post:
            result = llm_backends.send_to_llamacpp("test prompt")

        self.assertEqual(result, "hello from llama.cpp")
        mocked_post.assert_called_once()
        call_kwargs = mocked_post.call_args.kwargs
        self.assertEqual(call_kwargs["json"]["model"], "nous-hermes-2-mixtral-8x7b-dpo-gguf")
        self.assertEqual(call_kwargs["json"]["messages"][-1]["content"], "test prompt")

    def test_send_to_llamacpp_raises_runtime_error_on_connection_failure(self) -> None:
        with patch.object(llm_backends.requests, "post", side_effect=requests.exceptions.ConnectionError("offline")):
            with self.assertRaises(RuntimeError) as captured:
                llm_backends.send_to_llamacpp("test prompt")
        self.assertIn("Cannot connect to llama.cpp server", str(captured.exception))

    def test_send_to_llamacpp_raises_runtime_error_on_invalid_json(self) -> None:
        fake_response = Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.side_effect = requests.exceptions.JSONDecodeError("bad json", "{}", 0)
        with patch.object(llm_backends.requests, "post", return_value=fake_response):
            with self.assertRaises(RuntimeError) as captured:
                llm_backends.send_to_llamacpp("test prompt")
        self.assertIn("returned invalid JSON", str(captured.exception))

    def test_send_to_backend_passes_system_to_ollama(self) -> None:
        with patch.object(llm_backends, "send_to_ollama", return_value="ok") as send:
            result = llm_backends.send_to_backend(
                "prompt",
                backend="ollama",
                model="m",
                system="system context",
            )
        self.assertEqual(result, "ok")
        send.assert_called_once_with("prompt", model="m", system="system context")

    def test_send_to_backend_passes_system_to_letta(self) -> None:
        with patch.object(llm_backends, "send_to_letta", return_value="ok") as send:
            result = llm_backends.send_to_backend(
                "prompt",
                backend="letta",
                agent_id="agent-1",
                system="system context",
            )
        self.assertEqual(result, "ok")
        send.assert_called_once_with("prompt", agent_id="agent-1", system="system context")

    def test_main_health_exits_cleanly_when_remote_disabled(self) -> None:
        with patch.object(main.config, "REMOTE_ENABLED", False), patch("builtins.print") as mock_print:
            exit_code = main.main(["--health"])
        self.assertEqual(exit_code, 0)
        mock_print.assert_called_once_with("Remote backend: disabled (set KITEZH_REMOTE_ENABLED=1 to enable)")

    def test_main_init_llamacpp_exits_cleanly(self) -> None:
        """Regression: main --init <file> --backend llamacpp must not crash.

        Before the generate_frame envelope fix, the warmup call
        ``audio.generate_frame(duration=0.1)`` raised:
            ValueError: could not broadcast input array from shape (6615,)
                        into shape (4410,)
        because the hard-coded release constant (6615 samples) exceeded
        total_samples (4410) for the 0.1 s warmup frame, causing a shape
        mismatch on the numpy envelope slice assignment.

        A ValueError from inside main() propagates as a test error, so a clean
        exit code of 0 confirms the bug does not recur.
        """
        fake_response = Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "ok"}}]
        }
        with patch.object(main.config, "REMOTE_ENABLED", False), \
             patch.object(llm_backends.requests, "post", return_value=fake_response), \
             patch("builtins.print"):
            exit_code = main.main(["--init", "system_manifest.md", "--backend", "llamacpp"])
        self.assertEqual(exit_code, 0)

    def test_main_init_llamacpp_returns_error_on_invalid_json(self) -> None:
        fake_response = Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.side_effect = requests.exceptions.JSONDecodeError("bad json", "{}", 0)
        with patch.object(main.config, "REMOTE_ENABLED", False), \
             patch.object(llm_backends.requests, "post", return_value=fake_response), \
             patch("builtins.print"):
            exit_code = main.main(["--init", "system_manifest.md", "--backend", "llamacpp"])
        self.assertEqual(exit_code, 1)


class TestChatWithToolsLlamacpp(unittest.TestCase):
    """Tests for the agentic tool-calling loop."""

    def _make_response(self, content: str, tool_calls: list | None = None) -> Mock:
        finish_reason = "tool_calls" if tool_calls else "stop"
        message: dict = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        fake = Mock()
        fake.raise_for_status.return_value = None
        fake.json.return_value = {
            "choices": [{"message": message, "finish_reason": finish_reason}]
        }
        return fake

    def test_plain_text_response_returned_directly(self) -> None:
        fake = self._make_response("Hello there!")
        with patch.object(llm_backends.requests, "post", return_value=fake):
            result = llm_backends.chat_with_tools_llamacpp([{"role": "user", "content": "hi"}])
        self.assertEqual(result, "Hello there!")

    def test_system_prompt_prepended_to_messages(self) -> None:
        fake = self._make_response("ok")
        with patch.object(llm_backends.requests, "post", return_value=fake) as mocked:
            llm_backends.chat_with_tools_llamacpp(
                [{"role": "user", "content": "hi"}],
                system="You are Kai.",
            )
        sent_messages = mocked.call_args.kwargs["json"]["messages"]
        self.assertEqual(sent_messages[0]["role"], "system")
        self.assertEqual(sent_messages[0]["content"], "You are Kai.")

    def test_tool_definitions_included_in_request(self) -> None:
        fake = self._make_response("ok")
        tools = [{"type": "function", "function": {"name": "my_tool", "description": "x", "parameters": {}}}]
        with patch.object(llm_backends.requests, "post", return_value=fake) as mocked:
            llm_backends.chat_with_tools_llamacpp(
                [{"role": "user", "content": "hi"}],
                tools=tools,
            )
        sent_payload = mocked.call_args.kwargs["json"]
        self.assertIn("tools", sent_payload)
        self.assertEqual(sent_payload["tool_choice"], "auto")

    def test_tool_call_executed_and_result_fed_back(self) -> None:
        tool_call_response = self._make_response(
            "",
            tool_calls=[{
                "id": "call_1",
                "function": {"name": "my_tool", "arguments": '{"x": 1}'},
            }],
        )
        final_response = self._make_response("Done!")

        executor = Mock(return_value="tool output")
        with patch.object(llm_backends.requests, "post", side_effect=[tool_call_response, final_response]):
            result = llm_backends.chat_with_tools_llamacpp(
                [{"role": "user", "content": "use the tool"}],
                tool_executor=executor,
            )
        self.assertEqual(result, "Done!")
        executor.assert_called_once_with("my_tool", {"x": 1})

    def test_no_executor_stops_on_tool_call(self) -> None:
        tool_call_response = self._make_response(
            "partial",
            tool_calls=[{"id": "c", "function": {"name": "t", "arguments": "{}"}}],
        )
        with patch.object(llm_backends.requests, "post", return_value=tool_call_response):
            result = llm_backends.chat_with_tools_llamacpp(
                [{"role": "user", "content": "hi"}],
                tool_executor=None,
            )
        # Should return partial content (or fallback string) without crashing.
        self.assertIsInstance(result, str)

    def test_connection_error_raises_runtime_error(self) -> None:
        with patch.object(
            llm_backends.requests, "post", side_effect=requests.exceptions.ConnectionError("down")
        ):
            with self.assertRaises(RuntimeError) as ctx:
                llm_backends.chat_with_tools_llamacpp([{"role": "user", "content": "hi"}])
        self.assertIn("Cannot connect", str(ctx.exception))

    def test_empty_choices_returns_raw_data(self) -> None:
        fake = Mock()
        fake.raise_for_status.return_value = None
        fake.json.return_value = {"choices": []}
        with patch.object(llm_backends.requests, "post", return_value=fake):
            result = llm_backends.chat_with_tools_llamacpp([{"role": "user", "content": "hi"}])
        self.assertIsInstance(result, str)


if __name__ == "__main__":
    unittest.main()
