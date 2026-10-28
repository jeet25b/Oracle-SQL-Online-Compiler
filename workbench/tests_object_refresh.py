import json
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase, RequestFactory
from .object_refresh import metadata_refresh
from .views import list_objects, object_details


class ObjectRefreshTests(SimpleTestCase):
    def test_constraints_use_scoped_dictionary_and_metadata_timeout(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.description = [('CONSTRAINT_TYPE',), ('REFERENCED_TABLE_NAME',)]
        cursor.fetchall.return_value = [('R', 'DEPARTMENTS'), ('P', None)]
        state = {'settings': {'user': 'TEST', 'connectString': 'example'}}
        with patch('workbench.views.current_oracle_session', return_value=state), patch('workbench.views.is_default_connection', return_value=False), patch('workbench.views.connect_options', return_value={}), patch('workbench.views.open_oracle_connection', return_value=conn):
            response = object_details(RequestFactory().get('/api/objects/TEST/T', {'view': 'constraints'}), 'TEST', 'T')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(conn.call_timeout, 30000)
        query = cursor.execute.call_args.args[0]
        self.assertIn('FROM user_constraints c', query)
        self.assertIn('LEFT JOIN user_cons_columns cc', query)
        self.assertIn('ref.owner=c.r_owner', query)
        self.assertEqual(json.loads(response.content)['constraints'][0]['REFERENCED_TABLE_NAME'], 'DEPARTMENTS')
        conn.close.assert_called_once()

    def plan(self, *sql):
        return metadata_refresh([{"statement": text} for text in sql])

    def test_reads_and_data_changes_skip_metadata(self):
        self.assertEqual(self.plan("SELECT 'CREATE TABLE X' FROM dual", "INSERT INTO T VALUES (1)", "UPDATE T SET X=2", "DELETE FROM T", "COMMIT", "-- DROP TABLE T\nSELECT 1 FROM dual"), {"full": False, "names": []})

    def test_common_ddl_targets_quoted_and_qualified_names(self):
        self.assertEqual(self.plan('CREATE TABLE HR."My Table" (ID NUMBER)', 'ALTER TABLE T ADD X NUMBER', 'CREATE OR REPLACE VIEW V AS SELECT * FROM T', 'CREATE UNIQUE INDEX IX ON T(X)'), {"full": False, "names": ["IX", "My Table", "T", "V"]})

    def test_complex_and_dynamic_changes_fall_back(self):
        for sql in ('DROP TABLE T CASCADE CONSTRAINTS', 'ALTER TABLE T RENAME TO U', 'ALTER TABLE T DROP COLUMN X', "BEGIN EXECUTE IMMEDIATE 'CREATE TABLE T(X NUMBER)'; END;", 'GRANT SELECT ON T TO U'):
            self.assertTrue(self.plan(sql)["full"])

    def test_failed_ddl_is_not_refreshed(self):
        self.assertEqual(metadata_refresh([{"statement": "CREATE TABLE T(X NUMBER)", "error": "already exists"}])["names"], [])

    def test_target_query_binds_names_and_keeps_owner_restriction(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.description = [(name,) for name in ("OWNER", "OBJECT_NAME", "OBJECT_TYPE", "STATUS")]
        cursor.fetchall.return_value = []
        state = {"settings": {"connectString": "example", "user": "TEST"}}
        request = RequestFactory().get('/api/objects', {"names": json.dumps(["T'X"])})
        with patch('workbench.views.current_oracle_session', return_value=state), patch('workbench.views.is_default_connection', return_value=True), patch('workbench.views.connect_options', return_value={}), patch('workbench.views.open_oracle_connection', return_value=conn):
            response = list_objects(request)
        self.assertEqual(response.status_code, 200)
        for call in cursor.execute.call_args_list:
            query, binds = call.args
            self.assertIn('owner = USER', query)
            self.assertIn(':name0', query)
            self.assertNotIn("T'X", query)
            self.assertEqual(binds, {"name0": "T'X"})
        self.assertEqual(conn.call_timeout, 30000)
        conn.close.assert_called_once()

    def test_invalid_scope_does_not_open_connection(self):
        with patch('workbench.views.current_oracle_session', return_value={"settings": {"connectString": "x", "user": "TEST"}}), patch('workbench.views.open_oracle_connection') as connect:
            response = list_objects(RequestFactory().get('/api/objects', {"names": '{}'}))
        self.assertEqual(response.status_code, 400)
        connect.assert_not_called()
