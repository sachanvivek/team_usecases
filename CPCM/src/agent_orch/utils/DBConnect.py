import mysql.connector
import configparser
import pandas as pd
from decimal import Decimal
import numpy as np

class DBConnect:
    def __init__(self):
        """Initialize the database connection using the configuration file."""
        self.config = configparser.ConfigParser()
        self.config.read('src/agent_orch/utils/config.ini')
        self.connection = None
        self.cursor = None
        self._connect()

    def _connect(self):
        """Establish a connection to the MySQL/MariaDB database."""
        db_config = self.config["Database"]
        try:
            self.connection = mysql.connector.connect(
                host=db_config['server'],
                database=db_config['database'],
                user=db_config['username'],
                password=db_config['password'],
                auth_plugin='mysql_native_password'  # helps with some MySQL setups
            )
            self.cursor = self.connection.cursor(dictionary=False)  # or dictionary=True if you want dict rows
            print("Database connection established.")
        except mysql.connector.Error as e:
            print("Error connecting to database:", e)
            raise

    def insert_autonum(self, table: str, data: dict):
        """Insert data into the specified table and return the last inserted ID."""
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['%s'] * len(data))
        query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
        try:
            self.cursor.execute(query, tuple(data.values()))
            self.connection.commit()
            last_id = self.cursor.lastrowid
            print(f"Insert successful. Last inserted ID: {last_id}")
            return last_id
        except mysql.connector.Error as e:
            print("Error during insert operation:", e)
            self.connection.rollback()
            return None    

    def insert(self, table: str, data: dict):
        """Insert data into the specified table."""
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['%s'] * len(data))
        query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
        try:
            self.cursor.execute(query, tuple(data.values()))
            self.connection.commit()
            print("Insert successful.")
        except mysql.connector.Error as e:
            print("Error during insert operation:", e)
            self.connection.rollback()

    def update(self, table: str, data: dict, condition: str):
        """Update data in the specified table with a condition."""
        set_clause = ', '.join([f"{col} = %s" for col in data.keys()])
        query = f"UPDATE {table} SET {set_clause} WHERE {condition}"
        try:
            self.cursor.execute(query, tuple(data.values()))
            self.connection.commit()
            print("Update successful.")
        except mysql.connector.Error as e:
            print("Error during update operation:", e)
            self.connection.rollback()

    def delete(self, table: str, condition: str):
        """Delete data from the specified table with a condition."""
        query = f"DELETE FROM {table} WHERE {condition}"
        try:
            self.cursor.execute(query)
            self.connection.commit()
            print("Delete successful.")
        except mysql.connector.Error as e:
            print("Error during delete operation:", e)
            self.connection.rollback()

    def _convert_value(self, value):
        """Convert database values to JSON-serializable Python types."""
        if isinstance(value, Decimal):
            return float(value)
        elif isinstance(value, (np.integer, np.int64, np.int32)):
            return int(value)
        elif isinstance(value, (np.floating, np.float64, np.float32)):
            return float(value)
        elif isinstance(value, bytes):
            return value.decode('utf-8')
        return value

    def select(self, table: str = None, columns: list = None, condition: str = None, raw_query: str = None, params: tuple = ()):
        """Select data from the specified table."""
        try:
            if raw_query:
                query = raw_query
            else:
                columns_str = ', '.join(columns)
                query = f"SELECT {columns_str} FROM {table}"
                if condition:
                    query += f" WHERE {condition}"

            self.cursor.execute(query, params)
            rows = self.cursor.fetchall()
            if rows:
                cols = [desc[0] for desc in self.cursor.description]
                # Convert all values to JSON-serializable types
                results = [
                    {col: self._convert_value(val) for col, val in zip(cols, row)}
                    for row in rows
                ]
                return results
            else:
                return []  # Return empty list instead of empty string
        except mysql.connector.Error as e:
            print("Error during select operation:", e)
            return []  # Return empty list instead of None

    # def query(self, table: str = None, columns: list = None, condition: str = None, raw_query: str = None, params: tuple = ()):
    #     """Alias for select() method for compatibility."""
    #     return self.select(table=table, columns=columns, condition=condition, raw_query=raw_query, params=params)

    def query(self, table: str = None, columns: list = None, condition: str = None, raw_query: str = None, params: tuple = ()):
        if self.connection is None or not self.connection.is_connected():
            self._connect()

        if raw_query:
            sql = raw_query
        else:
            cols = ', '.join(columns or ['*'])
            sql = f"SELECT {cols} FROM {table}"
            if condition:
                sql += f" WHERE {condition}"

        try:
            cur = self.connection.cursor(dictionary=False, buffered=True)
            cur.execute(sql, params)
            rows = cur.fetchall()

            if not rows:
                return []

            col_names = [d[0] for d in cur.description]
            return [
                {col: self._convert_value(val) for col, val in zip(col_names, row)}
                for row in rows
            ]

        except mysql.connector.Error as exc:
            print("Error during query:", exc)
            return []

        finally:
            try:
                cur.close()
            except Exception:
                pass

            if self.connection and self.connection.is_connected():
                self.connection.close()
                self.connection = None

   
    def insert_ignore(self, table: str, data: dict):
        """Perform an INSERT IGNORE to skip duplicate entries."""
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['%s'] * len(data))
        query = f"INSERT IGNORE INTO {table} ({columns}) VALUES ({placeholders})"
        try:
            self.cursor.execute(query, tuple(data.values()))
            self.connection.commit()
            print("Insert ignore operation completed successfully.")
        except mysql.connector.Error as e:
            print("Error during insert ignore operation:", e)
            self.connection.rollback()

    def insert_or_update(self, table: str, data: dict, key_columns: list):
        """Insert or update using MySQL's ON DUPLICATE KEY UPDATE."""
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['%s'] * len(data))
        update_clause = ', '.join([f"{col} = VALUES({col})" for col in data.keys()])
        query = f"""
        INSERT INTO {table} ({columns}) VALUES ({placeholders})
        ON DUPLICATE KEY UPDATE {update_clause}
        """
        try:
            self.cursor.execute(query, tuple(data.values()))
            self.connection.commit()
            print("Insert or update operation completed successfully.")
        except mysql.connector.Error as e:
            print("Error during insert or update operation:", e)
            self.connection.rollback()

    def update_from_dataframe(self, table: str, dataframe: pd.DataFrame, key_columns: list):
        """Update a MySQL table using data from a Pandas DataFrame."""
        try:
            for _, row in dataframe.iterrows():
                set_clause = ', '.join([f"{col} = %s" for col in dataframe.columns if col not in key_columns])
                where_clause = ' AND '.join([f"{key} = %s" for key in key_columns])
                query = f"UPDATE {table} SET {set_clause} WHERE {where_clause}"
                set_values = [row[col] for col in dataframe.columns if col not in key_columns]
                where_values = [row[key] for key in key_columns]
                params = set_values + where_values
                self.cursor.execute(query, params)
            self.connection.commit()
            print("Table updated successfully with DataFrame data.")
        except mysql.connector.Error as e:
            print("Error during update operation:", e)
            self.connection.rollback()

    def close(self):
        """Close the database connection."""
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
        print("Database connection closed.")
