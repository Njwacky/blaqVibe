"""Correlated, server-side upload stages. Never log ZIP contents or credentials."""
import logging
import time
import uuid
from contextlib import contextmanager
from functools import wraps

logger = logging.getLogger(__name__)


class UploadTrace:
    def __init__(self, source):
        self.source = source
        # Generated here, never accepted from a caller-supplied header.
        self.reference = uuid.uuid4().hex
        self.project_id = None

    @contextmanager
    def stage(self, name):
        started = time.monotonic()
        logger.info('upload ref=%s source=%s stage=%s event=started project_id=%s',
                    self.reference, self.source, name, self.project_id)
        try:
            yield
        except BaseException:
            # Log and RE-RAISE even Gunicorn's SystemExit: its 30-second worker
            # abort is not an Exception and used to leave no upload-stage clue.
            logger.exception(
                'upload ref=%s source=%s stage=%s event=failed project_id=%s elapsed_ms=%d',
                self.reference, self.source, name, self.project_id,
                (time.monotonic() - started) * 1000,
            )
            raise
        else:
            logger.info(
                'upload ref=%s source=%s stage=%s event=completed project_id=%s elapsed_ms=%d',
                self.reference, self.source, name, self.project_id,
                (time.monotonic() - started) * 1000,
            )


def trace_upload(source):
    """Cover decorators too: a Redis rate-limit error can precede the view."""
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method != 'POST':
                return view(request, *args, **kwargs)
            trace = request.upload_trace = UploadTrace(source)
            with trace.stage('request'):
                response = view(request, *args, **kwargs)
            response['X-Upload-Reference'] = trace.reference
            return response
        return wrapped
    return decorate
