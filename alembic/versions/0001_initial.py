"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2025-01-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("keitaro_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.Column("geo", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("offer_id", sa.Integer(), nullable=True),
        sa.Column("domain_id", sa.Integer(), nullable=True),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("traffic_source_id", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("campaign_url", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_campaigns_keitaro_id", "campaigns", ["keitaro_id"], unique=True)
    op.create_index("ix_campaigns_alias", "campaigns", ["alias"], unique=True)

    op.create_table(
        "campaign_flows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("keitaro_stream_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("schema", sa.String(length=32), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("target_url", sa.String(length=1024), nullable=True),
        sa.Column("filters", sa.JSON(), nullable=True),
        sa.Column("collect_clicks", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("keitaro_stream_id", name="uq_flow_stream_id"),
    )
    op.create_index("ix_campaign_flows_campaign_id", "campaign_flows", ["campaign_id"])
    op.create_index("ix_campaign_flows_keitaro_stream_id", "campaign_flows", ["keitaro_stream_id"])

    op.create_table(
        "flow_offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("flow_id", sa.Integer(), sa.ForeignKey("campaign_flows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        sa.Column("share", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("name_snapshot", sa.String(length=512), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("flow_id", "offer_id", name="uq_flow_offer"),
    )
    op.create_index("ix_flow_offers_flow_id", "flow_offers", ["flow_id"])
    op.create_index("ix_flow_offers_offer_id", "flow_offers", ["offer_id"])

    op.create_table(
        "operation_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("method", sa.String(length=8), nullable=False, server_default="POST"),
        sa.Column("path", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
        sa.Column("request_body", sa.JSON(), nullable=True),
        sa.Column("response_body", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_operation_log_created_at", "operation_log", ["created_at"])

    op.create_table(
        "dictionary_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("kind", "external_id", name="uq_dict_kind_external"),
    )
    op.create_index("ix_dictionary_items_kind", "dictionary_items", ["kind"])
    op.create_index("ix_dictionary_items_synced_at", "dictionary_items", ["synced_at"])


def downgrade() -> None:
    op.drop_table("dictionary_items")
    op.drop_table("operation_log")
    op.drop_table("flow_offers")
    op.drop_table("campaign_flows")
    op.drop_table("campaigns")
