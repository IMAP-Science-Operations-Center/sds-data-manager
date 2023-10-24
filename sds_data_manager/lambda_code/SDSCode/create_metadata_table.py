import psycopg2
from psycopg2 import Error

# Add code to get Secret instead
host = "rds-rdsinstance1d827d17-oaxcyeanywfy.cna6jyzzcvie.us-west-2.rds.amazonaws.com"
database = "imap"
user = "imap_user"
password = "JAg1iNkrRYe,28Lke.0vxU1TuEPr,G"

try:
    # Establish a connection to the PostgreSQL database
    connection = psycopg2.connect(
        host=host, database=database, user=user, password=password
    )

    # Create a cursor object to interact with the database
    cursor = connection.cursor()

    # SQL query to create a table
    create_table_query = """
        CREATE TABLE IF NOT EXISTS metadata (
            id SERIAL PRIMARY KEY,
            mission VARCHAR(100),
            type VARCHAR(100),
            instrument VARCHAR(100),
            level VARCHAR(10),
            year INT,
            month INT,
            day INT,
            version INT
        )
    """

    # Execute the create table query
    cursor.execute(create_table_query)
    print("Table Created")
except (Exception, Error) as error:
    print("Error while connecting to PostgreSQL:", error)

finally:
    # Close the cursor and connection
    if connection:
        connection.commit()
        cursor.close()
        connection.close()
        print("PostgreSQL connection is closed.")
