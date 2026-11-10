"""Live Oracle integration checks; only broker-leased temporary schemas are modified."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "oraplay.settings")
import django
django.setup()
from decimal import Decimal
from types import SimpleNamespace
from workbench.models import ProjectExecution, ProjectRunnerConfiguration
from workbench.execution_grading import grade_question
config = ProjectRunnerConfiguration.objects.select_related("sandbox_connection__owner").get(pk=1)
owner = config.sandbox_connection.owner
setup = "CREATE TABLE marks (id NUMBER PRIMARY KEY, score NUMBER); INSERT INTO marks VALUES (1, 10); INSERT INTO marks VALUES (2, 20);"
cases = [
 ("SELECT", "", "SELECT 2 FROM dual;", "SELECT 1+1 FROM dual;", "", True),
 ("INSERT", setup, "INSERT INTO marks VALUES (3,30);", "INSERT INTO marks (score,id) VALUES (10+20,3);", "", True),
 ("UPDATE", setup, "UPDATE marks SET score=score+1;", "UPDATE marks SET score=1+score;", "", True),
 ("DELETE", setup, "DELETE FROM marks WHERE id=1;", "DELETE FROM marks WHERE score=10;", "", True),
 ("MERGE", setup, "MERGE INTO marks m USING (SELECT 1 id, 30 score FROM dual) n ON (m.id=n.id) WHEN MATCHED THEN UPDATE SET m.score=n.score;", "UPDATE marks SET score=30 WHERE id=1;", "", True),
 ("CREATE TABLE", "", "CREATE TABLE sample (id NUMBER CONSTRAINT pk_a PRIMARY KEY);", "CREATE TABLE sample (id NUMBER CONSTRAINT pk_b PRIMARY KEY);", "", True),
 ("ALTER TABLE", setup, "ALTER TABLE marks ADD note VARCHAR2(30);", "ALTER TABLE marks ADD (note VARCHAR2(30));", "", True),
 ("DROP TABLE", setup, "DROP TABLE marks;", "DROP TABLE marks;", "", True),
 ("TRUNCATE", setup, "TRUNCATE TABLE marks;", "DELETE FROM marks;", "", True),
 ("PLSQL output", "", "BEGIN DBMS_OUTPUT.PUT_LINE('2'); END;", "BEGIN DBMS_OUTPUT.PUT_LINE(1+1); END;", "", True),
 ("PROCEDURE", setup, "CREATE OR REPLACE PROCEDURE add_mark AS BEGIN UPDATE marks SET score=score+1; END;", "CREATE OR REPLACE PROCEDURE add_mark AS BEGIN UPDATE marks SET score=1+score; END;", "BEGIN add_mark; END;", True),
 ("FUNCTION", "", "CREATE OR REPLACE FUNCTION double_it(n NUMBER) RETURN NUMBER AS BEGIN RETURN n*2; END;", "CREATE OR REPLACE FUNCTION double_it(n NUMBER) RETURN NUMBER AS BEGIN RETURN n+n; END;", "SELECT double_it(4) FROM dual;", True),
 ("TRIGGER", setup, "CREATE OR REPLACE TRIGGER score_trigger BEFORE INSERT ON marks FOR EACH ROW BEGIN :NEW.score := :NEW.score + 1; END;", "CREATE OR REPLACE TRIGGER score_trigger BEFORE INSERT ON marks FOR EACH ROW BEGIN :NEW.score := 1 + :NEW.score; END;", "INSERT INTO marks VALUES(3,30); SELECT score FROM marks WHERE id=3;", True),
 ("wrong data", setup, "UPDATE marks SET score=30 WHERE id=1;", "UPDATE marks SET score=31 WHERE id=1;", "", False),
 ("wrong stored behavior", setup, "CREATE OR REPLACE PROCEDURE add_mark AS BEGIN UPDATE marks SET score=score+1; END;", "CREATE OR REPLACE PROCEDURE add_mark AS BEGIN NULL; END;", "BEGIN add_mark; END;", False),
]
cases += [
 ("SELECT order follows reference", setup, "SELECT id FROM marks;", "SELECT id FROM marks ORDER BY id DESC;", "", True),
 ("ROLLBACK", setup, "UPDATE marks SET score=99; ROLLBACK;", "BEGIN NULL; END;", "", True),
 ("SEQUENCE", "", "CREATE SEQUENCE seq START WITH 5 NOCACHE;", "CREATE SEQUENCE seq START WITH 5 NOCACHE;", "SELECT seq.NEXTVAL FROM dual;", True),
 ("IDENTITY", "", "CREATE TABLE ids (id NUMBER GENERATED ALWAYS AS IDENTITY, label VARCHAR2(20)); INSERT INTO ids(label) VALUES ('x');", "CREATE TABLE ids (id NUMBER GENERATED ALWAYS AS IDENTITY, label VARCHAR2(20)); INSERT INTO ids(label) VALUES ('x');", "", True),
 ("implicit cursor", "", "DECLARE c SYS_REFCURSOR; BEGIN OPEN c FOR SELECT 2 FROM dual; DBMS_SQL.RETURN_RESULT(c); END;", "DECLARE c SYS_REFCURSOR; BEGIN OPEN c FOR SELECT 1+1 FROM dual; DBMS_SQL.RETURN_RESULT(c); END;", "", True),
]
failures=[]
for name, fixture, reference, answer, verification, verdict in cases:
    question=SimpleNamespace(expected_sql=reference, verification_sql=verification, marks=Decimal("1"))
    try:
        result=grade_question(owner, question, answer, fixture)
        if result["is_correct"] != verdict:
            raise AssertionError(result)
        print("PASS:", name, flush=True)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        failures.append(name)
        print("FAIL:", name, type(exc).__name__, flush=True)
remaining=ProjectExecution.objects.filter(activity_kind="grading",schema_removed_at__isnull=True).exclude(schema_name="").count()
print("Remaining temporary grading schemas:", remaining, flush=True)
if failures or remaining:
    raise SystemExit("Integration failures: " + ", ".join(failures))
print("All 20 Oracle integration cases passed.")
