"""Framework serving runtime for Lucy (ADR 0010).

`create_app` builds the process-local serving app: health, metrics, realtime
SSE, models, and evals. Platform fleet routes and the Pili vertical live
elsewhere (lucy-platform, pili); they are not part of this open framework app.
"""

from lucy.serve.app import create_app

__all__ = ["create_app"]
