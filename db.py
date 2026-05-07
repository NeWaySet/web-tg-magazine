try:
    import psycopg2
    from psycopg2 import OperationalError
except ModuleNotFoundError:
    psycopg2 = None
    OperationalError = Exception

try:
    import pg8000.dbapi
except ModuleNotFoundError:
    pg8000 = None

from config import Config
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

connection_pool = None
db_driver = None


def init_db_pool():
    global connection_pool, db_driver
    try:
        if psycopg2:
            connection_pool = psycopg2.pool.SimpleConnectionPool(
                1,
                10,
                dsn=Config.DATABASE_URL
            )
            db_driver = 'psycopg2'
            logger.info("PostgreSQL connection pool created with psycopg2")
            return True

        if pg8000:
            connection = pg8000.dbapi.connect(
                host=Config.DATABASE_HOST,
                database=Config.DATABASE_NAME,
                user=Config.DATABASE_USER,
                password=Config.DATABASE_PASSWORD,
                port=int(Config.DATABASE_PORT)
            )
            connection.close()
            connection_pool = True
            db_driver = 'pg8000'
            logger.info("PostgreSQL connection checked with pg8000")
            return True

        logger.error("PostgreSQL driver is not installed: install psycopg2-binary or pg8000")
        return False
    except OperationalError as e:
        logger.error(f"Database connection error: {e}")
        connection_pool = None
        return False
    except Exception as e:
        logger.error(f"Unexpected database pool error: {e}")
        connection_pool = None
        return False


def get_db_connection():
    global connection_pool
    if connection_pool is None:
        logger.warning("Database pool is not initialized. Trying to initialize...")
        if not init_db_pool():
            return None

    try:
        if db_driver == 'psycopg2':
            return connection_pool.getconn()

        if db_driver == 'pg8000':
            return pg8000.dbapi.connect(
                host=Config.DATABASE_HOST,
                database=Config.DATABASE_NAME,
                user=Config.DATABASE_USER,
                password=Config.DATABASE_PASSWORD,
                port=int(Config.DATABASE_PORT)
            )

        return None
    except OperationalError as e:
        logger.error(f"Database connection checkout error: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected database connection error: {e}")
        return None


def release_db_connection(connection):
    if not connection:
        return
    try:
        if db_driver == 'psycopg2' and connection_pool:
            connection_pool.putconn(connection)
        else:
            connection.close()
    except Exception as e:
        logger.error(f"Database connection release error: {e}")


def close_all_connections():
    global connection_pool
    if not connection_pool:
        return
    try:
        if db_driver == 'psycopg2':
            connection_pool.closeall()
        logger.info("Database connections closed")
    except Exception as e:
        logger.error(f"Database close error: {e}")
