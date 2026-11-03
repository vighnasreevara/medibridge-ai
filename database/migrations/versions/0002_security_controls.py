"""Database-level guardrails and least-privilege grants.

- Audit log: hash chain assigned in a BEFORE INSERT trigger under an advisory lock; UPDATE/DELETE/TRUNCATE rejected.
- Append-only tables: handoff_versions, approval_records, clinical_note_versions.
- document_versions: content columns immutable once written.
- Finalized clinical notes / encounters cannot be silently reopened.
- Prescriptions and clinical approvals require an active user with the doctor role (guardrails 6, 7).
- The application role gets only the privileges it needs.

The chain relies on READ COMMITTED: the trigger's SELECT takes a fresh snapshot after the lock is held.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

from medibridge_api.config import get_settings

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

AUDIT_LOCK_KEY = 724591  # arbitrary constant, used only for the audit chain advisory lock

APPEND_ONLY = ("workflow.handoff_versions", "workflow.approval_records", "clinical.clinical_note_versions")


def upgrade() -> None:
    app = get_settings().postgres_app_user

    # ---------------------------------------------------------------- audit chain
    op.execute("CREATE SEQUENCE audit.audit_seq AS bigint")
    op.execute(r"""
CREATE FUNCTION audit.compute_row_hash(r audit.audit_logs) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT encode(sha256(convert_to(concat_ws('|',
    r.seq::text, r.id::text,
    to_char(r.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    coalesce(r.actor_user_id::text, ''), coalesce(r.actor_role, ''), r.action, r.category, r.outcome,
    coalesce(r.patient_id::text, ''), coalesce(r.encounter_id::text, ''),
    coalesce(r.resource_type, ''), coalesce(r.resource_id, ''), coalesce(r.request_id, ''), coalesce(r.ip, ''),
    coalesce(r.details, '{}'::jsonb)::text, r.prev_hash), 'UTF8')), 'hex')
$$""")
    op.execute(f"""
CREATE FUNCTION audit.chain_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, audit AS $$
DECLARE last_hash text;
BEGIN
  PERFORM pg_advisory_xact_lock({AUDIT_LOCK_KEY});
  NEW.seq := nextval('audit.audit_seq');
  NEW.occurred_at := coalesce(NEW.occurred_at, now());
  NEW.details := coalesce(NEW.details, '{{}}'::jsonb);
  SELECT row_hash INTO last_hash FROM audit.audit_logs ORDER BY seq DESC LIMIT 1;
  NEW.prev_hash := coalesce(last_hash, repeat('0', 64));
  NEW.row_hash := audit.compute_row_hash(NEW);
  RETURN NEW;
END $$""")
    op.execute("CREATE TRIGGER audit_logs_chain BEFORE INSERT ON audit.audit_logs "
               "FOR EACH ROW EXECUTE FUNCTION audit.chain_insert()")

    op.execute("""
CREATE FUNCTION audit.reject_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% on %.% is not permitted (append-only)', TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
    USING ERRCODE = 'insufficient_privilege';
END $$""")
    for table in ("audit.audit_logs", *APPEND_ONLY):
        name = table.split(".")[1]
        op.execute(f"CREATE TRIGGER {name}_append_only BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION audit.reject_change()")
        op.execute(f"CREATE TRIGGER {name}_no_truncate BEFORE TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_change()")

    # Verification: returns the first broken link (empty result = chain intact).
    op.execute("""
CREATE FUNCTION audit.verify_chain() RETURNS TABLE(seq bigint, problem text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, audit AS $$
  WITH ordered AS (
    SELECT l, lag(l.row_hash) OVER (ORDER BY l.seq) AS expected_prev FROM audit.audit_logs l
  )
  SELECT (o.l).seq,
         CASE WHEN (o.l).row_hash <> audit.compute_row_hash(o.l) THEN 'row_hash_mismatch'
              ELSE 'prev_hash_mismatch' END
  FROM ordered o
  WHERE (o.l).row_hash <> audit.compute_row_hash(o.l)
     OR (o.l).prev_hash <> coalesce(o.expected_prev, repeat('0', 64))
  ORDER BY (o.l).seq
$$""")

    # ---------------------------------------------------------------- document versions
    op.execute("""
CREATE FUNCTION docs.protect_version_content() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.document_id <> OLD.document_id OR NEW.version <> OLD.version
     OR NEW.storage_key <> OLD.storage_key OR NEW.content_sha256 <> OLD.content_sha256
     OR NEW.mime_type <> OLD.mime_type OR NEW.size_bytes <> OLD.size_bytes
     OR (OLD.extracted_text IS NOT NULL AND NEW.extracted_text IS DISTINCT FROM OLD.extracted_text) THEN
    RAISE EXCEPTION 'document version content is immutable; create a new version'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$""")
    op.execute("CREATE TRIGGER document_versions_immutable BEFORE UPDATE ON docs.document_versions "
               "FOR EACH ROW EXECUTE FUNCTION docs.protect_version_content()")
    op.execute("CREATE TRIGGER document_versions_no_delete BEFORE DELETE ON docs.document_versions "
               "FOR EACH ROW EXECUTE FUNCTION audit.reject_change()")

    # ---------------------------------------------------------------- finalized records
    op.execute("""
CREATE FUNCTION clinical.protect_finalized_note() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status IN ('final', 'amended') THEN
    IF NEW.status NOT IN ('amended', 'entered_in_error') AND NOT (NEW.status = OLD.status AND NEW.current_version = OLD.current_version) THEN
      RAISE EXCEPTION 'finalized note % cannot move to status %', OLD.id, NEW.status USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.current_version < OLD.current_version OR NEW.author_user_id <> OLD.author_user_id
       OR NEW.patient_id <> OLD.patient_id OR NEW.encounter_id <> OLD.encounter_id THEN
      RAISE EXCEPTION 'finalized note % is protected', OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
  END IF;
  IF OLD.status = 'entered_in_error' AND NEW IS DISTINCT FROM OLD THEN
    RAISE EXCEPTION 'note % was entered in error and is closed', OLD.id USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$""")
    op.execute("CREATE TRIGGER clinical_notes_protect BEFORE UPDATE ON clinical.clinical_notes "
               "FOR EACH ROW EXECUTE FUNCTION clinical.protect_finalized_note()")

    op.execute("""
CREATE FUNCTION clinical.protect_finalized_encounter() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.patient_id <> NEW.patient_id THEN
    RAISE EXCEPTION 'encounter % cannot be moved to another patient', OLD.id USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.status = 'finalized' AND NEW.status NOT IN ('finalized', 'entered_in_error') THEN
    RAISE EXCEPTION 'finalized encounter % cannot be reopened', OLD.id USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$""")
    op.execute("CREATE TRIGGER encounters_protect BEFORE UPDATE ON clinical.encounters "
               "FOR EACH ROW EXECUTE FUNCTION clinical.protect_finalized_encounter()")

    # ---------------------------------------------------------------- doctor-only actions
    op.execute("""
CREATE FUNCTION auth.is_active_doctor(uid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, auth AS $$
  SELECT EXISTS (SELECT 1 FROM auth.users u JOIN auth.roles r ON r.id = u.role_id
                 WHERE u.id = uid AND u.status = 'active' AND r.key = 'doctor')
$$""")
    op.execute("""
CREATE FUNCTION clinical.require_doctor_prescriber() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT auth.is_active_doctor(NEW.prescriber_user_id) THEN
    RAISE EXCEPTION 'prescriptions can only be authored by an active doctor' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.approval_record_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM workflow.approval_records a
       WHERE a.id = NEW.approval_record_id AND a.decision = 'approved' AND a.patient_id = NEW.patient_id
         AND a.encounter_id = NEW.encounter_id AND auth.is_active_doctor(a.decided_by)) THEN
    RAISE EXCEPTION 'prescription approval record is missing, not approved, or for another patient/encounter'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$""")
    op.execute("CREATE TRIGGER prescriptions_doctor_only BEFORE INSERT OR UPDATE ON clinical.prescriptions "
               "FOR EACH ROW EXECUTE FUNCTION clinical.require_doctor_prescriber()")

    op.execute("""
CREATE FUNCTION workflow.require_doctor_approver() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT auth.is_active_doctor(NEW.decided_by) OR NEW.decided_role <> 'doctor' THEN
    RAISE EXCEPTION 'clinical approvals require an active doctor' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$""")
    op.execute("CREATE TRIGGER approval_records_doctor_only BEFORE INSERT ON workflow.approval_records "
               "FOR EACH ROW EXECUTE FUNCTION workflow.require_doctor_approver()")

    # ---------------------------------------------------------------- grants
    op.execute(f'GRANT USAGE ON SCHEMA auth, clinical, docs, workflow, audit TO "{app}"')
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA auth, clinical, docs, workflow TO "{app}"')
    op.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA workflow TO "{app}"')
    # Session cleanup and rate-limit pruning are the only hard deletes the app may perform.
    op.execute(f'GRANT DELETE ON auth.sessions, auth.auth_attempts TO "{app}"')
    op.execute(f'REVOKE UPDATE ON {", ".join(APPEND_ONLY)} FROM "{app}"')
    # Roles/permissions are reference data managed by migrations and the seed (owner role).
    op.execute(f'REVOKE INSERT, UPDATE ON auth.roles, auth.permissions, auth.role_permissions FROM "{app}"')
    op.execute(f'GRANT SELECT, INSERT ON audit.audit_logs TO "{app}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION audit.verify_chain() TO "{app}"')


def downgrade() -> None:
    app = get_settings().postgres_app_user
    op.execute(f'REVOKE ALL ON ALL TABLES IN SCHEMA auth, clinical, docs, workflow, audit FROM "{app}"')
    op.execute("DROP TRIGGER IF EXISTS approval_records_doctor_only ON workflow.approval_records")
    op.execute("DROP FUNCTION IF EXISTS workflow.require_doctor_approver()")
    op.execute("DROP TRIGGER IF EXISTS prescriptions_doctor_only ON clinical.prescriptions")
    op.execute("DROP FUNCTION IF EXISTS clinical.require_doctor_prescriber()")
    op.execute("DROP FUNCTION IF EXISTS auth.is_active_doctor(uuid)")
    op.execute("DROP TRIGGER IF EXISTS encounters_protect ON clinical.encounters")
    op.execute("DROP FUNCTION IF EXISTS clinical.protect_finalized_encounter()")
    op.execute("DROP TRIGGER IF EXISTS clinical_notes_protect ON clinical.clinical_notes")
    op.execute("DROP FUNCTION IF EXISTS clinical.protect_finalized_note()")
    op.execute("DROP TRIGGER IF EXISTS document_versions_no_delete ON docs.document_versions")
    op.execute("DROP TRIGGER IF EXISTS document_versions_immutable ON docs.document_versions")
    op.execute("DROP FUNCTION IF EXISTS docs.protect_version_content()")
    for table in ("audit.audit_logs", *APPEND_ONLY):
        name = table.split(".")[1]
        op.execute(f"DROP TRIGGER IF EXISTS {name}_append_only ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {name}_no_truncate ON {table}")
    op.execute("DROP FUNCTION IF EXISTS audit.verify_chain()")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_chain ON audit.audit_logs")
    op.execute("DROP FUNCTION IF EXISTS audit.chain_insert()")
    op.execute("DROP FUNCTION IF EXISTS audit.reject_change()")
    op.execute("DROP FUNCTION IF EXISTS audit.compute_row_hash(audit.audit_logs)")
    op.execute("DROP SEQUENCE IF EXISTS audit.audit_seq")
