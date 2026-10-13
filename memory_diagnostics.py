"""Low-overhead Linux RSS diagnostics; never logs request or credential data."""
import asyncio
from contextlib import contextmanager
import logging
import os
import time

logger = logging.getLogger(__name__)


def current_rss_mb():
    """Current resident memory, rather than resource.getrusage's peak RSS."""
    try:
        with open('/proc/self/statm', encoding='ascii') as source:
            resident_pages = int(source.read().split()[1])
        return resident_pages * os.sysconf('SC_PAGE_SIZE') / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        return None


def log_memory(event, **fields):
    rss = current_rss_mb()
    logger.info('Memory diagnostic: event=%s pid=%s rss_mb=%s %s',
                event, os.getpid(), 'unavailable' if rss is None else f'{rss:.2f}',
                ' '.join(f'{key}={value}' for key, value in fields.items()))


@contextmanager
def memory_span(job, **fields):
    before = current_rss_mb()
    started = time.monotonic()
    log_memory('job_start', job=job, **fields)
    outcome = 'ok'
    try:
        yield
    except BaseException:
        outcome = 'error_or_cancelled'
        raise
    finally:
        after = current_rss_mb()
        delta = 'unavailable' if before is None or after is None else f'{after-before:.2f}'
        log_memory('job_end', job=job, outcome=outcome,
                   elapsed_seconds=f'{time.monotonic()-started:.2f}', delta_mb=delta, **fields)


async def memory_sampler():
    while True:
        log_memory('sample')
        await asyncio.sleep(60)
