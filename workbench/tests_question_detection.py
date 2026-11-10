from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from .daily_tasks import DailyTaskForm
from .daily_task_workflow import stage_import
from .portal_views import _questions_from_text, _questions_from_upload


class PastedQuestionDetectionTests(SimpleTestCase):
    def test_paste_and_file_detect_identical_questions(self):
        examples = [
            "1. Find employees\nInclude their names.\n2. Count departments",
            "Q1: Find employees\nSELECT * FROM emp;\nQuestion 2) Count departments",
            "- Find employees\nInclude their names.\n# Count departments",
            "/* Find employees */\nSELECT * FROM emp;\n/* Count departments */",
            "Find employees\nCount departments",
        ]
        for text in examples:
            with self.subTest(text=text):
                upload = SimpleUploadedFile("questions.txt", text.encode())
                self.assertEqual(_questions_from_text(text), _questions_from_upload(upload))
                self.assertEqual(len(_questions_from_text(text)), 2)

    def form(self, text, upload=None):
        form = DailyTaskForm(
            data={"title": "Practice", "instructions": text,
                  "general_instructions": "Explain your answer.", "late_policy": "allow"},
            files={"question_file": upload} if upload else None,
        )
        form.is_valid()
        return form

    def test_paste_and_upload_merge_and_stage_individually(self):
        form = self.form("1. Find employees\nInclude names.\n2. Count departments",
                         SimpleUploadedFile("questions.txt", b"1. List jobs"))
        self.assertFalse(form.errors)
        self.assertEqual(form.cleaned_data["questions"],
                         ["Find employees\nInclude names.", "Count departments", "List jobs"])
        from types import SimpleNamespace
        _, preview = stage_import(SimpleNamespace(pk=1), form.cleaned_data)
        self.assertEqual(len(preview.forms), 3)
        for row in preview.forms:
            self.assertEqual(row.initial["general_instructions"], "Explain your answer.")

    def test_combined_question_limit_is_enforced(self):
        text = "\n".join(f"{index}. Question {index}" for index in range(1, 201))
        form = self.form(text, SimpleUploadedFile("questions.txt", b"Extra question"))
        self.assertIn("question_file", form.errors)
        form = self.form(text + "\n201. Extra question")
        self.assertIn("instructions", form.errors)

    def test_empty_question_is_rejected(self):
        self.assertIn("instructions", self.form("  ").errors)
