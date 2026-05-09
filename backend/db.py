import traceback
from os import getenv
from datetime import datetime
from threading import Lock
from pgvector.peewee import VectorField
from peewee import (
   PostgresqlDatabase,
   Model,
   TextField,
   ForeignKeyField,
   DateTimeField,
   IntegerField,
   FloatField,
   BooleanField,
)
from playhouse.db_url import connect
from dotenv import load_dotenv
load_dotenv()  # add this before anything else

_database_initialized = False
_database_lock = Lock()
_vector_initialized = False

database_url = getenv("DATABASE_URL")

if database_url and "://" in database_url:
   db = connect(database_url)
else:
   postgres_db_name = getenv("POSTGRES_DB_NAME") or getenv("PGDATABASE")
   postgres_host = getenv("POSTGRES_DB_HOST") or getenv("PGHOST")
   postgres_port = getenv("POSTGRES_DB_PORT") or getenv("PGPORT")
   postgres_user = getenv("POSTGRES_DB_USER") or getenv("PGUSER")
   postgres_password = getenv("POSTGRES_DB_PASSWORD") or getenv("PGPASSWORD")

   missing_postgres_vars = [
      name
      for name, value in {
         "POSTGRES_DB_NAME or PGDATABASE": postgres_db_name,
         "POSTGRES_DB_HOST or PGHOST": postgres_host,
         "POSTGRES_DB_PORT or PGPORT": postgres_port,
         "POSTGRES_DB_USER or PGUSER": postgres_user,
         "POSTGRES_DB_PASSWORD or PGPASSWORD": postgres_password,
      }.items()
      if not value
   ]
   if missing_postgres_vars:
      raise RuntimeError(
         "Database configuration is missing. Set a valid DATABASE_URL or these variables: "
         + ", ".join(missing_postgres_vars)
      )
   db = PostgresqlDatabase(
       postgres_db_name,
       host=postgres_host,
       port=postgres_port,
       user=postgres_user,
       password=postgres_password,
   )

class Users(Model):
   email = TextField(unique=True)
   password_hash = TextField()
   is_admin = BooleanField(default=False)
   created_at = DateTimeField(default=datetime.utcnow)
   class Meta:
      database = db
      db_table = 'users'

class Documents(Model):
   name = TextField()
   user_id = ForeignKeyField(Users, backref="documents", on_delete='CASCADE', null=True)
   class Meta:
      database = db
      db_table = 'documents'
      
class Tags(Model):
   name = TextField()
   user_id = ForeignKeyField(Users, backref="tags", on_delete='CASCADE', null=True)
   class Meta:
      database = db
      db_table = 'tags'
      
class DocumentTags(Model):
   document_id = ForeignKeyField(Documents, backref="document_tags", on_delete='CASCADE')
   tag_id = ForeignKeyField(Tags, backref="document_tags", on_delete='CASCADE')
   class Meta:
      database = db
      db_table = 'document_tags'
      
class DocumentInformationChunks(Model):
   document_id = ForeignKeyField(Documents, backref="document_information_chunks", on_delete='CASCADE')
   chunk = TextField()
   embedding = VectorField(dimensions=384)  # ← was 1536
   class Meta:
      database = db
      db_table = 'document_information_chunks'

class DocumentProcessingJobs(Model):
   user_id = ForeignKeyField(Users, backref="document_processing_jobs", on_delete='CASCADE')
   document_name = TextField()
   status = TextField(default="queued")
   progress = IntegerField(default=0)
   message = TextField(default="Queued")
   error = TextField(null=True)
   created_at = DateTimeField(default=datetime.utcnow)
   updated_at = DateTimeField(default=datetime.utcnow)
   class Meta:
      database = db
      db_table = 'document_processing_jobs'

class ChatMessages(Model):
   user_id = ForeignKeyField(Users, backref="chat_messages", on_delete='CASCADE')
   document_id = ForeignKeyField(Documents, backref="chat_messages", on_delete='SET NULL', null=True)
   selected_document_name = TextField()
   role = TextField()
   content = TextField()
   references = TextField(null=True)
   groundedness_label = TextField(null=True)
   groundedness_score = FloatField(null=True)
   groundedness_reason = TextField(null=True)
   created_at = DateTimeField(default=datetime.utcnow)
   class Meta:
      database = db
      db_table = 'chat_messages'

