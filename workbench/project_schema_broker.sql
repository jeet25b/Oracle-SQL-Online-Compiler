-- Install as an Oracle DBA in the configured sandbox PDB, never CDB$ROOT.
-- Replace __PROJECT_OWNER__ with the quoted trusted provisioning account.
WHENEVER SQLERROR EXIT FAILURE
DECLARE
  existing NUMBER;
BEGIN
  IF SYS_CONTEXT('USERENV','CON_NAME') = 'CDB$ROOT' THEN
    RAISE_APPLICATION_ERROR(-20001, 'Install only inside the sandbox PDB');
  END IF;
  SELECT COUNT(*) INTO existing FROM dba_users WHERE username='PCHK_BROKER';
  IF existing = 0 THEN
    EXECUTE IMMEDIATE 'CREATE USER PCHK_BROKER NO AUTHENTICATION ACCOUNT LOCK';
  END IF;
END;
/
ALTER USER PCHK_BROKER NO AUTHENTICATION ACCOUNT LOCK;
ALTER USER PCHK_BROKER QUOTA 10M ON USERS;
GRANT CREATE USER, DROP USER TO PCHK_BROKER;
-- Delegate only the two fixed practice roles, never GRANT ANY ROLE.
GRANT CONNECT, RESOURCE TO PCHK_BROKER WITH ADMIN OPTION;
BEGIN
  EXECUTE IMMEDIATE 'CREATE TABLE PCHK_BROKER.FILE_LEASES
    (run_id NUMBER PRIMARY KEY, schema_name VARCHAR2(30) UNIQUE NOT NULL,
     caller_name VARCHAR2(128) NOT NULL, removed NUMBER DEFAULT 0 NOT NULL)';
EXCEPTION WHEN OTHERS THEN
  IF SQLCODE != -955 THEN RAISE; END IF;
END;
/
CREATE OR REPLACE PACKAGE PCHK_BROKER.SCHEMAS AUTHID DEFINER AS
  PROCEDURE ENSURE_SCHEMA(p_run NUMBER, p_name VARCHAR2, p_password VARCHAR2);
  PROCEDURE REMOVE_SCHEMA(p_run NUMBER, p_name VARCHAR2);
END;
/
CREATE OR REPLACE PACKAGE BODY PCHK_BROKER.SCHEMAS AS
  PROCEDURE check_name(p_name VARCHAR2) IS
  BEGIN
    IF NOT REGEXP_LIKE(p_name, '^PCHK_[A-F0-9]{24}$', 'c') OR LENGTH(p_name) != 29 THEN
      RAISE_APPLICATION_ERROR(-20002, 'Invalid file schema name');
    END IF;
  END;
  PROCEDURE ENSURE_SCHEMA(p_run NUMBER, p_name VARCHAR2, p_password VARCHAR2) IS
    saved_name VARCHAR2(30);
    saved_caller VARCHAR2(128);
    was_removed NUMBER;
  BEGIN
    check_name(p_name);
    IF NOT REGEXP_LIKE(p_password, '^P[a-f0-9]{48}$', 'c') OR LENGTH(p_password) != 49 THEN
      RAISE_APPLICATION_ERROR(-20003, 'Invalid generated password');
    END IF;
    BEGIN
      SELECT schema_name, caller_name, removed INTO saved_name, saved_caller, was_removed
        FROM FILE_LEASES WHERE run_id=p_run;
      IF saved_name != p_name OR saved_caller != SYS_CONTEXT('USERENV','SESSION_USER') OR was_removed != 0 THEN
        RAISE_APPLICATION_ERROR(-20004, 'File schema lease does not match');
      END IF;
      RETURN;
    EXCEPTION WHEN NO_DATA_FOUND THEN NULL;
    END;
    INSERT INTO FILE_LEASES(run_id,schema_name,caller_name)
      VALUES(p_run,p_name,SYS_CONTEXT('USERENV','SESSION_USER'));
    COMMIT;
    BEGIN
      EXECUTE IMMEDIATE 'CREATE USER ' || p_name || ' IDENTIFIED BY "' || p_password ||
        '" DEFAULT TABLESPACE USERS QUOTA 100M ON USERS';
    EXCEPTION WHEN OTHERS THEN
      -- Never claim ownership of an existing user if CREATE USER failed.
      DELETE FROM FILE_LEASES WHERE run_id=p_run;
      COMMIT;
      RAISE;
    END;
    -- If this grant fails, retain the lease for cleanup of the user we created.
    EXECUTE IMMEDIATE 'GRANT CONNECT, RESOURCE TO ' || p_name;
  END;
  PROCEDURE REMOVE_SCHEMA(p_run NUMBER, p_name VARCHAR2) IS
    saved_name VARCHAR2(30);
    saved_caller VARCHAR2(128);
    was_removed NUMBER;
  BEGIN
    check_name(p_name);
    BEGIN
      SELECT schema_name, caller_name, removed INTO saved_name, saved_caller, was_removed
        FROM FILE_LEASES WHERE run_id=p_run;
    EXCEPTION WHEN NO_DATA_FOUND THEN RETURN;
    END;
    IF saved_name != p_name OR saved_caller != SYS_CONTEXT('USERENV','SESSION_USER') THEN
      RAISE_APPLICATION_ERROR(-20004, 'File schema lease does not match');
    END IF;
    IF was_removed = 1 THEN RETURN; END IF;
    BEGIN
      EXECUTE IMMEDIATE 'DROP USER ' || p_name || ' CASCADE';
    EXCEPTION WHEN OTHERS THEN
      IF SQLCODE != -1918 THEN RAISE; END IF;
    END;
    UPDATE FILE_LEASES SET removed=1 WHERE run_id=p_run;
    COMMIT;
  END;
END;
/
-- Package compilation warnings must fail deployment rather than enable a broken broker.
DECLARE
  failures NUMBER;
BEGIN
  SELECT COUNT(*) INTO failures FROM dba_errors WHERE owner='PCHK_BROKER' AND name='SCHEMAS';
  IF failures > 0 THEN RAISE_APPLICATION_ERROR(-20005,'Broker compilation failed'); END IF;
END;
/
GRANT EXECUTE ON PCHK_BROKER.SCHEMAS TO __PROJECT_OWNER__;
