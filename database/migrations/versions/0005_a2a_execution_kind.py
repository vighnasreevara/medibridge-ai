"""a2a execution kind

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-20 21:10:00.000000

The supervisor records each agent-to-agent call as its own execution (kind 'a2a'); the remote agent's
execution is its child.
"""
from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

OLD = "kind IN ('agent', 'tool', 'llm', 'retrieval', 'guardrail', 'gate')"
NEW = "kind IN ('agent', 'tool', 'llm', 'retrieval', 'guardrail', 'gate', 'a2a')"


def upgrade() -> None:
    op.drop_constraint(op.f('ck_agent_executions_kind_vocab'), 'agent_executions', schema='workflow', type_='check')
    op.create_check_constraint(op.f('ck_agent_executions_kind_vocab'), 'agent_executions', NEW, schema='workflow')


def downgrade() -> None:
    # The agents' own executions name the a2a call as their parent: detach them before removing the calls.
    op.execute("UPDATE workflow.agent_executions SET parent_id = NULL WHERE parent_id IN "
               "(SELECT id FROM workflow.agent_executions WHERE kind = 'a2a')")
    op.execute("DELETE FROM workflow.agent_executions WHERE kind = 'a2a'")
    op.drop_constraint(op.f('ck_agent_executions_kind_vocab'), 'agent_executions', schema='workflow', type_='check')
    op.create_check_constraint(op.f('ck_agent_executions_kind_vocab'), 'agent_executions', OLD, schema='workflow')
