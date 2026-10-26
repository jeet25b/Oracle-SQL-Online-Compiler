import re
from django.test import SimpleTestCase
from .sql_lexer import split_statements, bind_names, prepare_statement


class SqlLexicalTests(SimpleTestCase):
    def test_execution_copy_repairs_suffix_and_reduces_extra_whitespace(self):
        original = 'INSERT   INTO EMPLOYEE  SELECT * FROM EMPLOYEE   _BACKUP;'
        self.assertEqual(prepare_statement(original), 'INSERT INTO EMPLOYEE SELECT * FROM EMPLOYEE_BACKUP')
        self.assertIn('EMPLOYEE   _BACKUP', original)

    def test_whitespace_cleanup_preserves_literals_names_comments_and_aliases(self):
        source = 'SELECT  \'two  spaces\', q\'[keep   this]\', "EMPLOYEE _BACKUP" FROM  EMPLOYEE e -- keep  comment\n WHERE  e.ID = 1;'
        result = prepare_statement(source)
        self.assertIn("'two  spaces'", result)
        self.assertIn("q'[keep   this]'", result)
        self.assertIn('"EMPLOYEE _BACKUP"', result)
        self.assertIn('EMPLOYEE e -- keep  comment\nWHERE', result)

    def test_sql_and_quoted_data(self):
        sql = "SELECT q'[isn't; :fake]' FROM DUAL; SELECT ';' FROM DUAL;"
        self.assertEqual(len(split_statements(sql)), 2)
        self.assertEqual(bind_names(sql), set())

    def test_plsql_and_comment_header(self):
        sql = "-- comment\nBEGIN :x := :y; NULL; END;\n/\nSELECT 1 FROM DUAL;"
        parts = split_statements(sql)
        self.assertEqual(len(parts), 2)
        self.assertTrue(prepare_statement(parts[0]).endswith('END;'))
        self.assertFalse(prepare_statement(parts[1]).endswith(';'))
        self.assertEqual(bind_names(parts[0]), {'X', 'Y'})

    def test_sqlplus_lines(self):
        commands = re.compile(r'^(SET|DESC|PROMPT)\b', re.I)
        parts = split_statements("SET HEADING OFF\nSELECT 1 FROM DUAL;\nDESC EMP", True, commands)
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0], 'SET HEADING OFF')

    def test_stored_program_and_multiline_literal(self):
        sql = "CREATE OR REPLACE PROCEDURE p AS BEGIN x := q'[a\n; b]'; NULL; END;\n/\nSELECT 1 FROM DUAL;"
        parts = split_statements(sql)
        self.assertEqual(len(parts), 2)
        self.assertTrue(prepare_statement(parts[0]).endswith('END;'))

    def test_ignore_literals_identifiers_and_comments_for_binds(self):
        self.assertEqual(bind_names("SELECT ':skip', q'[isn't :fake]', :id, :ID, :1 FROM DUAL /* :no */"), {'ID', '1'})

    def test_statement_mode_rejects_batch_before_database_execution(self):
        from unittest.mock import patch
        from django.test import RequestFactory
        from .views import execute_sql
        request = RequestFactory().post('/api/execute', {'sql': 'SELECT 1 FROM DUAL; SELECT 2 FROM DUAL;', 'executionMode': 'statement'}, content_type='application/json')
        with patch('workbench.views.same_origin', return_value=True), patch('workbench.views.current_oracle_session', return_value={'settings': {}}), patch('workbench.views.open_oracle_connection') as connect:
            response = execute_sql(request)
        self.assertEqual(response.status_code, 400)
        connect.assert_not_called()
