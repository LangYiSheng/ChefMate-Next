import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data_cleaning.config import database_path
from data_cleaning.models.batch import BatchRepository
from data_cleaning.services.batch import execute

if __name__ == "__main__":
    execute(BatchRepository(database_path()), sys.argv[1])
