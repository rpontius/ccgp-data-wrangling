import pygsheets
import numpy as np
import pandas as pd


"""Classes and functions for interacting with Google Sheets."""


class WGSTracking:
    """Class for getting data from WGSTracking sheet."""

    def __init__(self) -> None:
        gc = pygsheets.authorize(service_file="google_secret.json")
        self.sh = gc.open("WGSTracking")

    def _get_column_as_series(self, column: str) -> pd.Series:
        self.df.replace(
            r"^\s*$", np.nan, regex=True, inplace=True
        )  # Replace whitespace with nans to then remove them
        self.df.set_index("SpeciesProjectID", inplace=True)
        series = self.df[column]
        series = series.dropna()
        return series

    def reference_progress(self) -> pd.Series:
        wks = self.sh.worksheet_by_title("ReferenceProgress")
        self.df = wks.get_as_df()
        series = self._get_column_as_series("ReferenceStage")
        return series

    def expected_count(self) -> pd.Series:
        wks = self.sh.worksheet_by_title("ExpectedWGSbySpeciesProject")
        self.df = wks.get_as_df()
        series = self._get_column_as_series("sample number of species project")
        return series

    def reference_accession(self, project_id: str) -> pd.Series:
        wks = self.sh.worksheet_by_title("RAW-PGL CCGP Assemblies status")
        self.df = wks.get_as_df()
        series = self._get_column_as_series("NCBI Genome accession primary").astype(
            "str"
        )
        accession = series.get(project_id, default="NaN")
        return accession
