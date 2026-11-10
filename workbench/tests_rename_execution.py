from unittest.mock import MagicMock, call
from django.test import SimpleTestCase

from .views import execute_one, sqlplus_feedback


class RenameExecutionTests(SimpleTestCase):
    def connection(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.description = []
        cursor.rowcount = 0
        return connection, cursor

    def test_repeated_rename_evicts_cached_handle_and_executes_prepared_statement(self):
        connection, cursor = self.connection()
        sql = 'RENAME EMPLOYEE TO EMPLOYEE1'
        for _ in range(3):
            execute_one(connection, sql, {}, True)
        self.assertEqual(cursor.prepare.call_args_list, [call(sql, cache_statement=False)] * 6)
        self.assertEqual(cursor.execute.call_args_list, [call(None, {})] * 3)

    def test_leading_comments_and_quoted_names_keep_rename_uncached(self):
        connection, cursor = self.connection()
        sql = '-- example\nrename "Employee" to "Employee1"'
        execute_one(connection, sql, {}, False)
        self.assertEqual(cursor.prepare.call_args_list, [call(sql, cache_statement=False)] * 2)
        cursor.execute.assert_called_once_with(None, {})

    def test_other_statements_keep_normal_execution_and_binds(self):
        for sql, binds in [('DROP TABLE EMPLOYEE1', {}), ('UPDATE EMPLOYEE SET SALARY=:amount', {'amount': 100})]:
            with self.subTest(sql=sql):
                connection, cursor = self.connection()
                execute_one(connection, sql, binds, False)
                cursor.prepare.assert_not_called()
                cursor.execute.assert_called_once_with(sql, {name.upper(): value for name, value in binds.items()})

    def test_old_cached_handle_is_closed_before_fresh_rename_execution(self):
        connection, cursor = self.connection()
        eviction = MagicMock()
        primary_context, eviction_context = MagicMock(), MagicMock()
        primary_context.__enter__.return_value = cursor
        eviction_context.__enter__.return_value = eviction
        connection.cursor.side_effect = [primary_context, eviction_context]
        events = []
        eviction.prepare.side_effect = lambda *args, **kwargs: events.append('evict')
        eviction_context.__exit__.side_effect = lambda *args: events.append('close old handle')
        cursor.prepare.side_effect = lambda *args, **kwargs: events.append('prepare fresh')
        cursor.execute.side_effect = lambda *args, **kwargs: events.append('execute')
        execute_one(connection, 'RENAME EMPLOYEE TO EMPLOYEE1', {}, True)
        self.assertEqual(events, ['evict', 'close old handle', 'prepare fresh', 'execute'])
        eviction.execute.assert_not_called()

    def test_rename_feedback_describes_the_actual_ddl_operation(self):
        self.assertEqual(sqlplus_feedback('RENAME EMPLOYEE TO EMPLOYEE1', 0), 'Object renamed.')
