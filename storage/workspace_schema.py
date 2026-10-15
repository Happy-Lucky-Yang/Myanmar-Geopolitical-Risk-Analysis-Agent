"""个人工作区独立结构；通过新增迁移创建，禁止启动时自动建表。"""
from sqlalchemy import (Table, Column as C, String, Text, Integer, Boolean, Date, DateTime,
                        ForeignKey as FK, ForeignKeyConstraint, UniqueConstraint, CheckConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB

TABLES = ('conversations', 'messages', 'message_feedback', 'user_preferences', 'feature_usage_daily', 'agent_requests')


def attach(metadata):
    if 'conversations' in metadata.tables:
        return
    def stamp():
        return C('created_at', DateTime(timezone=True), nullable=False, server_default=text('now()'))
    def owner():
        return C('owner_id', String(64), FK('users.id'), nullable=False)
    def payload():
        return C('payload', JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    Table('conversations', metadata, C('id', String(64), primary_key=True), owner(),
          C('title', String(160), nullable=False), stamp(),
          UniqueConstraint('id', 'owner_id', name='conversation_owner'))
    Table('messages', metadata, C('id', String(64), primary_key=True), owner(),
          C('conversation_id', String(64), nullable=False), C('role', String(16), nullable=False),
          C('content', Text, nullable=False), payload(), stamp(),
          ForeignKeyConstraint(['conversation_id', 'owner_id'], ['conversations.id', 'conversations.owner_id'], ondelete='CASCADE'),
          UniqueConstraint('id', 'owner_id', name='message_owner'),
          CheckConstraint("role IN ('user','assistant')", name='message_role'))
    Table('message_feedback', metadata, C('message_id', String(64), primary_key=True), owner(),
          C('rating', Integer, nullable=False), C('tag', String(32), nullable=False),
          C('comment', String(1000), nullable=False), stamp(),
          ForeignKeyConstraint(['message_id','owner_id'], ['messages.id','messages.owner_id'], ondelete='CASCADE'),
          CheckConstraint('rating BETWEEN 1 AND 5', name='feedback_rating'))
    Table('user_preferences', metadata, C('owner_id', String(64), FK('users.id'), primary_key=True), payload())
    Table('feature_usage_daily', metadata, C('owner_id', String(64), FK('users.id'), primary_key=True),
          C('date', Date, primary_key=True), C('feature', String(32), primary_key=True),
          C('count', Integer, nullable=False, server_default='0'))
    Table('agent_requests', metadata, C('id', String(64), primary_key=True),
          C('owner_id', String(64), FK('users.id')), C('conversation_id', String(64)),
          ForeignKeyConstraint(['conversation_id','owner_id'], ['conversations.id','conversations.owner_id'], ondelete='CASCADE'),
          C('request_key', String(64), nullable=False, unique=True),
          C('kind', String(16), nullable=False), C('status', String(16), nullable=False),
          C('region', String(64), nullable=False), payload(),
          C('result', JSONB, nullable=False, server_default=text("'{}'::jsonb")), stamp(),
          CheckConstraint("kind IN ('chat','briefing','auto')", name='agent_kind'),
          CheckConstraint("status IN ('queued','running','done','failed')", name='agent_status'),
          CheckConstraint("(kind='auto' AND owner_id IS NULL AND conversation_id IS NULL) OR (kind!='auto' AND owner_id IS NOT NULL)", name='agent_ownership'))