class QuestionUsage(Model):
   user_id = ForeignKeyField(Users, backref="question_usage", on_delete='CASCADE')
   usage_date = TextField()
   count = IntegerField(default=0)
   class Meta:
      database = db
      db_table = 'question_usage'

class ApiUsage(Model):
   user_id = ForeignKeyField(Users, backref="api_usage", on_delete='CASCADE')
   operation = TextField()
   model = TextField()
   prompt_tokens = IntegerField(default=0)
   completion_tokens = IntegerField(default=0)
   total_tokens = IntegerField(default=0)
   estimated_cost_usd = FloatField(default=0)
   latency_ms = IntegerField(null=True)
   document_processing_job_id = ForeignKeyField(
      DocumentProcessingJobs,
      backref="api_usage",
      on_delete='SET NULL',
      null=True,
   )
   created_at = DateTimeField(default=datetime.utcnow)
   class Meta:
      database = db
      db_table = 'api_usage'

def _table_exists(table_name: str) -> bool:
   result = db.execute_sql(
      """
      SELECT 1
      FROM information_schema.tables
      WHERE table_name = %s
      """,
      (table_name,),
   ).fetchone()
   return result is not None

def _column_exists(table_name: str, column_name: str) -> bool:
   result = db.execute_sql(
      """
      SELECT 1
      FROM information_schema.columns
      WHERE table_name = %s AND column_name = %s
      """,
      (table_name, column_name),
   ).fetchone()
   return result is not None

def _ensure_column(table_name: str, column_name: str, definition: str):
   if not _column_exists(table_name, column_name):
      db.execute_sql(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {definition}')

def _ensure_first_admin():
   admin_exists = Users.select().where(Users.is_admin == True).exists()
   if not admin_exists:
      first_user = Users.select().order_by(Users.id.asc()).first()
      if first_user:
         first_user.is_admin = True
         first_user.save()

def _assign_orphan_rows_to_admin():
   admin_user = Users.select().where(Users.is_admin == True).order_by(Users.id.asc()).first()
   if not admin_user:
      return
   Documents.update(user_id=admin_user.id).where(Documents.user_id.is_null(True)).execute()
   Tags.update(user_id=admin_user.id).where(Tags.user_id.is_null(True)).execute()

def initialize_database(require_vector: bool = False):
   global _database_initialized, _vector_initialized
   if _database_initialized:
      if require_vector and not _vector_initialized:
         _initialize_vector_store(require_vector=True)
      return

   with _database_lock:
      if _database_initialized:
         if require_vector and not _vector_initialized:
            _initialize_vector_store(require_vector=True)
         return

      db.connect(reuse_if_open=True)
      db.create_tables([
         Users,
         Documents,
         Tags,
         DocumentTags,
         DocumentProcessingJobs,
         ChatMessages,
         QuestionUsage,
         ApiUsage,
      ])

      if _table_exists("documents"):
         _ensure_column("documents", "user_id", "INTEGER REFERENCES users(id) ON DELETE CASCADE")
      if _table_exists("tags"):
         _ensure_column("tags", "user_id", "INTEGER REFERENCES users(id) ON DELETE CASCADE")
      if _table_exists("users"):
         _ensure_column("users", "is_admin", "BOOLEAN DEFAULT FALSE")

      _ensure_first_admin()
      _assign_orphan_rows_to_admin()
      _database_initialized = True
      _initialize_vector_store(require_vector=require_vector)

def _initialize_vector_store(require_vector: bool = False):
   global _vector_initialized
   if _vector_initialized:
      return

   try:
      db.connect(reuse_if_open=True)
      db.execute_sql("CREATE EXTENSION IF NOT EXISTS vector")
      db.create_tables([DocumentInformationChunks])
      _vector_initialized = True
   except Exception:
      print("Could not initialize pgvector-backed document chunk storage.")
      traceback.print_exc()
      if require_vector:
         raise
