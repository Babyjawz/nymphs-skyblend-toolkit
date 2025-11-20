import time

class Timer:
    """Simple replacement for deprecated time.clock() using perf_counter()."""

    def __init__(self):
        self.start_time = time.perf_counter()

    def elapsed(self):
        return time.perf_counter() - self.start_time

    def reset(self):
        self.start_time = time.perf_counter()
