import io
import zipfile
from types import SimpleNamespace
from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, RequestFactory
from .question_import import parse_text, parse_upload, preview_upload
from .daily_tasks import DailyTaskForm
from .daily_task_workflow import stage_import, commit_import
from .portal_views import portal_tests
from .models import AssessmentDefinition, PortalUser

SAMPLE = "Q1. DISPLAY ALL EMPLOYEES.\n\nSQL STATEMENT :-\nSELECT *\nFROM employee;\n\nQ2. DISPLAY EMPLOYEES FROM GWALIOR.\nSQL STATEMENT :-\nSELECT * FROM employee WHERE city = 'Gwalior';"


class QuestionImportTests(SimpleTestCase):
    def upload(self, text=SAMPLE, name="DQL.txt"):
        return SimpleUploadedFile(name, text.encode("utf-8"))

    def test_dql_format_extracts_pairs_and_preserves_sql(self):
        records=parse_upload(self.upload())
        self.assertEqual(len(records),2)
        self.assertEqual(records[0]["prompt"],"DISPLAY ALL EMPLOYEES.")
        self.assertEqual(records[0]["expected_sql"],"SELECT *\nFROM employee;")
        self.assertIn("'Gwalior'",records[1]["expected_sql"])
        self.assertNotIn("SQL STATEMENT :-",records[0]["prompt"])

    def test_answer_heading_aliases_markdown_and_inline_sql(self):
        for label in ("Answer:","ANS :-", "Reference SQL answer:","**Reference SQL answer:**", "SQL Answer:","Solution:"):
            with self.subTest(label=label):
                record=parse_text("Q1. Select one.\n"+label+" SELECT 1 FROM dual;")[0]
                self.assertEqual(record["expected_sql"],"SELECT 1 FROM dual;")

    def test_sql_comments_numbers_fences_and_multiline_literals_are_preserved(self):
        source="Q1. Show text.\nAnswer:\n```sql\n-- preserve this comment\nSELECT 'first\nQ2. part of the string' FROM dual;\n```\nQ2. Select two.\nAnswer:\nSELECT 2 FROM dual;"
        records=parse_text(source)
        self.assertEqual(len(records),2)
        self.assertIn("-- preserve this comment",records[0]["expected_sql"])
        self.assertIn("Q2. part of the string",records[0]["expected_sql"])
        self.assertNotIn("```",records[0]["expected_sql"])
        raw="Q1. Show text.\nAnswer:\nSELECT q'[start\nQ2. literal text]' FROM dual;\nQ2. Real question.\nAnswer: SELECT 2 FROM dual;"
        self.assertEqual(len(parse_text(raw)),2)

    def test_empty_answer_rejected_and_missing_answer_stays_empty(self):
        with self.assertRaisesRegex(ValueError,"no SQL"):
            parse_text("Q1. Question\nAnswer:\nQ2. Another\nAnswer: SELECT 2 FROM dual;")
        records=parse_text("Q1. No supplied answer.\nQ2. Answered.\nAnswer: SELECT 2 FROM dual;")
        self.assertEqual(records[0]["expected_sql"],"")

    def test_csv_extracts_named_columns(self):
        source=io.StringIO()
        import csv
        writer=csv.writer(source);writer.writerow(["Question","Reference SQL answer","Verification SQL"])
        writer.writerow(["Select one","SELECT 1 FROM dual;","SELECT 2 FROM dual;"])
        record=parse_upload(self.upload(source.getvalue(),"questions.csv"))[0]
        self.assertEqual(record["expected_sql"],"SELECT 1 FROM dual;")
        self.assertEqual(record["verification_sql"],"SELECT 2 FROM dual;")

    def test_docx_paragraphs_extract_reference(self):
        data=io.BytesIO()
        xml='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Q1. Select employees.</w:t></w:r></w:p><w:p><w:r><w:t>SQL STATEMENT :-</w:t></w:r></w:p><w:p><w:r><w:t>SELECT *</w:t><w:br/><w:t>FROM employee;</w:t></w:r></w:p></w:body></w:document>'
        with zipfile.ZipFile(data,"w") as docx: docx.writestr("word/document.xml",xml)
        record=parse_upload(SimpleUploadedFile("questions.docx",data.getvalue()))[0]
        self.assertEqual(record["expected_sql"],"SELECT *\nFROM employee;")

    def test_starter_verification_and_reference_remain_separate(self):
        record=parse_text("Q1. Select one.\nStarter SQL: SELECT\nReference SQL answer: SELECT 1 FROM dual;\nVerification SQL: SELECT 2 FROM dual;")[0]
        self.assertEqual(record["starter_sql"],"SELECT")
        self.assertEqual(record["expected_sql"],"SELECT 1 FROM dual;")
        self.assertEqual(record["verification_sql"],"SELECT 2 FROM dual;")

    def test_daily_preview_receives_each_answer(self):
        form=DailyTaskForm({"title":"DQL","grading_mode":"result_match"},{"question_file":self.upload()})
        self.assertTrue(form.is_valid(),form.errors)
        _,preview=stage_import(SimpleNamespace(pk=1),form.cleaned_data)
        self.assertEqual(len(preview.forms),2)
        for row,record in zip(preview.forms,parse_text(SAMPLE)):
            self.assertEqual(row.initial["expected_sql"],record["expected_sql"])
            self.assertEqual(row.initial["instructions"],record["prompt"])


class QuestionImportFlowTests(TestCase):
    def setUp(self):
        import uuid
        key=uuid.uuid4().hex[:8]
        self.teacher=PortalUser.objects.create_user(username="import-teacher-"+key,role="TEACHER")
        self.student=PortalUser.objects.create_user(username="import-student-"+key,role="STUDENT")

    def request(self,data,user=None):
        request=RequestFactory().post("/portal/tests/",data)
        request.user=user or self.teacher;request.session={"portal_active_role":request.user.role}
        return request

    def test_test_upload_saves_separate_questions_and_answers(self):
        response=portal_tests(self.request({"title":"Imported DQL","grading_mode":"result_match","question_file":SimpleUploadedFile("DQL.txt",SAMPLE.encode())}))
        self.assertEqual(response.status_code,302)
        entry=AssessmentDefinition.objects.get(created_by=self.teacher,title="Imported DQL")
        self.assertEqual(len(entry.payload["questions"]),2)
        for item,record in zip(entry.payload["questions"],parse_text(SAMPLE)):
            self.assertEqual(item["prompt"],record["prompt"])
            self.assertEqual(item["expected_sql"],record["expected_sql"])

    def test_browser_parsed_upload_is_not_added_twice(self):
        records=parse_text(SAMPLE)
        response=portal_tests(self.request({"title":"Parsed DQL","grading_mode":"result_match","question_file_parsed":"1","question":[r["prompt"] for r in records],"expected_sql":[r["expected_sql"] for r in records],"question_file":SimpleUploadedFile("DQL.txt",SAMPLE.encode())}))
        self.assertEqual(response.status_code,302)
        entry=AssessmentDefinition.objects.get(created_by=self.teacher,title="Parsed DQL")
        self.assertEqual(len(entry.payload["questions"]),2)

    def test_preview_endpoint_is_teacher_only_and_does_not_save_content(self):
        data=lambda:{"question_file":SimpleUploadedFile("DQL.txt",SAMPLE.encode())}
        with patch("workbench.views.same_origin",return_value=True):
            response=preview_upload(self.request(data()))
            self.assertEqual(response.status_code,200)
            self.assertEqual(preview_upload(self.request(data(),self.student)).status_code,403)
        self.assertFalse(AssessmentDefinition.objects.filter(created_by=self.teacher).exists())
