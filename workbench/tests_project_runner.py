import shutil
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch

from django.test import SimpleTestCase, RequestFactory

from .project_runner import project_resume, execute_project_run, strip_project_citations
from .project_sqlplus import CLIENT_COMMAND, SQLPlusError, SQLPlusInputRequired, SQLPlusSession, wire_statement, validate_project_command, validate_sandbox
from .sql_lexer import split_statements


class ProjectRunnerStatementTests(SimpleTestCase):
    def test_trailing_pasted_citations_do_not_become_commands(self):
        source = "-- lab\nCREATE TABLE employee (id NUMBER);[cite: 12]\nSELECT '[cite: 13]' AS note FROM dual;[cite: 13, 14]\n"
        cleaned = strip_project_citations(source)
        self.assertIn("'[cite: 13]'", cleaned)
        self.assertNotIn(';[cite:', cleaned)
        statements = split_statements(cleaned, sqlplus=True, client_command=CLIENT_COMMAND)
        self.assertEqual(len(statements), 2)
        for statement in statements:
            validate_project_command(statement)

    def test_three_corrections_per_statement_excluding_initial_failure(self):
        from .project_runner import correction_attempts_remaining
        run = SimpleNamespace(next_index=0, results=[{'index': 0, 'status': 'error'}])
        self.assertEqual(correction_attempts_remaining(run), 3)
        for remaining in (2, 1, 0):
            run.results.append({'type': 'correction_attempt', 'index': 0})
            self.assertEqual(correction_attempts_remaining(run), remaining)
        run.next_index = 1
        self.assertEqual(correction_attempts_remaining(run), 3)

    def test_admin_policy_matches_compiler(self):
        from .views import blocked_admin_command
        for statement in ['GRANT CONNECT TO someone', 'REVOKE RESOURCE FROM someone',
                          'SET ROLE ALL', 'CREATE USER someone IDENTIFIED BY secret',
                          '-- note\nALTER SYSTEM SET processes=100', 'DROP USER someone',
                          "BEGIN EXECUTE IMMEDIATE 'ALTER USER someone ACCOUNT UNLOCK'; END;"]:
            with self.subTest(statement=statement):
                blocked = blocked_admin_command(statement)
                self.assertIsNotNone(blocked)
                with self.assertRaisesRegex(SQLPlusError, blocked + ' is disabled'):
                    validate_project_command(statement)
        validate_project_command('-- note\nCREATE TABLE employee (id NUMBER);')

    def test_privileged_logins_rejected_before_driver_connect(self):
        for username in ['SYS', 'SYSTEM', 'SYS AS SYSDBA', '"SYS"']:
            with self.subTest(username=username), patch('workbench.views.open_oracle_connection') as connect:
                with self.assertRaises(ValueError):
                    validate_sandbox(username, 'secret', 'db/service')
                connect.assert_not_called()

    def test_effective_privileges_checked_and_connection_closed(self):
        connection = Mock()
        with patch('workbench.views.open_oracle_connection', return_value=connection), patch('workbench.views.connect_info', side_effect=ValueError('elevated system privileges')) as check:
            with self.assertRaisesRegex(SQLPlusError, 'elevated system privileges'):
                validate_sandbox('PROJECT_USER', 'secret', 'db/service')
        check.assert_called_once_with(connection, 'db/service', 'PROJECT_USER')
        connection.close.assert_called_once()

    def test_client_commands_split_without_semicolons(self):
        source = "SET SERVEROUTPUT ON\nCREATE TABLE employee (id NUMBER);\nDESC EMPLOYEE\nVARIABLE n NUMBER\nEXEC :n := 42\nPRINT n\nSELECT * FROM employee;\n/"
        parts = split_statements(source, sqlplus=True, client_command=CLIENT_COMMAND)
        self.assertEqual(len(parts), 8)
        self.assertEqual(parts[-1], '/')

    def test_multiline_sql_and_plsql_are_not_split_at_client_keywords(self):
        source = 'UPDATE employee\nSET id=2;\nBEGIN\n NULL;\nEND;\n/\nSHOW USER'
        parts = split_statements(source, sqlplus=True, client_command=CLIENT_COMMAND)
        self.assertEqual(len(parts), 3)
        self.assertIn('SET id=2;', parts[0])
        self.assertEqual(wire_statement(parts[1]), 'BEGIN\n NULL;\nEND;\n/\n')

    def test_sql_terminators_do_not_repeat_statements(self):
        self.assertEqual(wire_statement('SELECT 1 FROM dual; -- note'), 'SELECT 1 FROM dual; -- note\n')
        self.assertEqual(wire_statement('SELECT 1 FROM dual -- note'), 'SELECT 1 FROM dual -- note\n;\n')
        self.assertEqual(wire_statement('EXEC p()'), 'EXEC p()\n')

    def test_connection_and_protocol_changes_are_rejected_before_execution(self):
        session = SQLPlusSession.__new__(SQLPlusSession)
        for text in ['CONNECT / AS SYSDBA', 'SELECT 1\n#CONNECT / AS SYSDBA', 'SET CMDSEP +', '&command', 'ACCEPT name', 'WHENEVER SQLERROR CONTINUE']:
            with self.subTest(text=text), self.assertRaises(SQLPlusError):
                session.execute(text)

    def run_request(self, execute, pending_inputs=None):
        run = Mock(pk=123, next_index=0, statements=['PROMPT ready', 'SELECT 1 FROM dual;'], results=[], status='pending')
        run.name = 'Test project'
        run.activity_kind = 'file'
        run.setup_statement_count = 0
        run.schema_name = ''
        run.schema_expires_at = None
        run.sandbox_connection.name = 'Shared sandbox'
        run.sandbox_connection.username = 'PROJECT_USER'
        client = Mock(next_index=0)
        client.pending_inputs = pending_inputs or []
        client.lock.acquire.return_value = True
        client.execute.side_effect = execute
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ('DB', 'SERVICE', 'PDB', 'USER', 'SCHEMA')
        request = RequestFactory().post('/api/teacher/project-runs/123/resume', '{}', content_type='application/json')
        request.user = SimpleNamespace(is_authenticated=True, is_staff=True)
        with patch('workbench.project_runner.renew_schema'), patch('workbench.project_runner.remove_schema'), patch('workbench.project_runner.same_origin', return_value=True), patch('workbench.project_runner.connect_info'), patch('workbench.project_runner.ProjectExecution.objects') as manager, patch('workbench.project_runner.project_session', return_value=client), patch('workbench.project_runner.close_project_session') as close, patch('workbench.project_runner.open_oracle_connection', return_value=connection):
            manager.select_related.return_value.get.return_value = run
            manager.filter.return_value.exists.return_value = False
            response = execute_project_run(run, {})
        return response, run, client, close

    def test_error_pauses_before_following_statement(self):
        response, run, client, close = self.run_request([SQLPlusError('SP2-0734: unknown command')])
        self.assertEqual(response.status_code, 422)
        self.assertEqual(run.next_index, 0)
        self.assertEqual(run.status, 'paused')
        self.assertEqual(client.execute.call_count, 1)
        close.assert_not_called()
        client.lock.release.assert_called_once()

    def test_failed_variable_statement_offers_inputs_again(self):
        for kinds in [('substitution', 'substitution'), ('bind', 'bind'), ('substitution', 'bind')]:
            with self.subTest(kinds=kinds):
                inputs = [{'kind': kinds[0], 'name': 'EMP_ID'},
                          {'kind': kinds[1], 'name': 'EMP_NAME'}]
                response, run, client, close = self.run_request(
                    [SQLPlusError('ORA-12899: value too large')], pending_inputs=inputs)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(run.results[-1]['inputs'], inputs)
                self.assertEqual(run.next_index, 0)
                self.assertEqual(run.status, 'paused')
                self.assertIn('ORA-12899', run.results[-1]['error'])
                close.assert_not_called()

    def test_native_output_is_saved_and_session_closed_after_success(self):
        response, run, client, close = self.run_request([('ready', False), ('1', False)])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(run.next_index, 2)
        self.assertEqual(run.results[1]['output'], 'ready')
        self.assertEqual(run.status, 'completed')
        close.assert_called_once_with(123)

    def test_exit_stops_remaining_commands(self):
        response, run, client, close = self.run_request([('exit', True)])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.execute.call_count, 1)
        self.assertEqual(run.next_index, 1)


class NativeSQLPlusTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not shutil.which('sqlplus'):
            import unittest
            raise unittest.SkipTest('SQL*Plus is not installed')

    def setUp(self):
        self.session = SQLPlusSession()
        self.addCleanup(self.session.close)

    def test_variables_persist_across_commands(self):
        self.session.execute('DEFINE portal_value = hello')
        self.assertEqual(self.session.execute('PROMPT &portal_value'), ('hello', False))
        self.session.execute('SET HEADING OFF')
        output, _ = self.session.execute('SHOW HEADING')
        self.assertIn('OFF', output.upper())

    def test_substitution_cannot_bypass_admin_policy(self):
        self.session.execute('DEFINE target = SYSTEM')
        with self.assertRaisesRegex(SQLPlusError, 'ALTER SYSTEM is disabled'):
            self.session.execute('ALTER &target SET processes=100;')
        self.session.execute('SET DEFINE ^')
        with self.assertRaisesRegex(SQLPlusError, 'ALTER SYSTEM is disabled'):
            self.session.execute('ALTER ^target SET processes=100;')
        self.session.execute('SET MARKUP HTML ON')
        with self.assertRaisesRegex(SQLPlusError, 'ALTER SYSTEM is disabled'):
            self.session.execute('ALTER ^target SET processes=100;')

    def test_buffer_edits_cannot_bypass_admin_policy(self):
        with self.assertRaises(SQLPlusError):
            self.session.execute('SELECT 1 FROM dual;')  # /nolog, but buffer is retained
        self.session.execute('CHANGE /SELECT 1 FROM dual/ALTER SYSTEM SET processes=100/')
        for command in ['/', 'RUN']:
            with self.subTest(command=command), self.assertRaisesRegex(SQLPlusError, 'ALTER SYSTEM is disabled'):
                self.session.execute(command)

    def test_undefined_substitution_fails_without_prompting(self):
        with self.assertRaises(SQLPlusError):
            self.session.execute('PROMPT &portal_undefined_value')
        self.assertEqual(self.session.execute('PROMPT recovered'), ('recovered', False))

    def test_missing_substitutions_can_be_supplied_together(self):
        with self.assertRaises(SQLPlusInputRequired) as raised:
            self.session.execute('PROMPT &emp_id &emp_name')
        self.assertEqual({item['name'] for item in raised.exception.requests}, {'EMP_ID', 'EMP_NAME'})
        self.session.provide_inputs([{'kind': 'substitution', 'name': 'EMP_ID', 'value': 'E01'},
                                    {'kind': 'substitution', 'name': 'EMP_NAME', 'value': 'Alice'}])
        self.assertEqual(self.session.execute('PROMPT &emp_id &emp_name'), ('E01 Alice', False))
        self.session.substitution_inputs['EMP_NAME'] = 'R&D'
        self.assertEqual(self.session.execute('PROMPT &emp_name'), ('R&D', False))

    def test_supplied_substitution_keeps_admin_check(self):
        with self.assertRaises(SQLPlusInputRequired):
            self.session.execute('ALTER &target SET processes=100;')
        self.session.provide_inputs([{'kind': 'substitution', 'name': 'TARGET', 'value': 'SYSTEM'}])
        with self.assertRaisesRegex(SQLPlusError, 'ALTER SYSTEM is disabled'):
            self.session.execute('ALTER &target SET processes=100;')

    def test_undeclared_binds_request_values_and_types(self):
        with self.assertRaises(SQLPlusInputRequired) as raised:
            self.session.execute('SELECT :employee_id, :salary FROM dual;')
        self.assertEqual(raised.exception.requests, [{'kind': 'bind', 'name': 'EMPLOYEE_ID'}, {'kind': 'bind', 'name': 'SALARY'}])

    def test_semicolon_on_bind_declaration_is_removed(self):
        self.session.execute('VARIABLE employee_id NUMBER;')
        self.assertIn('employee_id', self.session._exchange('VARIABLE\n'))

    def test_variable_values_cannot_add_native_commands(self):
        self.session.pending_inputs = [{'kind': 'substitution', 'name': 'NAME'}]
        with self.assertRaises(SQLPlusError):
            self.session.provide_inputs([{'kind': 'substitution', 'name': 'NAME', 'value': 'Alice\nCONNECT / AS SYSDBA'}])

    def test_native_error_does_not_destroy_session(self):
        with self.assertRaises(SQLPlusError):
            self.session.execute('UNKNOWN_PORTAL_COMMAND')
        self.assertEqual(self.session.execute('PROMPT recovered'), ('recovered', False))

    def test_restricted_mode_blocks_host_and_server_files(self):
        for text in ['HOST echo forbidden', 'SPOOL forbidden.txt', '@missing.sql', 'GET missing.sql']:
            with self.subTest(text=text), self.assertRaises(SQLPlusError):
                self.session.execute(text)

    def test_exit_ends_native_process(self):
        output, exited = self.session.execute('EXIT')
        self.assertTrue(exited)
