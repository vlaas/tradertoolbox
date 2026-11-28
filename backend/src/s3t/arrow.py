"""Columns as one Arrow IPC stream, the format of the bulk snapshots (API-06; WP-1.2).

The only module that imports pyarrow. pyarrow has no type information of its own, and the edge of
the process (``s3t.api``) is type-checked strictly, so the edge hands plain columns to this typed
function. The stream format is the one a browser's Arrow reader takes as it comes, without a file
footer.
"""

from collections.abc import Mapping, Sequence
from typing import Literal

import pyarrow as pa

type ColumnType = Literal["float64", "string", "bool"]

MEDIA_TYPE = "application/vnd.apache.arrow.stream"

_TYPES = {"float64": pa.float64(), "string": pa.string(), "bool": pa.bool_()}


def ipc_stream(
    columns: Mapping[str, tuple[ColumnType, Sequence[float | str | bool | int]]],
    metadata: Mapping[str, str],
) -> bytes:
    """One record batch with these columns, in their order, and the metadata on its schema.
    Every column has one length. A NaN stays a NaN: no number is turned into a null."""
    lengths = {len(values) for _, values in columns.values()}
    if len(lengths) > 1:
        raise ValueError("the columns of a snapshot have one length")
    arrays = {
        name: pa.array(list(values), _TYPES[kind]) for name, (kind, values) in columns.items()
    }
    table = pa.table(arrays).replace_schema_metadata(dict(metadata))
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return bytes(sink.getvalue().to_pybytes())
