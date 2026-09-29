"""Process-local transcription queues shared by the web app and both workers."""

from threading import Lock


AUTO_YOUTUBE = "automatic_youtube"
AUTO_RSS = "automatic_rss"


class TranscriptionQueues:
    def __init__(self):
        self._lock = Lock()
        self._queues = {}

    def enqueue(self, queue_name, filename, prepare=None):
        """Append once, preserving order; return False if already pending.

        A manual request can remove old output in prepare() before publishing
        the job. The consumer cannot take that job halfway through preparation.
        """
        with self._lock:
            pending = self._queues.setdefault(queue_name, {})
            if filename in pending:
                return False
            if prepare is not None:
                prepare()
            pending[filename] = None
            return True

    def drain(self, queue_names):
        """Atomically take a batch; later additions remain for the next pass.

        As with the old queue files, deduplication covers pending jobs, not
        jobs already taken by the worker. Unselected engine queues stay intact.
        """
        with self._lock:
            return {
                name: list(self._queues.pop(name, {}))
                for name in queue_names
            }


transcription_queues = TranscriptionQueues()
