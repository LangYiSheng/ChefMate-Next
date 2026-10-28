"""Short-lived SQLite transactions release their OS handles on every path."""

import sqlite3


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *arguments):
        try:
            return super().__exit__(*arguments)
        finally:
            self.close()
