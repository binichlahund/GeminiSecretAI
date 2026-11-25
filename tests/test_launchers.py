"""Exercise real Windows launch paths without starting a second client."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from geminisecretai.paths import PATHS


@unittest.skipUnless(sys.platform.startswith("win"), "Windows launchers")
class LauncherTests(unittest.TestCase):
    def test_vbs_resolves_launcher_from_a_different_working_directory(self):
        with tempfile.TemporaryDirectory(prefix="Gemini other cwd ") as folder:
            result = subprocess.run(
                ["cscript.exe", "//nologo", str(PATHS.root / "Start GeminiSecretAI.vbs"), "/check"],
                cwd=folder, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()), PATHS.root / "scripts" / "launch.pyw")

    def test_autostart_targets_main_launcher_and_updates_owned_legacy_link(self):
        with tempfile.TemporaryDirectory(prefix="Gemini startup fixture ") as folder:
            # All shortcuts are synthetic and confined to this temporary folder.
            script = Path(folder) / "check.ps1"
            script.write_text('''param($ProjectRoot, $TestFolder)
$shellObject = New-Object -ComObject WScript.Shell
$linkPath = Join-Path $TestFolder 'Gemini Hotkey.lnk'
$link = $shellObject.CreateShortcut($linkPath)
$link.TargetPath = Join-Path $env:WINDIR 'System32\\wscript.exe'
$link.Arguments = '"' + (Join-Path $ProjectRoot 'start_hidden.vbs') + '"'
$link.Save()
& (Join-Path $ProjectRoot 'scripts\\autostart.ps1') -StartupFolder $TestFolder
$saved = $shellObject.CreateShortcut($linkPath)
@{target=$saved.TargetPath; arguments=$saved.Arguments; cwd=$saved.WorkingDirectory;
  count=@(Get-ChildItem -LiteralPath $TestFolder -Filter '*.lnk').Count} | ConvertTo-Json -Compress
''', encoding="utf-8")
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                 "-ProjectRoot", str(PATHS.root), "-TestFolder", folder],
                cwd=folder, capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            saved = json.loads(result.stdout)
            self.assertEqual(saved["arguments"], '"' + str(PATHS.root / "Start GeminiSecretAI.vbs") + '"')
            self.assertEqual(Path(saved["cwd"]), PATHS.root)
            self.assertTrue(saved["target"].lower().endswith("wscript.exe"))
            self.assertEqual(saved["count"], 1)


if __name__ == "__main__":
    unittest.main()
