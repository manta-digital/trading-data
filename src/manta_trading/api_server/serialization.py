"""Shared response serialization for the API's time-series routes.

Every route that offers ``?format=json|msgpack`` builds its ``Response``
through :func:`timeseries_response`, so the two encoders, their media types
and the accepted format values are spelled once (design 188 D7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import msgpack
import orjson
from fastapi import Response

if TYPE_CHECKING:
    from pydantic import BaseModel

#: The accepted values of the ``format`` query parameter. Defined here and
#: imported by every route that offers the parameter, so the vocabulary exists
#: in exactly one place.
ResponseFormat = Literal["json", "msgpack"]

JSON_MEDIA_TYPE = "application/json"
MSGPACK_MEDIA_TYPE = "application/x-msgpack"


def timeseries_response(model: BaseModel, fmt: ResponseFormat) -> Response:
    """Encode ``model`` as JSON or msgpack with the matching media type.

    The dump uses ``mode="json"``, which renders ``Decimal`` as a string and
    ``datetime`` as ISO-8601 *before* either encoder sees the object (D7).
    That is what puts Kalshi's fixed-point prices on the wire as ``"0.4900"``
    rather than a lossy float, and it is why the msgpack call carries no
    ``default=str``: by the time ``msgpack.packb`` runs there is nothing left
    for a fallback encoder to convert.
    """
    payload = model.model_dump(mode="json")
    if fmt == "msgpack":
        return Response(
            content=msgpack.packb(payload),
            media_type=MSGPACK_MEDIA_TYPE,
        )
    return Response(content=orjson.dumps(payload), media_type=JSON_MEDIA_TYPE)
