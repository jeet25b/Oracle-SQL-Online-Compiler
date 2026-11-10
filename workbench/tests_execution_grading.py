from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch, Mock
from django.test import SimpleTestCase
from .execution_grading import GradingUnavailable, equivalent, grade_question, statements, validate_reference, _implicit_results


class ExecutionGradingTests(SimpleTestCase):
    def outcome(self):
        return {"state": {"objects": [("T", "TABLE", "VALID")], "data": {"T": ["1"]}},
                "execution": {"queries": [], "output": []}, "verification": {"queries": [], "output": []}}

    def question(self):
        return SimpleNamespace(expected_sql="UPDATE t SET n=2;", verification_sql="", marks=Decimal("5"))

    def test_data_and_definition_statements_are_accepted(self):
        for source in ["INSERT INTO t VALUES(1);", "UPDATE t SET n=2;", "DELETE FROM t;", "CREATE TABLE t(n NUMBER);", "ALTER TABLE t ADD m NUMBER;", "DROP TABLE t;", "TRUNCATE TABLE t;", "BEGIN DBMS_OUTPUT.PUT_LINE(2); END;", "COMMIT;", "ROLLBACK;"]:
            with self.subTest(source=source): validate_reference(source)

    def test_stored_program_requires_behavioral_verification(self):
        reference="CREATE OR REPLACE PROCEDURE p AS BEGIN NULL; END;"
        with self.assertRaisesRegex(ValueError, "verification"):
            validate_reference(reference)
        validate_reference(reference, "BEGIN p; END;")

    def test_admin_and_interactive_commands_are_rejected(self):
        for source in ("DROP USER x CASCADE;", "GRANT DBA TO x;", "HOST whoami", "CONNECT another/password", "SELECT :missing FROM dual", "SELECT &input FROM dual"):
            with self.subTest(source=source), self.assertRaises(ValueError): statements(source)

    def test_exec_is_converted_to_plsql_and_literals_preserved(self):
        self.assertEqual(statements("EXEC p(2);"), ["BEGIN p(2); END;"])
        validate_reference("SELECT 'a&b' FROM dual;")

    def test_trigger_correlation_names_are_not_external_binds(self):
        validate_reference("CREATE OR REPLACE TRIGGER t BEFORE INSERT ON tab FOR EACH ROW BEGIN :NEW.n := :NEW.n+1; END;", "INSERT INTO tab VALUES (1);")

    def test_equal_output_with_wrong_table_data_is_incorrect(self):
        expected=self.outcome();actual=deepcopy(expected);actual["state"]["data"]["T"]=["2"]
        with patch("workbench.execution_grading.execute_isolated", side_effect=[expected,actual]):
            grade=grade_question(SimpleNamespace(), self.question(), "UPDATE t SET n=3;", "CREATE TABLE t(n NUMBER);")
        self.assertFalse(grade["is_correct"])
        self.assertEqual(grade["score"], Decimal("0"))

    def test_different_source_with_identical_effects_earns_marks(self):
        expected=self.outcome()
        with patch("workbench.execution_grading.execute_isolated", side_effect=[expected,deepcopy(expected)]) as execute:
            grade=grade_question(SimpleNamespace(), self.question(), "UPDATE t SET n=1+1;", "CREATE TABLE t(n NUMBER);")
        self.assertTrue(grade["is_correct"])
        self.assertEqual(grade["score"], Decimal("5"))
        self.assertEqual(execute.call_args_list[0].args[2], execute.call_args_list[1].args[2])

    def test_broken_reference_is_pending_not_incorrect(self):
        with patch("workbench.execution_grading.execute_isolated", side_effect=ValueError("broken setup")), self.assertRaises(GradingUnavailable):
            grade_question(SimpleNamespace(), self.question(), "UPDATE t SET n=2;")

    def test_candidate_oracle_error_is_incorrect(self):
        with patch("workbench.execution_grading.execute_isolated", side_effect=[self.outcome(), ValueError("ORA-00942")]):
            grade=grade_question(SimpleNamespace(), self.question(), "UPDATE missing SET n=2;")
        self.assertFalse(grade["is_correct"])

    def test_connection_failure_and_size_limits_are_pending(self):
        for error in (ValueError("ORA-03113"), GradingUnavailable("too much output")):
            with self.subTest(error=error), patch("workbench.execution_grading.execute_isolated", side_effect=[self.outcome(),error]), self.assertRaises(GradingUnavailable):
                grade_question(SimpleNamespace(), self.question(), "UPDATE t SET n=2;")

    def test_reference_controls_query_row_order(self):
        expected=self.outcome();actual=deepcopy(expected)
        expected["execution"]["queries"]=[(1,["1","2"],False)]
        actual["execution"]["queries"]=[(1,["2","1"],True)]
        self.assertTrue(equivalent(expected,actual))
        expected["execution"]["queries"]=[(1,["1","2"],True)]
        self.assertFalse(equivalent(expected,actual))

    def test_output_and_duplicate_rows_must_match(self):
        expected=self.outcome();actual=deepcopy(expected)
        expected["verification"]["output"]=["2"]
        actual["verification"]["output"]=["3"]
        self.assertFalse(equivalent(expected,actual))
        actual=deepcopy(expected)
        expected["execution"]["queries"]=[(1,["1","1"],False)]
        actual["execution"]["queries"]=[(1,["1"],False)]
        self.assertFalse(equivalent(expected,actual))

    def test_plsql_without_implicit_cursor_is_valid(self):
        cursor=Mock()
        cursor.getimplicitresults.side_effect=ValueError("DPY-1004: no statement executed")
        self.assertEqual(_implicit_results(cursor), [])
        cursor.getimplicitresults.side_effect=ValueError("ORA-03113: connection lost")
        with self.assertRaises(ValueError): _implicit_results(cursor)
