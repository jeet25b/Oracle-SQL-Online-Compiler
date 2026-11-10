import json
from unittest.mock import MagicMock, patch
from django.test import RequestFactory, SimpleTestCase
from .views import _execute_sql_request


class StatementTimingTests(SimpleTestCase):
    def test_script_returns_individual_durations_matching_history(self):
        request = RequestFactory().post('/api/execute', json.dumps({'sql': 'SELECT 1 FROM dual; SELECT 2 FROM dual;', 'sqlplus': True}), content_type='application/json')
        request.user = MagicMock(is_authenticated=True)
        with patch('workbench.views.same_origin', return_value=True), \
             patch('workbench.views.current_oracle_session', return_value={'sqlplus': MagicMock()}), \
             patch('workbench.views.execute_one', return_value=([], [], 0, [], [])), \
             patch('workbench.views.time.monotonic', side_effect=[0, 1, 1.12, 2, 2.34, 3]), \
             patch('workbench.views._record_compiler_query') as history:
            response = _execute_sql_request(request)
        self.assertEqual(response.status_code, 200, response.content)
        data = json.loads(response.content)
        self.assertEqual([item['durationMs'] for item in data['statementResults']], [120, 340])
        self.assertEqual([call.args[4] for call in history.call_args_list], [120, 340])
        self.assertEqual(data['durationMs'], 3000)
