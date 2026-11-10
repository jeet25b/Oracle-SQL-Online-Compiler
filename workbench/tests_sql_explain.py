from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from .views import _explain_plan_lines


class ExplainPlanResultTests(SimpleTestCase):
    @patch("workbench.views.open_oracle_connection")
    def test_dbms_xplan_text_and_plan_table_rows_form_a_hierarchy(self, open_connection):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.__exit__.return_value = False
        cursor.fetchmany.return_value = [
            ("Plan hash value: 123",),
            ("| Id | Operation        | Name |",),
            ("|  0 | SELECT STATEMENT |      |",),
            ("|  1 | TABLE ACCESS FULL | EMP  |",),
        ]
        cursor.fetchall.return_value = [
            (0, None, "SELECT STATEMENT", None, None, None, 1, 2),
            (1, 0, "TABLE ACCESS", "FULL", "APP", "EMP", 1, 2),
        ]
        connection = MagicMock()
        connection.cursor.side_effect = [cursor, cursor]
        open_connection.return_value = connection

        result = _explain_plan_lines(
            {"user": "APP", "password": "unused", "connectString": "db"},
            "SELECT * FROM EMP",
        )

        self.assertEqual(result["lines"][0], "Plan hash value: 123")
        self.assertEqual(len(result["tree"]), 1)
        root = result["tree"][0]
        self.assertEqual(root["operation"], "SELECT STATEMENT")
        self.assertEqual(root["children"][0]["operation"], "TABLE ACCESS FULL · APP.EMP")
        self.assertEqual(root["children"][0]["estimatedRows"], 1)
        self.assertTrue(any("DELETE FROM PLAN_TABLE" in call.args[0] for call in cursor.execute.call_args_list))
