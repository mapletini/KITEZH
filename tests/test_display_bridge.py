import tempfile
import unittest

from skills.display_bridge import DisplayBridge, build_display_payload, load_display_state
from skills.terminal_face import render_terminal_face


class TestDisplayBridge(unittest.TestCase):
    def test_publish_persists_latest_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bridge = DisplayBridge(state_path=f"{tmp}/state.json")
            payload = build_display_payload(
                {"label": "joy", "pad": [0.1, 0.2, 0.3], "intensity": 0.4, "strongest_need": "connection"},
                narrative="Kai feels bright.",
                mode="active",
            )
            bridge.publish(payload)
            loaded = load_display_state(f"{tmp}/state.json")
        self.assertEqual(loaded["mode"], "active")
        self.assertEqual(loaded["emotion"]["label"], "joy")

    def test_terminal_renderer_includes_narrative(self) -> None:
        frame = render_terminal_face(
            {
                "emotion": {"label": "joy", "intensity": 0.7, "pad": [0.5, 0.5, 0.5], "strongest_need": "connection"},
                "narrative": "Kai feels close to the user.",
            }
        )
        self.assertIn("Kai", frame)
        self.assertIn("joy", frame)


if __name__ == "__main__":
    unittest.main()
