"""Repository hygiene: this is a public package, and everything in it is written in English."""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# CJK ideographs, kana, hangul and full-width forms, written as code points to keep this file ASCII.
RANGES = ((0x3040, 0x30FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xAC00, 0xD7AF), (0xFF00, 0xFFEF))
FOREIGN_SCRIPT = re.compile("[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in RANGES) + "]")


def tracked_files():
    """Tracked files plus new ones not yet added, so a file is checked before its first commit."""
    try:
        output = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        names = [line for line in output.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        names = []
    if not names:  # not a git checkout (an installed copy): scan the files on disk instead
        names = [
            str(p.relative_to(ROOT))
            for p in ROOT.rglob("*")
            if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts
        ]
    return [ROOT / name for name in names]


class RepositoryTest(unittest.TestCase):
    def test_scanner_detects_a_sample(self):
        # Guards the guard: a broken pattern would make the next test pass on anything.
        self.assertTrue(FOREIGN_SCRIPT.search(chr(0x4E2D)))
        self.assertTrue(FOREIGN_SCRIPT.search(chr(0x30AB)))
        self.assertFalse(FOREIGN_SCRIPT.search("caf" + chr(0xE9) + " " + chr(0xB7) + " " + chr(0x2014)))

    def test_no_foreign_script_in_tracked_files(self):
        files = tracked_files()
        self.assertGreater(len(files), 5, "no files were found to scan")
        offenders = []
        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if FOREIGN_SCRIPT.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
