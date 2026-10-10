"""Run file parsers in a separate process with a time limit and (on POSIX) a memory limit.

The readers in spreadsheets.py already stop at their size limits; this is the backstop for
whatever they miss (a library bug, a pathological file). A runaway parser is killed instead
of exhausting the server's memory or tying up a worker thread forever.
"""
import multiprocessing
import os
import threading

PARSE_TIMEOUT_SECONDS = float(os.environ.get("PARSER_TIMEOUT_SECONDS", "30"))
PARSE_MEMORY_MB = int(os.environ.get("PARSER_MEMORY_MB", "1024"))
_slots = threading.BoundedSemaphore(int(os.environ.get("PARSER_CONCURRENCY", "2")))


class SandboxError(RuntimeError):
    """The parser was killed or crashed; the message is safe to show to the user."""


def _child(conn, func, args, memory_mb: int) -> None:
    try:
        import resource  # POSIX only; Windows relies on the timeout and the readers' own limits

        limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ImportError, ValueError, OSError):
        pass
    try:
        result = ("ok", func(*args))
    except BaseException as exc:  # sent back and re-raised in the parent
        result = ("error", exc)
    try:
        conn.send(result)
    except Exception:
        conn.send(("error", SandboxError("파일을 읽는 중 오류가 났습니다.")))
    finally:
        conn.close()


def run(func, *args, timeout: float | None = None, memory_mb: int | None = None):
    """func(*args) in a fresh process. `func` must be a module-level function; its exceptions are re-raised here."""
    timeout = PARSE_TIMEOUT_SECONDS if timeout is None else timeout
    with _slots:
        ctx = multiprocessing.get_context("spawn")
        receiver, sender = ctx.Pipe(duplex=False)
        process = ctx.Process(target=_child, args=(sender, func, args, memory_mb or PARSE_MEMORY_MB), daemon=True)
        process.start()
        sender.close()
        finished = False
        try:
            if not receiver.poll(timeout):
                raise SandboxError(f"파일을 읽는 데 {timeout:g}초를 넘겨 중단했습니다. 파일을 나눠서 올리세요.")
            try:
                status, value = receiver.recv()
            except (EOFError, OSError) as exc:  # killed by the memory limit, or crashed
                raise SandboxError("파일이 너무 크거나 복잡해서 읽지 못했습니다.") from exc
            finished = True
        finally:
            receiver.close()
            process.join(2 if finished else 0)
            if process.is_alive():
                process.kill()
                process.join(5)
    if status == "error":
        if isinstance(value, MemoryError):
            raise SandboxError("파일이 너무 크거나 복잡해서 읽지 못했습니다.")
        raise value
    return value
