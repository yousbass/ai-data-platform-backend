from pathlib import Path
import pandas as pd


def read_file(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".csv":
        try:
            df = pd.read_csv(path)
        except UnicodeDecodeError:
            df = pd.read_csv(path, encoding="latin1")
    elif suffix in [".xlsx", ".xls"]:
        df = pd.read_excel(path)
    else:
        raise ValueError("Only CSV and Excel files are supported in this MVP.")

    df.columns = [str(c).strip() for c in df.columns]
    return df
