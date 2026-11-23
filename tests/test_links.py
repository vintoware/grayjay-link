import json
import os
import pathlib
import stat
import tempfile
import unittest
from unittest import mock

from grayjay_link import links
from grayjay_link.urlrules import service_of


class UrlRulesTests(unittest.TestCase):
    # Same cases as firefox/rules.js is meant to handle; keep the two in sync.
    def test_supported(self):
        for url in [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/watch?v=dQw4w9WgXcQ&t=42",
            "https://m.youtube.com/watch?v=abc",
            "https://www.youtube.com/shorts/abc123",
            "https://www.youtube.com/live/abc123",
            "https://www.youtube.com/playlist?list=PL123",
            "https://www.youtube.com/@somechannel",
            "https://www.youtube.com/channel/UC123",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.twitch.tv/somechannel",
            "https://twitch.tv/videos/123456",
        ]:
            with self.subTest(url=url):
                self.assertIsNotNone(service_of(url))

    def test_not_supported(self):
        for url in [
            "https://www.youtube.com/",
            "https://www.youtube.com/watch",
            "https://www.youtube.com/feed/subscriptions",
            "https://accounts.youtube.com/whatever",
            "https://youtu.be/",
            "https://www.twitch.tv/directory",
            "https://www.twitch.tv/settings",
            "https://example.com/watch?v=abc",
            "https://notyoutube.com/watch?v=abc",
            "file:///home/x/index.html",
            "mailto:someone@example.com",
            "not a url",
        ]:
            with self.subTest(url=url):
                self.assertIsNone(service_of(url))


class DispatchTests(unittest.TestCase):
    def test_routing(self):
        opened = []
        with mock.patch.object(links, "forward") as fwd:
            result = links.dispatch(
                ["https://youtu.be/a", "https://example.com/", "https://www.twitch.tv/somechannel"],
                lambda url: opened.append(url) or True)
        self.assertEqual(opened, ["https://youtu.be/a", "https://www.twitch.tv/somechannel"])
        self.assertEqual([t for _, t in result], ["grayjay", "browser", "grayjay"])
        fwd.assert_called_once_with(["https://example.com/"])

    def test_falls_back_to_browser_when_helper_unavailable(self):
        with mock.patch.object(links, "forward") as fwd:
            result = links.dispatch(["https://youtu.be/a"], lambda url: False)
        self.assertEqual(result, [("https://youtu.be/a", "browser")])
        fwd.assert_called_once_with(["https://youtu.be/a"])

    def test_dry_run_opens_nothing(self):
        with mock.patch.object(links, "forward") as fwd:
            result = links.dispatch(["https://youtu.be/a", "https://example.com/"],
                                    lambda url: self.fail("must not open"), dry_run=True)
        self.assertEqual([t for _, t in result], ["grayjay", "browser"])
        fwd.assert_not_called()


FAKE_XDG_MIME = """#!/bin/sh
# Minimal xdg-mime: default <id> <types...> / query default <type>, stored in $FAKE_STATE.
if [ "$1" = default ]; then echo "$2" > "$FAKE_STATE/https"; exit 0; fi
if [ "$1" = query ]; then cat "$FAKE_STATE/https" 2>/dev/null; exit 0; fi
exit 1
"""
FAKE_KREAD = """#!/bin/sh
cat "$FAKE_STATE/kde" 2>/dev/null
"""
FAKE_KWRITE = """#!/bin/sh
for last; do :; done
echo "$last" > "$FAKE_STATE/kde"
"""


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.state = root / "state"
        self.state.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for name, body in (("xdg-mime", FAKE_XDG_MIME), ("kreadconfig6", FAKE_KREAD), ("kwriteconfig6", FAKE_KWRITE)):
            f = bin_dir / name
            f.write_text(body)
            f.chmod(f.stat().st_mode | stat.S_IEXEC)
        apps = root / "data" / "applications"
        apps.mkdir(parents=True)
        for desktop_id in (links.HANDLER_ID, "org.mozilla.firefox.desktop"):
            (apps / desktop_id).write_text("[Desktop Entry]\n")
        (self.state / "https").write_text("org.mozilla.firefox.desktop\n")
        env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_STATE": str(self.state),
               "XDG_DATA_HOME": str(root / "data"), "XDG_DATA_DIRS": str(root / "none"),
               "XDG_CONFIG_HOME": str(root / "config")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_enable_disable_restores_previous_browser(self):
        st = links.enable()
        self.assertTrue(st["enabled"])
        self.assertEqual(st["fallback"], "org.mozilla.firefox.desktop")
        self.assertEqual((self.state / "https").read_text().strip(), links.HANDLER_ID)
        self.assertFalse((self.state / "kde").exists())  # was unset: left alone
        self.assertEqual(links.browser_for_forwarding().name, "org.mozilla.firefox.desktop")
        st = links.disable()
        self.assertFalse(st["enabled"])
        self.assertEqual((self.state / "https").read_text().strip(), "org.mozilla.firefox.desktop")

    def test_kde_browser_application_is_swapped_and_restored(self):
        (self.state / "kde").write_text("org.mozilla.firefox.desktop\n")
        links.enable()
        self.assertEqual((self.state / "kde").read_text().strip(), links.HANDLER_ID)
        self.assertEqual(json.loads(links.config_file().read_text())["kde_browser_application"],
                         "org.mozilla.firefox.desktop")
        links.disable()
        self.assertEqual((self.state / "kde").read_text().strip(), "org.mozilla.firefox.desktop")

    def test_enable_twice_keeps_original_fallback(self):
        links.enable()
        links.enable()
        self.assertEqual(links.status()["fallback"], "org.mozilla.firefox.desktop")


if __name__ == "__main__":
    unittest.main()
