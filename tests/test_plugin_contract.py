import hashlib
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CALIBRE_ICON_SHA256 = "b876a5790062dcf51a8955ef3af985076bb02996a6fae226ef3a66143f9bef8b"


class PluginContractTest(unittest.TestCase):
    def test_manifest_exposes_one_native_bar_widget(self) -> None:
        manifest = json.loads((ROOT / "manifest.json").read_text())

        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(manifest["id"], "io.github.whitehades.calibre")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(manifest["license"], "MIT")
        self.assertEqual(manifest["kinds"], ["bar-widget"])
        self.assertEqual(manifest["entryPoints"], {"barWidget": "BarWidget.qml"})
        self.assertEqual(manifest["barWidget"]["allowMultiple"], False)
        self.assertEqual(manifest["barWidget"]["defaultSection"], "right")
        self.assertEqual(manifest["barWidget"]["defaults"]["preferredFormats"], "EPUB,AZW3,PDF,MOBI")
        self.assertTrue((ROOT / manifest["entryPoints"]["barWidget"]).is_file())

    def test_widget_uses_the_native_panel_contract(self) -> None:
        widget = (ROOT / "BarWidget.qml").read_text()
        panel = (ROOT / "Panel.qml").read_text()

        self.assertIn("BarWidget {", widget)
        self.assertIn('moduleName: "io.github.whitehades.calibre"', widget)
        for method in ("open", "close", "togglePanel", "injectPanel"):
            self.assertRegex(widget, rf"function\s+{method}\s*\(")
        self.assertIn("Panel {", panel)
        self.assertIn("KeyboardPanel {", panel)
        self.assertIn("manageIpc: false", panel)
        self.assertIn("owner: root.barIdentity", panel)

    def test_bridge_is_persistent_and_accepts_only_json_lines(self) -> None:
        bridge = (ROOT / "CalibreBridge.qml").read_text()

        self.assertIn("stdinEnabled: true", bridge)
        self.assertIn('Qt.resolvedUrl("backend/calibre_bridge.py")', bridge)
        self.assertIn("JSON.stringify", bridge)
        self.assertIn("JSON.parse", bridge)
        self.assertNotIn('"bash", "-c"', bridge)

    def test_setup_actions_are_explicit_and_user_triggered(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()

        self.assertIn('bar.run("omarchy-install-app Calibre calibre")', panel)
        self.assertIn('Qt.openUrlExternally("https://calibre-ebook.com/download_linux")', panel)
        self.assertNotRegex(panel, re.compile(r"Component\.onCompleted[^}]*omarchy-install-app", re.DOTALL))

    def test_code_is_mit_and_the_upstream_icon_keeps_its_gpl_license(self) -> None:
        manifest = json.loads((ROOT / "manifest.json").read_text())
        code_license = (ROOT / "LICENSE").read_text()
        icon_license = (ROOT / "assets" / "LICENSE.GPL-3.0-only.txt").read_text()
        notice = (ROOT / "assets" / "NOTICE.md").read_text()

        self.assertEqual(manifest["license"], "MIT")
        self.assertIn("MIT License", code_license)
        self.assertIn("Copyright (c) 2026 WhiteHades", code_license)
        self.assertIn("GNU GENERAL PUBLIC LICENSE", icon_license)
        self.assertIn("Version 3, 29 June 2007", icon_license)
        self.assertIn("calibre.svg", notice)
        self.assertIn("GPL-3.0-only", notice)
        self.assertIn("Kovid Goyal", notice)

    def test_vendored_app_icon_is_the_exact_upstream_calibre_svg(self) -> None:
        icon_path = ROOT / "assets" / "calibre.svg"

        self.assertTrue(icon_path.is_file())
        self.assertEqual(hashlib.sha256(icon_path.read_bytes()).hexdigest(), CALIBRE_ICON_SHA256)

        component = (ROOT / "CalibreIcon.qml").read_text()
        widget = (ROOT / "BarWidget.qml").read_text()
        panel = (ROOT / "Panel.qml").read_text()
        self.assertIn('Qt.resolvedUrl("assets/calibre.svg")', component)
        self.assertIn("CalibreIcon {", widget)
        self.assertIn("CalibreIcon {", panel)
        self.assertNotIn("󰂺", widget + panel)

    def test_visual_harness_always_self_terminates(self) -> None:
        harness = (ROOT / "tests" / "qml-panel" / "shell.qml").read_text()

        self.assertIn("Qt.quit()", harness)
        match = re.search(r"id:\s*safetyTimeout[\s\S]*?interval:\s*(\d+)", harness)
        self.assertIsNotNone(match)
        self.assertLessEqual(int(match.group(1)), 10_000)

    def test_panel_exposes_the_adoption_first_workflows(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()

        for component in ("ConversionDialog {", "CalibreConfirmDialog {", "FormatManager {"):
            self.assertIn(component, panel)
        for operation in (
            '"conversion.describe"',
            '"book.convert.quick"',
            '"book.convert.replace"',
            '"format.add"',
            '"format.replace"',
            '"format.remove"',
            '"book.remove"',
            '"action.commit"',
        ):
            self.assertIn(operation, panel)
        self.assertIn('Quickshell.execDetached(["xdg-open", path])', panel)
        self.assertNotIn('Quickshell.execDetached(["bash"', panel)

    def test_job_cancellation_stays_inside_the_bridge_protocol(self) -> None:
        bridge = (ROOT / "CalibreBridge.qml").read_text()
        panel = (ROOT / "Panel.qml").read_text()

        self.assertRegex(bridge, r"function\s+cancel\s*\(requestId\)")
        self.assertIn('type: "cancel"', bridge)
        self.assertIn("JobsDialog {", panel)
        self.assertIn("bridge.cancel(requestId)", panel)

    def test_confirmation_cancel_discards_the_staged_bridge_plan(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()

        self.assertRegex(panel, r"function\s+cancelConfirmation\s*\(")
        self.assertIn('bridge.submit("action.discard"', panel)
        self.assertIn("confirmationToken: pending.token", panel)

    def test_secondary_actions_live_in_a_keyboard_command_palette(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()
        palette = (ROOT / "CommandPalette.qml").read_text()

        self.assertIn("CommandPalette {", panel)
        self.assertIn("Model.commandMatches", palette)
        self.assertIn('text === "p"', panel)
        for command_id in ("add-files", "add-folder", "choose-library", "refresh", "jobs"):
            self.assertIn(f'id: "{command_id}"', panel)

    def test_tracked_sources_do_not_publish_a_private_home_path(self) -> None:
        private_home = "/home/" + "efaz"
        for path in ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts or ".tmp" in path.parts:
                continue
            if path.suffix not in {".md", ".qml", ".js", ".json", ".py"}:
                continue
            self.assertNotIn(private_home, path.read_text(errors="replace"), str(path.relative_to(ROOT)))


if __name__ == "__main__":
    unittest.main()
