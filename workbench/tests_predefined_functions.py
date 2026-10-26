import json
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase

from .sql_lexer import prepare_statement, tokens
from .views import execute_sql, split_script


class PredefinedFunctionTests(SimpleTestCase):
    def test_document_function_examples_reach_oracle_in_both_editors(self):
        queries = ["SELECT concat('Oracle', ' SQL') AS CONCAT_RESULT FROM dual;", "SELECT lower('ORACLE') AS LOWER_RESULT FROM dual;", "SELECT upper('oracle') AS UPPER_RESULT FROM dual;", "SELECT initcap('oracle sql') AS INITCAP_RESULT FROM dual;", "SELECT lpad('ram',10,'*') AS LPAD_RESULT FROM dual;", "SELECT rpad('ram',10,'*') AS RPAD_RESULT FROM dual;", "SELECT ltrim('***ram','*') AS LTRIM_RESULT FROM dual;", "SELECT rtrim('ram***','*') AS RTRIM_RESULT FROM dual;", "SELECT substr('ORACLE',-3,2) AS SUBSTR_RESULT FROM dual;", "SELECT replace('sun','un','xy') AS REPLACE_RESULT FROM dual;", "SELECT translate('sun','un','xy') AS TRANSLATE_RESULT FROM dual;", "SELECT instr('sun sun','un',1,2) AS INSTR_RESULT FROM dual;", "SELECT length('Oracle') AS LENGTH_RESULT FROM dual;", "SELECT ascii('z') AS ASCII_RESULT FROM dual;", 'SELECT chr(65) AS CHR_RESULT FROM dual;', "SELECT soundex('scott') AS SOUNDEX_RESULT FROM dual;", 'SELECT round(1234.567,2) AS ROUND_RESULT FROM dual;', 'SELECT trunc(1234.567,2) AS TRUNC_RESULT FROM dual;', 'SELECT ceil(1.2) AS CEIL_RESULT FROM dual;', 'SELECT floor(1.2) AS FLOOR_RESULT FROM dual;', 'SELECT sign(-15) AS SIGN_RESULT FROM dual;', 'SELECT mod(5.7,2.5) AS MOD_RESULT FROM dual;', 'SELECT sqrt(81) AS SQRT_RESULT FROM dual;', 'SELECT power(10,3) AS POWER_RESULT FROM dual;', 'SELECT sin(1) AS SIN_RESULT FROM dual;', 'SELECT sinh(1) AS SINH_RESULT FROM dual;', 'SELECT cos(1) AS COS_RESULT FROM dual;', 'SELECT cosh(1) AS COSH_RESULT FROM dual;', 'SELECT tan(1) AS TAN_RESULT FROM dual;', 'SELECT tanh(1) AS TANH_RESULT FROM dual;', 'SELECT ln(exp(1)) AS LN_RESULT FROM dual;', 'SELECT log(10,100) AS LOG_RESULT FROM dual;', 'SELECT exp(1) AS EXP_RESULT FROM dual;', 'SELECT abs(-15) AS ABS_RESULT FROM dual;', 'SELECT sysdate AS SYSDATE_RESULT FROM dual;', "SELECT trunc((DATE '2026-10-25' + 1) - DATE '2026-10-25') AS DATE_ARITHMETIC_RESULT FROM dual;", "SELECT months_between(DATE '2026-10-25',DATE '2025-10-25') AS MONTHS_BETWEEN_RESULT FROM dual;", "SELECT add_months(DATE '2026-10-25',2) AS ADD_MONTHS_RESULT FROM dual;", "SELECT last_day(DATE '2026-10-25') AS LAST_DAY_RESULT FROM dual;", "SELECT next_day(DATE '2026-10-25',to_char(DATE '2026-10-25','DAY')) AS NEXT_DAY_RESULT FROM dual;", "SELECT to_char(DATE '2026-10-25','DD-MM-YYYY') AS TO_CHAR_RESULT FROM dual;", "SELECT to_char(sysdate,'HH24:MI:SS') AS TO_CHAR_TIME_RESULT FROM dual;", "SELECT to_char(DATE '2026-10-25','DDSPTH') AS TO_CHAR_SPELLED_RESULT FROM dual;", "SELECT to_date('11/11/2004 10:30 PM','DD/MM/YYYY HH:MI PM') AS TO_DATE_RESULT FROM dual;", "SELECT to_number('12345') AS TO_NUMBER_RESULT FROM dual;", 'SELECT nvl(cast(null as number),0) AS NVL_RESULT FROM dual;', "SELECT decode(20,10,'Training',20,'Export',30,'Sales','Other') AS DECODE_RESULT FROM dual;", 'SELECT user AS USER_RESULT FROM dual;', 'SELECT uid AS UID_RESULT FROM dual;', "SELECT userenv('LANGUAGE') AS USERENV_RESULT FROM dual;", 'SELECT vsize(3.141) AS VSIZE_RESULT FROM dual;', 'SELECT SUM(n) AS SUM_RESULT FROM (SELECT 10 n FROM dual UNION ALL SELECT 20 FROM dual UNION ALL SELECT 30 FROM dual);', 'SELECT MIN(n) AS MIN_RESULT FROM (SELECT 10 n FROM dual UNION ALL SELECT 20 FROM dual UNION ALL SELECT 30 FROM dual);', 'SELECT MAX(n) AS MAX_RESULT FROM (SELECT 10 n FROM dual UNION ALL SELECT 20 FROM dual UNION ALL SELECT 30 FROM dual);', 'SELECT COUNT(n) AS COUNT_RESULT FROM (SELECT 10 n FROM dual UNION ALL SELECT 20 FROM dual UNION ALL SELECT 30 FROM dual);', 'SELECT AVG(n) AS AVG_RESULT FROM (SELECT 10 n FROM dual UNION ALL SELECT 20 FROM dual UNION ALL SELECT 30 FROM dual);', 'SELECT STDDEV(n) AS STDDEV_RESULT FROM (SELECT 10 n FROM dual UNION ALL SELECT 20 FROM dual UNION ALL SELECT 30 FROM dual);', 'SELECT VARIANCE(n) AS VARIANCE_RESULT FROM (SELECT 10 n FROM dual UNION ALL SELECT 20 FROM dual UNION ALL SELECT 30 FROM dual);']
        source = '-- Leading comments must not block function queries.\n' + '\n'.join(queries)
        for sqlplus in (False, True):
            statements = split_script(source, sqlplus=sqlplus)
            self.assertEqual(len(statements), 58)
            for sql in statements:
                with self.subTest(sqlplus=sqlplus, sql=sql):
                    connection = MagicMock()
                    request = RequestFactory().post('/api/execute', data=json.dumps({
                        'sql': sql, 'sqlplus': sqlplus, 'executionMode': 'statement',
                    }), content_type='application/json')
                    with patch('workbench.views.same_origin', return_value=True), \
                         patch('workbench.views.current_oracle_session', return_value={'sqlplus': connection, 'editor': connection}), \
                         patch('workbench.views.execute_one', return_value=([{'name': 'RESULT', 'type': 'NUMBER'}], [[1]], None, [], [])) as execute, \
                         patch('workbench.views._record_compiler_query'):
                        response = execute_sql(request)
                    self.assertEqual(response.status_code, 200, response.content)
                    execute.assert_called_once()
                    self.assertIs(execute.call_args.args[0], connection)
                    prepared = prepare_statement(sql)
                    first = next(token for token in tokens(prepared) if token['kind'] != 'comment')
                    self.assertEqual(execute.call_args.args[1], prepared[first['start']:])
                    self.assertEqual(json.loads(response.content)['rows'], [[1]])
