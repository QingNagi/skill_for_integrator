"""Supervise persistent draft processes inside the restricted batch environment."""
import contextlib
import io
import json
import multiprocessing
from multiprocessing.connection import wait
import os
import sys
import time


JOB_TIMEOUT = 7200


def encode(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def draft_worker(channel, defaults, quote):
    from dilib.cli import main
    with channel:
        while True:
            try:
                directory = json.loads(channel.recv_bytes())
            except EOFError:
                return
            args = ["draft", directory, "--json", "--strict-exit"]
            if defaults:
                args.extend(["--defaults", "--user-quote", quote])
            output = io.StringIO()
            try:
                with contextlib.redirect_stdout(output):
                    status = main(args)
                result = output.getvalue().strip()
            except Exception as exc:
                status, result = 2, str(exc)
            channel.send_bytes(encode([status, result]))


def pool_results(directories, workers, defaults=False, quote=None, timeout=JOB_TIMEOUT):
    """Give each live worker one job, replacing failed workers only for untouched jobs."""
    if not directories:
        return []
    ctx = multiprocessing.get_context("spawn")
    slots = {}
    results = [None] * len(directories)
    next_job = 0

    def release(channel, *, failed=False):
        proc, _, _ = slots.pop(channel)
        channel.close()
        if failed and proc.is_alive():
            proc.terminate()
        proc.join(timeout=2)
        if proc.is_alive():
            proc.kill()
            proc.join()
        proc.close()

    def send_job(channel, proc):
        nonlocal next_job
        index = next_job
        next_job += 1
        slots[channel] = (proc, index, time.monotonic() + timeout)
        try:
            channel.send_bytes(encode(directories[index]))
        except OSError as exc:
            results[index] = [2, "草稿工作进程通信失败：%s" % exc]
            release(channel, failed=True)

    # Windows' connection.wait() uses a bounded wait handle set on Python 3.10–3.12.
    width = min(workers, len(directories), 60 if os.name == "nt" else workers)
    try:
        while next_job < len(directories) or slots:
            while next_job < len(directories) and len(slots) < width:
                parent, child = ctx.Pipe()
                proc = ctx.Process(target=draft_worker, args=(child, defaults, quote))
                try:
                    proc.start()
                except OSError as exc:
                    parent.close()
                    results[next_job] = [2, "无法启动草稿工作进程：%s" % exc]
                    next_job += 1
                    continue
                finally:
                    child.close()
                send_job(parent, proc)
            if not slots:
                continue
            deadline = min(end for _, _, end in slots.values())
            ready = wait(slots, timeout=max(0, deadline - time.monotonic()))
            for channel in list(slots):
                proc, index, end = slots[channel]
                if channel in ready:
                    try:
                        results[index] = json.loads(channel.recv_bytes())
                    except (EOFError, OSError, ValueError) as exc:
                        results[index] = [2, "草稿工作进程意外结束：%s" % exc]
                        release(channel, failed=True)
                        continue
                    if next_job < len(directories):
                        send_job(channel, proc)
                    else:
                        release(channel)
                elif time.monotonic() >= end:
                    results[index] = [2, "此任务生成草稿超过两小时，请单独运行并检查输入。"]
                    release(channel, failed=True)
    finally:
        for channel in list(slots):
            release(channel, failed=True)
    return results


if __name__ == "__main__":
    request = json.load(sys.stdin.buffer)
    results = pool_results(**request)
    sys.stdout.buffer.write(encode(results))
