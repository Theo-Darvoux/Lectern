"""add_performance_indexes

Revision ID: e27745a4e006
Revises: add_completed_tutorials
Create Date: 2026-06-09 12:08:45.745257

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e27745a4e006'
down_revision: Union[str, None] = 'add_completed_tutorials'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_materials_directory_parent', 'materials', ['directory_id', 'parent_material_id'])
    op.create_index('ix_materials_parent_material_id', 'materials', ['parent_material_id'])
    op.create_index('ix_materials_views_today', 'materials', [sa.text('views_today DESC')])
    op.create_index('ix_materials_views_14d', 'materials', [sa.text('views_14d DESC')])
    op.create_index('ix_materials_author_id', 'materials', ['author_id'])
    
    op.create_index('ix_directories_parent_id', 'directories', ['parent_id'])
    
    op.create_index('ix_view_history_material_id', 'view_history', ['material_id'])


def downgrade() -> None:
    op.drop_index('ix_view_history_material_id', table_name='view_history')
    op.drop_index('ix_directories_parent_id', table_name='directories')
    op.drop_index('ix_materials_author_id', table_name='materials')
    op.drop_index('ix_materials_views_14d', table_name='materials')
    op.drop_index('ix_materials_views_today', table_name='materials')
    op.drop_index('ix_materials_parent_material_id', table_name='materials')
    op.drop_index('ix_materials_directory_parent', table_name='materials')
