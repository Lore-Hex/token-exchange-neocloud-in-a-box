# SPDX-License-Identifier: Apache-2.0
"""Real POSIX alarm races stay in disposable, port-free subprocesses."""

from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AlarmTests(unittest.TestCase):
    def child(self, body):
        setup = f"""
import os, runpy, signal, time, types
worker = types.SimpleNamespace(**runpy.run_path({str(ROOT / 'scripts/tau_trial_worker.py')!r}))
original_handler = signal.getsignal(signal.SIGALRM)
original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
def pending():
    until = time.monotonic() + 2
    while signal.SIGALRM not in signal.sigpending():
        assert time.monotonic() < until, 'timer did not expire'
        time.sleep(.001)
def clean():
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) == original_handler
    assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == original_mask
    assert signal.SIGALRM not in signal.sigpending()
"""
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", setup + textwrap.dedent(body)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_pending_expiry_is_consumed_before_default_handler_restored(self):
        self.child("""
mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
try:
    try:
        with worker.alarm(time.monotonic() + 5):
            signal.setitimer(signal.ITIMER_REAL, .001)
            pending()
    except worker.Abort as exc:
        assert exc.status == 'timeout'
    else:
        raise AssertionError('pending expiry accepted')
    assert signal.SIGALRM in signal.pthread_sigmask(signal.SIG_BLOCK, set())
finally:
    signal.pthread_sigmask(signal.SIG_SETMASK, mask)
clean()
""")

    def test_expired_fresh_module_outer_deadline_is_delivered_without_rearming(self):
        self.child(f"""
outer = types.SimpleNamespace(**runpy.run_path({str(ROOT / 'scripts/tau_trial_worker.py')!r}))
assert outer.Abort is not worker.Abort
now = [time.monotonic()]
clock = lambda: now[0]
# Advance elapsed time only after both scopes exist; real signals remain blocked.
for module in (worker, outer):
    module.alarm.__wrapped__.__globals__['time'] = types.SimpleNamespace(monotonic=clock)
mask = signal.pthread_sigmask(signal.SIG_BLOCK, {{signal.SIGALRM}})
real_timer, armed = signal.setitimer, []
def timer(kind, seconds, *args):
    if seconds:
        armed.append(seconds)
    return real_timer(kind, seconds, *args)
signal.setitimer = timer
try:
    try:
        until = clock() + 5
        with outer.alarm(until, clock):
            try:
                with worker.alarm(until + 5, clock):
                    now[0] += 6
                    os.kill(os.getpid(), signal.SIGALRM)
                    assert signal.SIGALRM in signal.sigpending()
            except worker.Abort:
                raise AssertionError('outer deadline became inner-module Abort')
    except outer.Abort as exc:
        assert exc.status == 'timeout'
    else:
        raise AssertionError('outer deadline accepted')
finally:
    signal.setitimer = real_timer
    signal.pthread_sigmask(signal.SIG_SETMASK, mask)
assert len(armed) == 2, armed
clean()
""")

    def test_earlier_inner_timeout_preserves_unexpired_outer_deadline(self):
        self.child("""
until = time.monotonic() + 5
with worker.alarm(until):
    outer_handler = signal.getsignal(signal.SIGALRM)
    try:
        with worker.alarm(time.monotonic() + .01):
            time.sleep(.1)
    except worker.Abort as exc:
        assert exc.status == 'timeout'
    else:
        raise AssertionError('inner deadline accepted')
    assert signal.getsignal(signal.SIGALRM) is outer_handler
    left, interval = signal.getitimer(signal.ITIMER_REAL)
    assert 0 < left <= until - time.monotonic() + .001 and interval == 0
clean()
""")

    def test_pending_outer_handler_runs_once_synchronously(self):
        self.child("""
class OuterExpiry(BaseException): pass
calls = []
def outer_handler(signum, frame):
    calls.append(signum)
    raise OuterExpiry()
mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
signal.signal(signal.SIGALRM, outer_handler)
now = [time.monotonic()]
clock = lambda: now[0]
worker.alarm.__wrapped__.__globals__['time'] = types.SimpleNamespace(monotonic=clock)
signal.setitimer(signal.ITIMER_REAL, 5)
try:
    try:
        with worker.alarm(clock() + 10, clock):
            now[0] += 6
            os.kill(os.getpid(), signal.SIGALRM)
            assert signal.SIGALRM in signal.sigpending()
    except OuterExpiry:
        pass
    else:
        raise AssertionError('outer deadline not delivered')
    assert signal.getsignal(signal.SIGALRM) is outer_handler
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
finally:
    signal.signal(signal.SIGALRM, original_handler)
    signal.pthread_sigmask(signal.SIG_SETMASK, mask)
assert calls == [signal.SIGALRM]
clean()
""")

    def test_body_base_exceptions_and_immediate_expiry_restore_state(self):
        self.child("""
for error in (KeyboardInterrupt(), SystemExit(7), RuntimeError('synthetic')):
    try:
        with worker.alarm(time.monotonic() + 2):
            raise error
    except BaseException as caught:
        assert caught is error
    clean()
try:
    with worker.alarm(time.monotonic() - 1):
        raise AssertionError('expired body entered')
except worker.Abort as exc:
    assert exc.status == 'timeout'
clean()
""")

    def test_repeated_nested_expiry_does_not_leave_a_timer_or_handler(self):
        self.child("""
for _ in range(100):
    try:
        until = time.monotonic() + .002
        with worker.alarm(until):
            with worker.alarm(until):
                time.sleep(.01)
    except worker.Abort as exc:
        assert exc.status == 'timeout'
    else:
        raise AssertionError('expired body accepted')
    clean()
""")

    def test_late_background_delivery_does_not_interrupt_restoration(self):
        self.child("""
import threading
stop, ready = threading.Event(), threading.Event()
def background():
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGALRM})
    ready.set()
    stop.wait(5)
thread = threading.Thread(target=background)
thread.start()
assert ready.wait(2)
real_timer, delivered = signal.setitimer, []
def timer(kind, seconds, *args):
    result = real_timer(kind, seconds, *args)
    if seconds == 0 and not delivered:
        # Force an in-flight C-handler delivery on another unblocked thread
        # after disarm but before the original Python handler is restored.
        delivered.append(True)
        signal.pthread_kill(thread.ident, signal.SIGALRM)
        time.sleep(.01)
    return result
signal.setitimer = timer
try:
    try:
        with worker.alarm(time.monotonic() + 5):
            pass
    except worker.Abort as exc:
        assert exc.status == 'timeout'
    else:
        raise AssertionError('late signal accepted')
finally:
    signal.setitimer = real_timer
    stop.set()
    thread.join(2)
assert delivered == [True] and not thread.is_alive()
clean()
""")

    def test_non_main_rejection_preserves_enclosing_timer(self):
        self.child("""
import threading
errors = []
def background():
    try:
        with worker.alarm(time.monotonic() + 2):
            raise AssertionError('non-main body entered')
    except ValueError:
        errors.append('rejected')
until = time.monotonic() + 5
with worker.alarm(until):
    handler = signal.getsignal(signal.SIGALRM)
    thread = threading.Thread(target=background)
    thread.start()
    thread.join(2)
    assert not thread.is_alive() and errors == ['rejected']
    assert signal.getsignal(signal.SIGALRM) is handler
    left, interval = signal.getitimer(signal.ITIMER_REAL)
    assert 0 < left <= until - time.monotonic() + .001 and interval == 0
clean()
""")

    def test_stress_with_unblocked_background_thread_and_both_main_masks(self):
        for block_main in (False, True):
            with self.subTest(block_main=block_main):
                self.child(f"""
import threading
stop, ready = threading.Event(), threading.Event()
def background():
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {{signal.SIGALRM}})
    assert signal.SIGALRM not in signal.pthread_sigmask(signal.SIG_BLOCK, set())
    ready.set()
    while not stop.wait(.0001):
        pass
thread = threading.Thread(target=background)
thread.start()
assert ready.wait(2)
mask = signal.pthread_sigmask(signal.SIG_BLOCK, {{signal.SIGALRM}} if {block_main!r} else set())
try:
    for _ in range(100):
        try:
            until = time.monotonic() + .002
            with worker.alarm(until):
                with worker.alarm(until):
                    time.sleep(.01)
        except worker.Abort as exc:
            assert exc.status == 'timeout'
        else:
            raise AssertionError('expiry accepted')
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
        assert signal.getsignal(signal.SIGALRM) == original_handler
        assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == mask | ({{signal.SIGALRM}} if {block_main!r} else set())
finally:
    signal.pthread_sigmask(signal.SIG_SETMASK, mask)
    stop.set()
    thread.join(2)
assert not thread.is_alive()
clean()
""")


if __name__ == "__main__":
    unittest.main()
