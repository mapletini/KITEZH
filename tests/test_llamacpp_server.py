import os
import unittest
from unittest.mock import Mock, patch

from skills import llamacpp_server


class TestLlamaCppServerEnv(unittest.TestCase):
    def _common_patches(self):
        return (
            patch.object(llamacpp_server.shutil, "which", return_value="/usr/local/bin/llama-server"),
            patch.object(llamacpp_server.Path, "exists", return_value=True),
            patch.object(llamacpp_server.LlamaCppServer, "_wait_for_ready", return_value=None),
        )

    def test_start_without_lib_dir_does_not_override_env(self) -> None:
        server = llamacpp_server.LlamaCppServer(model_path="/models/test.gguf")
        fake_proc = Mock()
        fake_proc.pid = 1234

        with self._common_patches()[0], self._common_patches()[1], self._common_patches()[2], \
             patch.object(llamacpp_server.config, "LLAMACPP_SERVER_LIB_DIR", ""), \
             patch.object(llamacpp_server.subprocess, "Popen", return_value=fake_proc) as mocked_popen:
            server.start()

        self.assertTrue(mocked_popen.called)
        self.assertIsNone(mocked_popen.call_args.kwargs.get("env"))

    def test_start_with_lib_dir_prepends_ld_library_path(self) -> None:
        server = llamacpp_server.LlamaCppServer(model_path="/models/test.gguf")
        fake_proc = Mock()
        fake_proc.pid = 5678

        with self._common_patches()[0], self._common_patches()[1], self._common_patches()[2], \
             patch.object(llamacpp_server.config, "LLAMACPP_SERVER_LIB_DIR", "/usr/local/lib"), \
             patch.dict(os.environ, {"LD_LIBRARY_PATH": "/opt/custom/lib"}, clear=False), \
             patch.object(llamacpp_server.subprocess, "Popen", return_value=fake_proc) as mocked_popen:
            server.start()

        popen_env = mocked_popen.call_args.kwargs.get("env")
        self.assertIsNotNone(popen_env)
        self.assertEqual(popen_env["LD_LIBRARY_PATH"], "/usr/local/lib:/opt/custom/lib")


if __name__ == "__main__":
    unittest.main()
