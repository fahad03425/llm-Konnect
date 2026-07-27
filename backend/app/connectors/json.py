import pandas as pd
import json
import os
from typing import Optional
from app.connectors.base import Connector

class JSONConnector(Connector):
    def __init__(self, file_path: str):
        self.file_path = file_path

    def _find_records_array(self, data: any) -> list:
        """Finds the main array of records in potentially wrapped JSON."""
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            # Heuristic: find the largest array in the dict
            largest_array = []
            for val in data.values():
                if isinstance(val, list) and len(val) > len(largest_array):
                    largest_array = val
            return largest_array
        return []

    def _load_data(self) -> pd.DataFrame:
        if self.file_path.endswith('.jsonl'):
            df = pd.read_json(self.file_path, lines=True)
            return df
            
        with open(self.file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        records = self._find_records_array(data)
        if not records:
            return pd.DataFrame()
            
        # Flatten nested JSON objects (e.g., product.generic_name -> product_generic_name)
        df = pd.json_normalize(records, sep='_')
        return df

    def fetch(self, **kwargs) -> pd.DataFrame:
        df = self._load_data()
        if not df.empty:
            df['source_connector'] = "json"
            df['source_row'] = df.index + 1
        return df

    def preview(self, n: int = 5, **kwargs) -> pd.DataFrame:
        df = self._load_data()
        if not df.empty:
            df = df.head(n).copy()
            df['source_connector'] = "json"
            df['source_row'] = df.index + 1
        return df

    def describe(self) -> str:
        return f"JSON Connector reading from {os.path.basename(self.file_path)}"
