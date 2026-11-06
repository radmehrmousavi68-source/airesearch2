import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
import zipfile

from attachment_parser import AttachmentError, extract_attachment


class AttachmentParserTests(unittest.TestCase):
    def test_plain_markdown_is_extracted(self):
        result = extract_attachment("README.md", "# Hello\n\nسلام".encode("utf-8"))
        self.assertIn("# Hello", result["text"])
        self.assertIn("سلام", result["text"])
        self.assertEqual(result["images"], [])

    def test_zip_extracts_supported_text_without_writing_files(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("docs/README.md", "# Project")
            archive.writestr("src/main.py", "print('safe')")
            archive.writestr("ignored.bin", b"\x00\x01")
        result = extract_attachment("project.zip", buffer.getvalue())
        self.assertIn("docs/README.md", result["text"])
        self.assertIn("print('safe')", result["text"])
        self.assertNotIn("ignored.bin", result["text"])

    def test_zip_path_traversal_is_not_extracted(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../outside.txt", "do not write")
            archive.writestr("safe.txt", "inside")
        result = extract_attachment("project.zip", buffer.getvalue())
        self.assertIn("ناامن", result["text"])
        self.assertIn("inside", result["text"])

    def test_bad_zip_is_rejected(self):
        with self.assertRaises(AttachmentError):
            extract_attachment("broken.zip", b"not a zip")

    def test_unsupported_file_is_rejected(self):
        with self.assertRaises(AttachmentError):
            extract_attachment("payload.exe", b"MZ")

    def test_file_size_limit_is_enforced(self):
        with self.assertRaises(AttachmentError):
            extract_attachment("large.txt", b"x" * (12 * 1024 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()
