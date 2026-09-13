from alembic import context
from matescope.models import Base

# Startup passes only the connection to MateScope's own SQLite database.
connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=Base.metadata)
with context.begin_transaction():
    context.run_migrations()
