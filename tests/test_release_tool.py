import importlib.util
import pathlib
import unittest
import unittest.mock

spec = importlib.util.spec_from_file_location(
    "release_tool", pathlib.Path(__file__).resolve().parent.parent / "tools" / "release.py")
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)


def c(subject, body=""):
    return rt.Commit("0123456789abcdef0123456789abcdef01234567", subject, body)


class VersioningRules(unittest.TestCase):
    def test_bumps(self):
        self.assertEqual(rt.next_version("1.2.3", [c("fix: a"), c("chore: b")]), "1.2.4")
        self.assertEqual(rt.next_version("1.2.3", [c("docs: a")]), "1.2.4")
        self.assertEqual(rt.next_version("1.2.3", [c("fix: a"), c("feat(widget): b")]), "1.3.0")
        self.assertEqual(rt.next_version("1.2.3", [c("feat!: drop x")]), "2.0.0")
        self.assertEqual(rt.next_version("1.2.3", [c("fix: a", "details\n\nBREAKING CHANGE: gone")]), "2.0.0")
        # pre-1.0: breaking changes bump the minor version
        self.assertEqual(rt.next_version("0.2.0", [c("refactor(daemon)!: new socket")]), "0.3.0")
        self.assertEqual(rt.next_version("0.2.0", [c("feat: x")]), "0.3.0")
        self.assertEqual(rt.next_version("0.2.0", [c("fix: x")]), "0.2.1")

    def test_parsing(self):
        x = c("feat(firefox): open playlists")
        self.assertTrue(x.valid)
        self.assertEqual((x.type, x.scope, x.text, x.breaking), ("feat", "firefox", "open playlists", False))
        self.assertTrue(c("chore(release): 1.2.3").is_release)
        self.assertFalse(c("Add things").valid)
        self.assertFalse(c("feature: things").valid)

    def test_lint(self):
        self.assertIsNone(rt.lint_message("fix(protocol): handle PONG"))
        self.assertIsNone(rt.lint_message('Revert "feat: x"'))
        self.assertIsNotNone(rt.lint_message("Fix the protocol"))
        self.assertIsNotNone(rt.lint_message("feat:missing space"))
        self.assertIsNotNone(rt.lint_message("wip: stuff"))

    def test_changelog_sections(self):
        text = rt.render_section("0.3.0", "0.2.0", [
            c("feat(widget): pairing QR"), c("fix: crash"), c("perf: faster"), c("ci: cache"),
            c("feat!: new config", "BREAKING CHANGE: config moved to ~/.config/x"),
        ], None)
        self.assertIn("/compare/v0.2.0...v0.3.0", text)
        order = [text.index(h) for h in ("### ⚠ Breaking changes", "### Features", "### Fixes", "### Chores")]
        self.assertEqual(order, sorted(order))
        self.assertIn("- **widget:** pairing QR", text)
        self.assertIn("config moved to ~/.config/x", text)
        fixes = text.split("### Fixes")[1].split("###")[0]
        self.assertIn("crash", fixes)
        self.assertIn("faster", fixes)


class ExtensionVersioning(unittest.TestCase):
    def test_changelog_says_whether_the_extension_changed(self):
        new = rt.render_section("0.4.0", "0.3.1", [c("feat(firefox): x")], None, extension="0.4.0")
        self.assertIn("Firefox extension: new version 0.4.0.", new)
        same = rt.render_section("0.4.0", "0.3.1", [c("feat(daemon): x")], None, extension="0.3.1")
        self.assertIn("Firefox extension: unchanged (0.3.1).", same)

    def test_extension_version_may_lag_but_not_lead(self):
        versions = {"grayjay_link/__init__.py": "0.4.0", "plasmoid/metadata.json": "0.4.0"}
        for ext, ok in (("0.3.1", True), ("0.4.0", True), ("0.5.0", False), ("junk", False)):
            with self.subTest(ext=ext), unittest.mock.patch.object(
                    rt, "read_versions", return_value={**versions, "firefox/manifest.json": ext}):
                if ok:
                    self.assertEqual(rt.current_version(), "0.4.0")
                else:
                    with self.assertRaises(SystemExit):
                        rt.current_version()

    def test_core_files_must_agree(self):
        with unittest.mock.patch.object(rt, "read_versions", return_value={
                "grayjay_link/__init__.py": "0.4.0", "plasmoid/metadata.json": "0.3.1",
                "firefox/manifest.json": "0.3.1"}):
            with self.assertRaises(SystemExit):
                rt.current_version()


if __name__ == "__main__":
    unittest.main()
