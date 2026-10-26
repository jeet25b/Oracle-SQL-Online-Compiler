import json
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase, RequestFactory
from .views import execute_sql
from django.http import JsonResponse


class ExecutionHistoryTests(SimpleTestCase):
    def test_describe_in_both_editors_uses_metadata_instead_of_executing_command(self):
        for sqlplus in (False, True):
            for command in ('desc employee', 'DESCRIBE employee;', 'DESC hr.employee;'):
                with self.subTest(sqlplus=sqlplus, command=command):
                    connection = MagicMock()
                    cursor = connection.cursor.return_value.__enter__.return_value
                    cursor.fetchall.return_value = [('ID', 'N', 'NUMBER', 22, 0, None, 10, 0)]
                    request = RequestFactory().post('/api/execute', data=json.dumps({
                        'sql': command, 'sqlplus': sqlplus, 'executionMode': 'statement',
                    }), content_type='application/json')
                    with patch('workbench.views.same_origin', return_value=True), \
                         patch('workbench.views.current_oracle_session', return_value={'sqlplus': connection, 'editor': connection}), \
                         patch('workbench.views.execute_one') as execute, \
                         patch('workbench.views._record_compiler_query'):
                        response = execute_sql(request)
                    self.assertEqual(response.status_code, 200, response.content)
                    self.assertEqual(json.loads(response.content)['rows'], [['ID', 'NOT NULL', 'NUMBER(10)']])
                    self.assertEqual(cursor.execute.call_args.args[1], {
                        'owner': 'HR' if 'hr.' in command else None, 'name': 'EMPLOYEE',
                    })
                    execute.assert_not_called()

    def test_select_date_format_preserves_timestamp_and_null(self):
        from datetime import datetime
        import oracledb
        from .views import json_value
        value = datetime(2026, 10, 20, 14, 30)
        self.assertEqual(json_value(value, oracledb.DB_TYPE_DATE), '20-OCT-26')
        self.assertEqual(json_value(value, oracledb.DB_TYPE_TIMESTAMP), '2026-10-20T14:30:00')
        self.assertIsNone(json_value(None, oracledb.DB_TYPE_DATE))

    def test_sqlplus_undeclared_bind_is_reported_without_oracle_execution(self):
        connection = MagicMock()
        request = RequestFactory().post('/api/execute', data=json.dumps({'sql':'INSERT INTO T VALUES (:COURSE_ID);', 'sqlplus':True}), content_type='application/json')
        with patch('workbench.views.same_origin', return_value=True), patch('workbench.views.current_oracle_session', return_value={'sqlplus':connection}), patch('workbench.views.execute_one') as execute, patch('workbench.views._record_compiler_query'):
            response = execute_sql(request)
        error = json.loads(response.content)['executionErrors'][0]['error']
        self.assertIn('SP2-0552: Bind variable "COURSE_ID" not declared.', error)
        self.assertIn('/sp2-0552/', error)
        execute.assert_not_called()

    def test_unknown_command_reports_sqlplus_error_without_sending_to_oracle(self):
        connection = MagicMock()
        request = RequestFactory().post('/api/execute', data=json.dumps({'sql':'Note:- Table must be empty.', 'sqlplus':True}), content_type='application/json')
        with patch('workbench.views.same_origin', return_value=True), patch('workbench.views.current_oracle_session', return_value={'sqlplus':connection}), patch('workbench.views.execute_one') as execute, patch('workbench.views._record_compiler_query'):
            response = execute_sql(request)
        self.assertIn('SP2-0734', json.loads(response.content)['executionErrors'][0]['error'])
        execute.assert_not_called()

    def test_refresh_request_reuses_result_without_executing_twice(self):
        from .views import _sqlplus_requests
        _sqlplus_requests.clear()
        request = RequestFactory().post('/api/execute', data='{"sql":"DELETE FROM T","sqlplus":true}',
            content_type='application/json', HTTP_X_TERMINAL_REQUEST_ID='refresh-request-test-123')
        request.user = MagicMock(pk=123, is_authenticated=True)
        with patch('workbench.views.same_origin', return_value=True), \
             patch('workbench.views._execute_sql_request', return_value=JsonResponse({'rowsAffected': 2})) as execute:
            self.assertEqual(execute_sql(request).status_code, 200)
            self.assertEqual(execute_sql(request).status_code, 200)
        execute.assert_called_once()
        _sqlplus_requests.clear()

    def test_missing_connection_request_can_retry_after_login(self):
        from .views import _sqlplus_requests
        _sqlplus_requests.clear()
        request = RequestFactory().post('/api/execute', data='{"sql":"SELECT 1 FROM DUAL","sqlplus":true}',
            content_type='application/json', HTTP_X_TERMINAL_REQUEST_ID='refresh-request-test-456')
        request.user = MagicMock(pk=123, is_authenticated=True)
        with patch('workbench.views.same_origin', return_value=True), \
             patch('workbench.views._execute_sql_request', side_effect=[JsonResponse({}, status=401), JsonResponse({'rows': [[1]]})]) as execute:
            self.assertEqual(execute_sql(request).status_code, 401)
            self.assertEqual(execute_sql(request).status_code, 200)
        self.assertEqual(execute.call_count, 2)
        _sqlplus_requests.clear()

    def run_batch(self, sqlplus, outcomes):
        connection = MagicMock()
        session = {"settings": {}, "sqlplus": connection, "editor": connection}
        request = RequestFactory().post('/api/execute', data=json.dumps({
            "sql": 'SELECT * FROM T; DELETE FROM T; INSERT INTO T VALUES (1);',
            "sqlplus": sqlplus, "editSource": {"typedChars": 20},
        }), content_type='application/json')
        with patch('workbench.views.same_origin', return_value=True), \
             patch('workbench.views.current_oracle_session', return_value=session), \
             patch('workbench.views.execute_one', side_effect=outcomes), \
             patch('workbench.views._record_compiler_query') as record:
            response = execute_sql(request)
        return response, record.call_args_list

    def test_both_sources_record_each_statement_and_returned_or_affected_rows(self):
        for sqlplus in (False, True):
            with self.subTest(sqlplus=sqlplus):
                response, records = self.run_batch(sqlplus, [
                    ([{"name": "ID"}], [[1], [2]], None, [], []),
                    ([], [], 2, [], []), ([], [], 1, [], []),
                ])
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(records), 3)
                self.assertEqual([call.args[5] for call in records], [2, 2, 1])
                self.assertEqual([call.args[2] for call in records], ['sqlplus' if sqlplus else 'editor'] * 3)
                self.assertTrue(all(call.args[3] == 'success' for call in records))
                self.assertEqual(records[0].args[7], {"typedChars": 20})
                self.assertIsNone(records[1].args[7])

    def test_failure_keeps_prior_history_without_recording_unexecuted_statements(self):
        for sqlplus in (False, True):
            response, records = self.run_batch(sqlplus, [
                ([{"name": "ID"}], [[1]], None, [], []),
                ValueError('Delete failed'), ([], [], 1, [], []),
            ])
            self.assertEqual(response.status_code, 200 if sqlplus else 400)
            self.assertEqual(len(records), 3 if sqlplus else 2)
            self.assertEqual(records[0].args[3:6:2], ('success', 1))
            self.assertEqual(records[1].args[3:6:2], ('error', 0))
            self.assertEqual(records[1].args[6], 'Delete failed')
