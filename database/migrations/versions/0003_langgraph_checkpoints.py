"""LangGraph checkpoint tables in their own schema ``lg``.

The checkpointer persists workflow state (including gate payloads), so it lives in the protected database.
Tables are created by the owner role; the app role only gets DML on them.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

from medibridge_api.config import get_settings

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def checkpoint_conn_string(role: str = "app") -> str:
    return get_settings().checkpoint_conn_string(role)  # type: ignore[arg-type]


def upgrade() -> None:
    from langgraph.checkpoint.postgres import PostgresSaver

    import psycopg

    app = get_settings().postgres_app_user
    # setup() runs on its own connection, so the schema must already be committed. Create it on a separate
    # autocommit connection instead of committing Alembic's transaction (which would lose the version stamp).
    with psycopg.connect(checkpoint_conn_string("owner"), autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS lg")
    with PostgresSaver.from_conn_string(checkpoint_conn_string("owner")) as saver:
        saver.setup()  # idempotent
    op.execute(f'GRANT USAGE ON SCHEMA lg TO "{app}"')
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA lg TO "{app}"')


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS lg CASCADE")
