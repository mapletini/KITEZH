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
        self.assertEqual(call_kwargs["json"]["messages"][1]["content"], "test prompt")

    def test_send_to_llamacpp_raises_runtime_error_on_connection_failure(self) -> None:
        with patch.object(llm_backends.requests, "post", side_effect=requests.exceptions.ConnectionError("offline")):
            with self.assertRaises(RuntimeError) as captured:
                llm_backends.send_to_llamacpp("test prompt")
        self.assertIn("Cannot connect to llama.cpp server", str(captured.exception))

    def test_send_to_llamacpp_raises_runtime_error_on_invalid_json(self) -> None:
        fake_response = Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.side_effect = ValueError("bad json")
        with patch.object(llm_backends.requests, "post", return_value=fake_response):
            with self.assertRaises(RuntimeError) as captured:
                llm_backends.send_to_llamacpp("test prompt")
        self.assertIn("returned invalid JSON", str(captured.exception))

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
        fake_response.json.side_effect = ValueError("bad json")
        with patch.object(main.config, "REMOTE_ENABLED", False), \
             patch.object(llm_backends.requests, "post", return_value=fake_response), \
             patch("builtins.print"):
            exit_code = main.main(["--init", "system_manifest.md", "--backend", "llamacpp"])
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
