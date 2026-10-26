from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase, RequestFactory
from .views import oracle_status, connect_saved_oracle, disconnect_oracle


class OracleConnectionTests(SimpleTestCase):
    def test_disconnect_preserves_portal_session_for_saved_reconnect(self):
        request = RequestFactory().post('/api/disconnect')
        request.session = MagicMock(session_key='portal-session')
        terminal, editor = MagicMock(), MagicMock()
        with patch('workbench.views.same_origin', return_value=True), \
             patch('workbench.views.current_oracle_session', return_value={'sqlplus': terminal, 'editor': editor}):
            response = disconnect_oracle(request)
        self.assertEqual(response.status_code, 200)
        terminal.close.assert_called_once()
        editor.close.assert_called_once()
        request.session.pop.assert_called_once_with('oracle_connected', None)
        request.session.cycle_key.assert_not_called()
        request.session.flush.assert_not_called()

    def test_transient_health_error_preserves_both_connections(self):
        terminal, editor = MagicMock(), MagicMock()
        terminal.cursor.return_value.__enter__.return_value.execute.side_effect = Exception("ORA-01013: operation cancelled")
        state = {"sqlplus": terminal, "editor": editor, "status": {"user":"TEST"}}
        with patch('workbench.views.current_oracle_session', return_value=state):
            response = oracle_status(RequestFactory().get('/api/status'))
        self.assertIn(b'"connected": true',response.content)
        terminal.close.assert_not_called()
        editor.close.assert_not_called()

    def test_broken_terminal_does_not_close_editor_transaction(self):
        terminal, editor = MagicMock(), MagicMock()
        terminal.cursor.return_value.__enter__.return_value.execute.side_effect = Exception("ORA-03114: not connected to ORACLE")
        state = {"sqlplus": terminal, "editor": editor}
        with patch('workbench.views.current_oracle_session', return_value=state):
            response = oracle_status(RequestFactory().get('/api/status'))
        self.assertIn(b'"connected": true',response.content)
        self.assertIsNone(state['sqlplus'])
        editor.close.assert_not_called()

    def test_busy_execution_skips_health_probe(self):
        terminal = MagicMock()
        lock = MagicMock()
        lock.acquire.return_value = False
        with patch('workbench.views.current_oracle_session', return_value={"sqlplus":terminal,"operation_lock":lock}):
            response = oracle_status(RequestFactory().get('/api/status'))
        terminal.cursor.assert_not_called()
        self.assertIn(b'"busy": true', response.content)

    def test_saved_connect_reuses_live_session(self):
        request = RequestFactory().post('/api/connect-saved')
        request.session = MagicMock(session_key='test-oracle-session')
        request.user = MagicMock(is_authenticated=True, oracle_username='TEST', oracle_connect_string='test-service')
        request.user.has_oracle_credentials.return_value = True
        state = {"settings":{"user":"TEST","connectString":"test-service"},"sqlplus":MagicMock(),"status":{"user":"TEST"}}
        with patch('workbench.views.same_origin',return_value=True), patch('workbench.views.current_oracle_session',return_value=state), patch('workbench.views._open_oracle_session') as opener:
            response = connect_saved_oracle(request)
        opener.assert_not_called()
        self.assertIn(b'"connected": true',response.content)
        self.assertIn(b'"saved": true',response.content)
