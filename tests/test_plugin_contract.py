import hashlib
import json
import re
import struct
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

    def test_bar_icon_uses_native_optical_size_and_theme_color(self) -> None:
        component = (ROOT / "CalibreIcon.qml").read_text()
        widget = (ROOT / "BarWidget.qml").read_text()

        self.assertIn("property color color:", component)
        self.assertIn("color: root.color", component)
        self.assertIn("maskSource: sourceImage", component)
        self.assertIn("iconSize: Style.space(12)", widget)
        self.assertIn("color: button.foreground", widget)

    def test_icon_decodes_at_the_display_pixel_ratio(self) -> None:
        component = (ROOT / "CalibreIcon.qml").read_text()

        self.assertIn("Screen.devicePixelRatio", component)
        self.assertNotIn("width * 2", component)

    def test_release_files_cover_installation_security_and_preview(self) -> None:
        for name in ("README.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md", "preview.png"):
            self.assertTrue((ROOT / name).is_file(), name)

        readme = (ROOT / "README.md").read_text()
        self.assertIn(
            "omarchy plugin add https://github.com/WhiteHades/omarchy-calibre-plugin.git --enable",
            readme,
        )
        self.assertIn("Calibre 7 or newer", readme)
        self.assertIn("GPL-3.0-only asset", readme)

        preview = (ROOT / "preview.png").read_bytes()
        self.assertEqual(preview[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(preview[12:16], b"IHDR")
        width, height = struct.unpack(">II", preview[16:24])
        self.assertGreaterEqual(width, 1200)
        self.assertGreaterEqual(height, 600)
        self.assertLessEqual(width * height, 40_000_000)

    def test_visual_harness_always_self_terminates(self) -> None:
        harness = (ROOT / "tests" / "qml-panel" / "shell.qml").read_text()

        self.assertIn("Qt.quit()", harness)
        match = re.search(r"id:\s*safetyTimeout[\s\S]*?interval:\s*(\d+)", harness)
        self.assertIsNotNone(match)
        self.assertLessEqual(int(match.group(1)), 10_000)

    def test_all_display_text_uses_plain_text_rendering(self) -> None:
        for path in ROOT.glob("*.qml"):
            source = path.read_text()
            self.assertEqual(
                source.count("Text {"),
                source.count("textFormat: Text.PlainText"),
                path.name,
            )

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

    def test_toolbar_wraps_and_the_catalogue_keeps_a_native_two_pane_layout(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()

        self.assertIn("Flow {\n            id: toolbar", panel)
        self.assertIn("height: implicitHeight", panel)
        self.assertRegex(panel, r'PanelSectionHeader\s*\{\s*text: "CATALOGUE"')
        self.assertIn("Math.floor(libraryPanes.availableWidth * 0.47)", panel)
        self.assertIn("bookScroll.contentHeight > bookScroll.height", panel)
        self.assertIn("bookScrollBar.width", panel)
        self.assertIn("inspectorScroll.contentHeight > inspectorScroll.height", panel)
        self.assertIn("inspectorScrollBar.width", panel)

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

    def test_concurrent_searches_cannot_restore_stale_results(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()

        self.assertIn("property int queryGeneration: 0", panel)
        self.assertRegex(panel, r"function\s+cancelOutstandingQueries\s*\(")
        self.assertIn('kind === "query" || kind === "query-append"', panel)
        self.assertIn("bridge.cancel(requestId)", panel)
        self.assertIn("context.queryGeneration", panel)
        self.assertIn("queryGeneration: generation", panel)
        self.assertIn("Model.forgetJob(viewState, event.id)", panel)

    def test_repeated_bootstraps_cannot_restore_stale_state(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()

        self.assertIn("property int bootstrapGeneration: 0", panel)
        self.assertIn("property string bootstrapRequestId:", panel)
        self.assertIn("bridge.cancel(bootstrapRequestId)", panel)
        self.assertIn("context.bootstrapGeneration", panel)
        self.assertIn("bootstrapGeneration: generation", panel)

    def test_panel_matches_the_native_hotkey_reveal_lifecycle(self) -> None:
        widget = (ROOT / "BarWidget.qml").read_text()
        panel = (ROOT / "Panel.qml").read_text()

        self.assertIn("panelLoader.item.openFromHotkey()", widget)
        self.assertRegex(panel, r"function\s+openFromHotkey\s*\(")
        self.assertRegex(panel, r"function\s+setCenterHoverRevealSuppressed\s*\(")
        self.assertIn("setCenterHoverRevealSuppressed(false)", panel)
        self.assertIn("setCenterHoverRevealSuppressed(true)", panel)

    def test_panel_lifecycle_is_local_but_refresh_reaches_peer_monitors(self) -> None:
        widget = (ROOT / "BarWidget.qml").read_text()

        self.assertIn("function open(): void { root.open() }", widget)
        self.assertIn("function close(): void { root.close() }", widget)
        self.assertIn("function toggle(): void { root.togglePanel() }", widget)
        self.assertIn('function refresh(): void { root.broadcast("refresh") }', widget)
        self.assertNotIn('root.broadcast("open")', widget)
        self.assertNotIn('root.broadcast("close")', widget)
        self.assertNotIn('root.broadcast("togglePanel")', widget)

    def test_panel_close_dismisses_and_cleans_the_open_workflow(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()

        self.assertRegex(panel, r"function\s+dismissWorkflow\s*\(")
        close_body = panel[panel.index("function close()") : panel.index("function toggle()")]
        self.assertIn("dismissWorkflow()", close_body)
        self.assertIn("closeMetadataDialog()", panel)
        self.assertIn("closeDeviceDialog()", panel)
        self.assertIn("cancelConfirmation()", panel)

    def test_destructive_preparations_are_bound_to_the_visible_selection(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()

        self.assertIn("property int workflowGeneration:", panel)
        self.assertRegex(panel, r"function\s+beginWorkflowContext\s*\(")
        self.assertRegex(panel, r"function\s+isCurrentWorkflow\s*\(")
        self.assertIn("discardConfirmationToken", panel)
        self.assertIn("context.workflowGeneration", panel)

    def test_book_mutation_results_cannot_retarget_the_visible_selection(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()

        self.assertRegex(panel, r"function\s+isCurrentBookContext\s*\(")
        self.assertIn("workflowGeneration: pending.workflowGeneration", panel)
        self.assertIn("var currentBookContext = isCurrentBookContext(context)", panel)
        self.assertIn("if (currentBookContext) viewState = Model.applyBook", panel)
        self.assertIn("if (currentBookContext && opened", panel)

    def test_calibre_actions_share_keyboard_and_accessibility_defaults(self) -> None:
        button = (ROOT / "CalibreButton.qml").read_text()
        action_button = (ROOT / "CalibreActionButton.qml").read_text()
        panel = (ROOT / "Panel.qml").read_text()
        palette = (ROOT / "CommandPalette.qml").read_text()

        for source in (button, action_button):
            self.assertIn("focusable: true", source)
            self.assertIn("Accessible.role: Accessible.Button", source)
            self.assertIn("Accessible.name:", source)
            self.assertIn("Accessible.onPressAction:", source)
        self.assertIn("Accessible.role: Accessible.ListItem", panel)
        self.assertIn("Accessible.role: Accessible.ListItem", palette)
        self.assertIn('root.viewState.mode !== "library"', panel)
        self.assertIn("!keyCatcher.activeFocus", panel)
        self.assertIn("searchField.forceActiveFocus()", panel)
        self.assertRegex(panel, r"function\s+restorePanelFocus\s*\(")
        self.assertRegex(panel, r"MouseArea\s*\{\s*id:\s*modalInputShield")

    def test_dropdown_trigger_is_inside_the_popup_close_boundary(self) -> None:
        dropdown = (ROOT / "CalibreDropdown.qml").read_text()

        self.assertRegex(dropdown, r"Popup\s*\{\s*id:\s*popup\s*closePolicy:[^\n]*CloseOnPressOutside\s*x:\s*0\s*y:\s*0")
        self.assertRegex(dropdown, r"MouseArea\s*\{\s*id:\s*popupTrigger")
        self.assertIn("height: trigger.height", dropdown)

    def test_panel_supports_the_documented_delete_key(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()

        self.assertRegex(
            panel,
            r'Shortcut\s*\{[\s\S]*?sequence:\s*"Delete"[\s\S]*?'
            r'onActivated:\s*root\.runSecondaryAction\("remove"\)',
        )

    def test_conversion_options_are_bound_to_one_dialog_session(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()
        dialog = (ROOT / "ConversionDialog.qml").read_text()

        self.assertIn("property int conversionSessionGeneration:", panel)
        self.assertIn("property string conversionRequestId:", panel)
        self.assertRegex(panel, r"function\s+closeConversionDialog\s*\(")
        self.assertIn("isCurrentConversionRequest", panel)
        self.assertIn("onCanceled: root.closeConversionDialog()", panel)
        self.assertIn("focus: visible", dialog)
        self.assertIn("root.forceActiveFocus()", dialog)

    def test_file_picker_actions_keep_the_launch_book_and_library(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()

        for context in ("coverPickerContext", "exportPickerContext", "formatPickerContext"):
            self.assertIn("property var " + context, panel)
        self.assertRegex(panel, r"function\s+submitForLibrary\s*\(")
        self.assertIn("bookId: root.coverPickerContext.bookId", panel)
        self.assertIn("bookIds: [root.exportPickerContext.bookId]", panel)
        self.assertRegex(panel, r"function\s+launchFilePicker\s*\(")
        self.assertIn("filePickerDelay.restart()", panel)
        self.assertIn("root.controller.hide()", panel)
        self.assertIn("onExited: root.finishFilePicker", panel)
        self.assertGreaterEqual(panel.count("if (queuedFilePicker || activeFilePicker) return"), 2)
        close_body = panel[panel.index("function close()") : panel.index("function dismissWorkflow()")]
        self.assertIn("filePickerDelay.stop()", close_body)
        self.assertIn("reopenAfterFilePicker = false", close_body)

    def test_bootstrap_preserves_the_visible_search_and_sort(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()

        bootstrap_body = panel[panel.index("function bootstrap") : panel.index("function refresh")]
        self.assertIn("Model.combineSearch(searchField.text, filterQuery)", bootstrap_body)
        self.assertIn("sort: sortField", bootstrap_body)
        self.assertIn("direction: sortDirection", bootstrap_body)

    def test_bridge_exit_terminalizes_every_pending_request(self) -> None:
        bridge = (ROOT / "CalibreBridge.qml").read_text()

        self.assertIn("property var activeRequests:", bridge)
        self.assertRegex(bridge, r"function\s+failPending\s*\(")
        self.assertIn('code: "bridge_stopped"', bridge)
        self.assertIn("root.failPending()", bridge)

    def test_command_progress_stays_indeterminate_without_real_measurements(self) -> None:
        backend = (ROOT / "backend" / "calibre_bridge.py").read_text()
        model = (ROOT / "Model.js").read_text()
        jobs = (ROOT / "JobsDialog.qml").read_text()
        device = (ROOT / "DeviceDialog.qml").read_text()

        self.assertNotIn('"fraction": 0.03', backend)
        self.assertIn("determinate: false", model)
        self.assertIn("job.determinate = true", model)
        self.assertIn("NumberAnimation", jobs)
        self.assertIn("property bool progressDeterminate", device)

    def test_two_pane_layout_keeps_controls_inside_narrow_panels(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()
        device = (ROOT / "DeviceDialog.qml").read_text()

        self.assertRegex(panel, r"Grid\s*\{\s*id:\s*libraryPanes")
        self.assertIn("readonly property bool stacked:", panel)
        self.assertIn("columns: stacked ? 1 : 3", panel)
        self.assertIn("stacked ? libraryPanes.width", panel)
        self.assertIn("Math.min(Style.space(145), toolbar.width)", panel)
        self.assertIn("Math.min(Style.space(105), toolbar.width)", panel)
        self.assertIn("Math.min(Style.space(110), toolbar.width)", panel)
        self.assertRegex(device, r"Flow\s*\{\s*id:\s*footer")
        self.assertIn("height: implicitHeight", device)

    def test_freeform_metadata_comments_have_an_accessible_name(self) -> None:
        editor = (ROOT / "MetadataEditor.qml").read_text()
        comments = editor[editor.index("QQC.TextArea {") : editor.index("background: BorderSurface", editor.index("QQC.TextArea {"))]

        self.assertIn("Accessible.role: Accessible.EditableText", comments)
        self.assertIn('Accessible.name: "Comments"', comments)

    def test_secondary_actions_live_in_a_keyboard_command_palette(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()
        palette = (ROOT / "CommandPalette.qml").read_text()

        self.assertIn("CommandPalette {", panel)
        self.assertIn("Model.commandMatches", palette)
        self.assertIn('text === "p"', panel)
        for command_id in ("add-files", "add-folder", "choose-library", "refresh", "jobs"):
            self.assertIn(f'id: "{command_id}"', panel)

    def test_keyboard_help_includes_live_calibre_diagnostics(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()

        self.assertIn("HelpDialog {", panel)
        self.assertIn('id: "help"', panel)
        self.assertIn('text === "?"', panel)
        self.assertIn('root.dialogMode = "help"', panel)
        self.assertIn("calibreVersion: String(root.viewState.calibre.version", panel)
        self.assertIn("libraryPath: root.libraryPath()", panel)

    def test_reader_transfer_is_native_and_never_asks_for_a_device_path(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()

        self.assertIn("DeviceDialog {", panel)
        self.assertIn('id: "device"', panel)
        self.assertIn('operation, inputData, kind', panel)
        self.assertIn('submit("device.probe"', panel)
        self.assertIn('submit("device.send"', panel)
        self.assertIn('submit("device.eject"', panel)
        self.assertIn('submit("action.commit"', panel)
        self.assertIn("confirmationToken: conflict.confirmationToken", panel)
        self.assertNotIn("deviceDestination", panel)
        send_function = panel[panel.find("function sendBookToDevice"):panel.find("function canReplaceDeviceConflict")]
        self.assertNotIn("force:", send_function)
        self.assertNotIn("destination:", send_function)

    def test_reader_results_and_replace_confirmation_are_session_bound(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()

        self.assertIn("property int deviceSessionGeneration", panel)
        self.assertIn("property var deviceBook", panel)
        self.assertIn("function isCurrentDeviceRequest", panel)
        self.assertIn("sessionGeneration: deviceSessionGeneration", panel)
        self.assertIn("libraryToken: viewState.currentLibrary", panel)
        self.assertIn("bookId: deviceBook.id", panel)
        self.assertIn("format: requestedFormat", panel)
        self.assertIn("deviceConflict.format", panel)
        self.assertIn("deviceConflict.confirmationToken", panel)
        self.assertIn("function discardDeviceConflict", panel)
        self.assertIn('result.state === "error"', panel)
        self.assertIn("stateForDeviceError(result.error)", panel)

    def test_metadata_preview_can_apply_selected_fields_or_be_discarded(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()

        self.assertIn("MetadataDownloadDialog {", panel)
        self.assertIn('name: "book.metadata.fetch"', panel)
        self.assertIn('submit("action.commit"', panel)
        self.assertIn('submit("action.discard"', panel)
        self.assertIn("fields: fields", panel)
        self.assertIn('downloadAvailable: root.hasCapability("book.metadata.fetch")', panel)
        self.assertIn('id: "metadata-fetch"', panel)
        self.assertIn("discardMetadataToken(result.previewToken, context.libraryToken)", panel)

    def test_metadata_results_are_bound_to_the_open_book_and_library(self) -> None:
        panel = (ROOT / "Panel.qml").read_text()

        self.assertIn("property int metadataSessionGeneration", panel)
        self.assertIn("property var metadataBook", panel)
        self.assertIn("property string metadataPreviewLibraryToken", panel)
        self.assertIn("function isCurrentMetadataRequest", panel)
        self.assertIn("sessionGeneration: metadataSessionGeneration", panel)
        self.assertIn("libraryToken: viewState.currentLibrary", panel)
        self.assertIn("bookId: metadataBook.id", panel)
        self.assertIn("previewToken: metadataPreviewToken", panel)
        self.assertIn("discardMetadataToken(result.previewToken, context.libraryToken)", panel)
        self.assertIn("discardMetadataToken(context.previewToken, context.libraryToken)", panel)
        self.assertIn("book: root.metadataBook", panel)

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
