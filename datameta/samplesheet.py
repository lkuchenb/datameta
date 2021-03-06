# Copyright (c) 2021 Leon Kuchenbecker <leon.kuchenbecker@uni-tuebingen.de>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import pandas as pd
import pandas.api.types as pdtypes
#is_string_dtype, is_numeric_dtype, is_datetime64_dtype

from sqlalchemy import and_
from datameta.models import MetaDatum, MetaDatumRecord, MetaDataSet
import datetime

class SampleSheetColumnsIncompleteError(RuntimeError):

    @property
    def columns(self):
        return self.args[0]

class SampleSheetReadError(RuntimeError):
    pass

def get_metadata_keys(dbsession):
    """Queries the metadata keys (column names) that are currently configured
    """
    return [datum for datum in dbsession.query(MetaDatum).order_by(MetaDatum.order).all() ]

def query_pending_annotated(dbsession, user):
    """Queries all metadata sets that are pending for the currently logged in user
    """
    return dbsession.query(MetaDataSet).filter(and_(
        MetaDataSet.user==user,
        MetaDataSet.group==user.group,
        MetaDataSet.submission==None)
        ).all()

def dataframe_from_mdsets(mdsets):
    """Creates a data frame from a list of MetaDataSets
    """
    return pd.DataFrame(
            [
                {
                    mdrec.metadatum.name : str(mdrec.value) for mdrec in mdset.metadatumrecords
                    }
                for mdset in mdsets
                ]
            )

# HANDLING OF DATES, TIMES AND DATEIMTES
#
# All dates, times and datetimes will be stored as ISO string representations
# of datetimes in the database. dates and times will be combined with the
# minimum value counterparts to form a valid datetime, i.e. midnight for time
# and 0001-01-01 for date.
#
# The `datetimefmt` string provided in the application config is used to parse
# dates / times / datetimes from text-based sample sheet submissions (TSV, CSV)
# or when Excel files are submitted but the corresponding column is of type
# "text" rather than datetime.

def strptime_iso_or_empty(s, datetimefmt):
    try:
        return datetime.datetime.strptime(s, datetimefmt).isoformat()
    except ValueError:
        return ""

def string_conversion_dates(series, datetimefmt):
    """Converts a series of dates to ISO format strings. The dates can either
    be provided as datetime objects or will otherwise be casted to `str` and
    parsed using the provided datetime format string.
    """
    if pdtypes.is_datetime64_dtype(series):
        return series.map(lambda x : x.isoformat())
    return series.map(lambda x : strptime_iso_or_empty(x, datetimefmt))


def string_conversion(data, metadata):
    """Converts all columns of the provided sample sheet to strings.
    """
    for mdat in metadata:
        if mdat.datetimefmt is not None:
            data[mdat.name] = string_conversion_dates(data[mdat.name], mdat.datetimefmt)
        else:
            data[mdat.name] = data[mdat.name].astype(str)

def import_samplesheet(dbsession, file_like_obj, user):
    """Import a sample sheet into the database. Extracts the metadata from the
    sample sheet, handles date and time conversions if necessary and adds the
    metadata to the database. The metadata will be pending, i.e. not associated
    with a submission.
    """
    # Try to read the sample sheet
    try:
        data = pd.read_excel(file_like_obj)
    except Exception as e:
        raise SampleSheetReadError(f"{e}")

    # Query column names that we expect to see in the sample sheet (intra-submission duplicates)
    metadata         = get_metadata_keys(dbsession)
    metadata_names   = [ datum.name for datum in metadata ]

    missing_columns  = [ metadata_name for metadata_name in metadata_names if metadata_name not in data.columns ]
    if missing_columns:
        raise SampleSheetColumnsIncompleteError(missing_columns)

    # Limit the sample sheet to the columns of interest and drop duplicates
    data = data[metadata_names].drop_duplicates()

    # Convert all data to strings
    string_conversion(data, metadata)

    # Obtain the currently pending annotations
    cur_pending = dataframe_from_mdsets(query_pending_annotated(dbsession, user))

    # Concatenate data frames and drop annotations that were already submitted before (cross submission duplicates)
    data['__cur_pending__']         = 0
    cur_pending['__cur_pending__']  = 1
    new = pd.concat([data, cur_pending]).groupby(metadata_names).sum().reset_index()
    new = new[new.__cur_pending__==0]

    # Import the provided data
    sets = [
            # Create one MetaDataSet per row of the sample sheet
            MetaDataSet(
                user_id = user.id,
                group_id = user.group_id,
                metadatumrecords = [
                    # Create one MetaDatumRecord for each value in the row
                    MetaDatumRecord(
                        metadatum = metadatum,
                        value = str(row[metadatum.name])
                        )
                    for metadatum in metadata
                    ]
                )
            for _, row in new.iterrows()
            ]

    dbsession.add_all(sets)

    # Return the number of records that were added
    return len(sets)
