# SPDX-License-Identifier: Apache-2.0
"""Real subprocess pipes, synthetic owner replies; no hardware or model score."""

import copy
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import tau_selected_driver as driver
import test_tau_selected_session as fixtures
import test_tau_selected_scorer as scorer

ROOT = Path(__file__).resolve().parents[1]
tau, worker, owners, runner = driver.tau, driver.worker, driver.owners, scorer.runner
CHILD = """
import os, sys, time
sys.path.insert(0, sys.argv[1])
import tau_transport as transport
import tau_pipe_trial as trial
channel = transport.Channel(os.dup(0), os.dup(1))
with open(os.devnull, 'r+b', buffering=0) as sink:
    for fd in (0, 1, 2): os.dup2(sink.fileno(), fd)
proxy = transport.WorkerProxy(channel, time.monotonic() + 4)
def execute(root, manifest, selection, bridge):
    bridge.assertions = ['assertion']
    print('PRIVATE_STDOUT_CANARY')
    os.write(2, b'PRIVATE_STDERR_CANARY')
    for role in ('simulator', 'agent', 'evaluator'):
        bridge.complete(model=transport.ALIASES[role], messages=[{'role':'user','content':'PRIVATE_MESSAGE_CANARY'}])
    return 1.0
try:
    trial.execute(None, {}, {}, proxy, run=execute)
finally:
    channel.close()
"""


class SelectedDriverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.payload = Path(self.temp.name)
        path = self.payload / 'upstream/data/tau2/domains/retail/tasks.json'
        path.parent.mkdir(parents=True)
        path.write_bytes(tau.encoded([{'id': '2', 'evaluation_criteria': {'nl_assertions': ['assertion']}}]))
        self.plan = scorer.fixture()[3]
        self.frozen = fixtures.manifest()
        self.channels = {name: fixtures.OwnerReplies(binding) for name, binding in self.frozen['owners'].items()}
        self.owners = {name: owners.Coordinator(channel, self.frozen['owners'][name], time.monotonic() + 90)
                       for name, channel in self.channels.items()}
        for owner in self.owners.values():
            self.addCleanup(owner.close)

    def run_child(self, code=CHILD, *, deadline=None, plan=None):
        return driver.run_process([sys.executable, '-I', '-S', '-B', '-c', code, str(ROOT / 'scripts')],
            {}, self.payload, self.plan if plan is None else plan, self.frozen,
            time.monotonic() + 65 if deadline is None else deadline, **self.owners)

    def test_three_roles_share_budget_and_output_has_no_content_or_false_claim(self):
        result = self.run_child()
        row = result['trial']
        self.assertEqual((row['status'], row['requests'], row['usage']['total_tokens']), ('evaluated', 3, 24))
        self.assertEqual(row['role_requests'], {'agent': 1, 'simulator': 1, 'evaluator': 1})
        self.assertEqual(row['reward'], '1.0')
        self.assertIsNone(row['usage']['cost_usd'])
        self.assertEqual((result['attach_cleanup'], result['scorer_exit_code']), ('reaped', 0))
        self.assertTrue(result['scorer_protocol_completed'])
        self.assertFalse(result['quality_accepted'] or result['confidential'] or result['live_admission'])
        self.assertNotIn('CANARY', repr(result))
        self.assertTrue(all(channel.closed for channel in self.channels.values()))
        self.assertTrue(result['owners_finished'])
        self.assertEqual(result['owner_finish'], {'candidate': {'status': 'evaluated', 'completed_requests': 1},
                                                  'support': {'status': 'evaluated', 'completed_requests': 2}})
        for channel in self.channels.values():
            self.assertEqual(channel.sent[-1]['kind'], 'finish')

    def test_unknown_usage_retains_reservation_without_retry(self):
        self.channels['candidate'].error = 'infrastructure_error'
        result = self.run_child()
        self.assertEqual(result['trial']['status'], 'infrastructure_error')
        self.assertEqual(result['trial']['requests'], 2)
        self.assertEqual(result['reserved_tokens'], 8 + 8448)
        self.assertIsNone(result['trial']['usage'])
        self.assertFalse(result['usage_known'])
        self.assertEqual(len(self.channels['candidate'].sent), 2)
        self.assertIsNone(result['trial']['reward'])
        self.assertFalse(result['owners_finished'])
        self.assertIsNone(result['owner_finish']['candidate'])
        self.assertEqual(result['owner_finish']['support'],
                         {'status': 'infrastructure_error', 'completed_requests': 1})

    def test_finish_failure_removes_reward_but_preserves_usage_and_other_ack(self):
        with mock.patch.object(self.owners['candidate'], 'finish', side_effect=OSError('PRIVATE_CANARY')):
            result = self.run_child()
        self.assertEqual(result['trial']['status'], 'infrastructure_error')
        self.assertIsNone(result['trial']['reward'])
        self.assertEqual(result['trial']['usage']['total_tokens'], 24)
        self.assertFalse(result['owners_finished'])
        self.assertEqual(result['owner_finish']['support'], {'status': 'evaluated', 'completed_requests': 2})
        self.assertNotIn('CANARY', repr(result))
        self.assertTrue(all(channel.closed for channel in self.channels.values()))

    def test_owner_expiry_reserves_shutdown_time_and_never_renews(self):
        for owner in self.owners.values():
            owner.deadline = time.monotonic() + 2
        with mock.patch.object(driver.subprocess, 'Popen') as popen:
            result = self.run_child()
        popen.assert_not_called()
        self.assertFalse(result['scorer_protocol_completed'])
        self.assertIsNone(result['trial']['reward'])
        self.assertTrue(result['owners_finished'])
        for name, channel in self.channels.items():
            self.assertEqual(len(channel.sent), 1)
            self.assertLessEqual(channel.sent[0]['deadline'], self.owners[name].deadline)

    def test_pending_count_is_not_misreported_as_finished(self):
        original = driver.selected.Session.serve_once
        def pending(session):
            original(session)
            session.owners[0].pending = ('candidate', 'f' * 64, 7, time.monotonic() + 1)
            raise worker.Abort('budget_abort')
        with mock.patch.object(driver.selected.Session, 'serve_once', pending):
            result = self.run_child()
        self.assertEqual(result['trial']['status'], 'budget_abort')
        self.assertFalse(result['owners_finished'])
        self.assertIsNone(result['owner_finish']['candidate'])
        self.assertEqual(self.channels['candidate'].sent, [])
        self.assertTrue(all(channel.closed for channel in self.channels.values()))

    def test_wrong_mode_or_stale_owner_does_not_launch(self):
        for mutation in (lambda p: p.update(transport='scripted-parent-pipe'),
                         lambda p: p.update(selected_operational=1), lambda p: p.update(seconds=300),
                         lambda p: p['selection'].update(trial=False), lambda p: p.update(manifest_sha256='a' * 64)):
            plan = copy.deepcopy(self.plan)
            mutation(plan)
            with mock.patch.object(driver.subprocess, 'Popen') as popen, self.assertRaises((ValueError, worker.Abort)):
                self.run_child(plan=plan)
            popen.assert_not_called()
        self.owners['candidate'].sequence = 1
        with mock.patch.object(driver.subprocess, 'Popen') as popen, self.assertRaises(ValueError):
            self.run_child()
        popen.assert_not_called()

    def test_trial_timeout_uses_original_cleanup_reserve_and_reaps(self):
        children, start = [], time.monotonic()
        original = subprocess.Popen
        def capture(*args, **kwargs):
            self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
            self.assertTrue(kwargs['start_new_session'] and kwargs['close_fds'])
            process = original(*args, **kwargs)
            children.append(process)
            return process
        with mock.patch.object(driver.subprocess, 'Popen', side_effect=capture):
            result = self.run_child('import time; time.sleep(30)', deadline=start + driver.RESERVE + .15)
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(result['trial']['status'], 'timeout')
        self.assertFalse(result['scorer_protocol_completed'])
        self.assertEqual(result['attach_cleanup'], 'reaped')
        self.assertTrue(all(child.poll() is not None for child in children))
        self.assertTrue(all(channel.closed for channel in self.channels.values()))

    def assert_reap_failure_closes_channels(self, error):
        sessions = []
        def abort_trial(session):
            sessions.append(session)
            self.addCleanup(session.close)
            raise worker.Abort('timeout')
        process = mock.Mock()
        process.poll.return_value = 0
        process.wait.side_effect = error
        with mock.patch.object(driver.subprocess, 'Popen', return_value=process), \
                mock.patch.object(driver.selected.Session, 'serve_once', autospec=True, side_effect=abort_trial), \
                self.assertRaises(type(error)) as caught:
            self.run_child()
        self.assertIs(caught.exception, error)
        process.wait.assert_called_once()
        self.assertEqual(len(sessions), 1)
        self.assertTrue(all(channel.closed for channel in self.channels.values()))
        self.assertEqual((sessions[0].channel.read_fd, sessions[0].channel.write_fd), (-1, -1))
        self.assertTrue(all(not channel.sent for channel in self.channels.values()))

    def test_keyboard_interrupt_during_reap_closes_channels_and_propagates(self):
        self.assert_reap_failure_closes_channels(KeyboardInterrupt())

    def test_system_exit_during_reap_closes_channels_and_propagates(self):
        self.assert_reap_failure_closes_channels(SystemExit(7))

    def test_unexpected_reap_error_closes_channels_and_propagates(self):
        self.assert_reap_failure_closes_channels(RuntimeError('PRIVATE_ERROR_CANARY'))

    def test_pre_result_system_exit_is_not_replaced_by_finish_failure(self):
        error = SystemExit(7)
        process = mock.Mock()
        process.poll.return_value = process.wait.return_value = 0
        with mock.patch.object(driver.subprocess, 'Popen', return_value=process), \
                mock.patch.object(driver.selected.Session, 'serve_once', side_effect=error), \
                self.assertRaises(SystemExit) as caught:
            self.run_child()
        self.assertIs(caught.exception, error)
        self.assertTrue(all(channel.closed for channel in self.channels.values()))
        self.assertTrue(all(not channel.sent for channel in self.channels.values()))

    def test_bad_exit_after_finished_does_not_publish_reward_or_erase_usage(self):
        result = self.run_child(CHILD + '\nraise SystemExit(9)\n')
        self.assertEqual(result['trial']['status'], 'protocol_error')
        self.assertIsNone(result['trial']['reward'])
        self.assertEqual(result['trial']['usage']['total_tokens'], 24)
        self.assertFalse(result['scorer_protocol_completed'])
        self.assertEqual(result['scorer_exit_code'], 9)

    def test_malformed_output_and_fork_setup_fail_closed(self):
        result = self.run_child("print('PRIVATE_MESSAGE_CANARY')")
        self.assertEqual(result['trial']['status'], 'protocol_error')
        self.assertNotIn('CANARY', repr(result))

    def test_post_terminal_hang_is_bounded_and_loses_reward(self):
        result = self.run_child(CHILD + '\nimport time; time.sleep(30)\n',
                                deadline=time.monotonic() + driver.RESERVE + .4)
        self.assertEqual(result['trial']['status'], 'timeout')
        self.assertIsNone(result['trial']['reward'])
        self.assertEqual(result['trial']['usage']['total_tokens'], 24)
        self.assertEqual(result['attach_cleanup'], 'reaped')
        self.assertFalse(result['scorer_protocol_completed'])

    def test_spawn_failure_closes_channels_and_never_claims_container_cleanup(self):
        with mock.patch.object(driver.subprocess, 'Popen', side_effect=OSError('PRIVATE_ERROR_CANARY')):
            result = self.run_child()
        self.assertFalse(result['scorer_protocol_completed'])
        self.assertIsNone(result['trial']['reward'])
        self.assertEqual(result['attach_cleanup'], 'not-started')
        self.assertTrue(all(channel.closed for channel in self.channels.values()))
        self.assertNotIn('CANARY', repr(result))

    def test_shared_verified_loader_ignores_ambient_owner_classes_and_aborts(self):
        names = ('tau_preflight', 'tau_trial_worker', 'tau_aggregate', 'tau_transport',
                 *runner.SELECTED_PARENT_MODULES, 'tau_pipe_driver', 'tau_selected_driver')
        files = {'scripts/' + name + '.py': hashlib.sha256((ROOT / 'scripts' / (name + '.py')).read_bytes()).hexdigest()
                 for name in names}
        with mock.patch.dict(sys.modules, {name: object() for name in names}):
            loaded = runner.parent_driver({'files': files}, selected_operational=True)
            self.assertIs(loaded.worker, loaded.selected.local.worker)
            self.assertIs(loaded.owners.worker, loaded.worker)
            self.assertIs(loaded.owners.identity, loaded.owners.cpu.identity)
            self.assertIs(loaded.policy, loaded.selected.policy)
        for name in ('tau_owner_channel', 'tau_selected_session', 'tau_selected_driver'):
            invalid = dict(files)
            invalid['scripts/' + name + '.py'] = '0' * 64
            with self.assertRaises(ValueError):
                runner.parent_driver({'files': invalid}, selected_operational=True)
        with self.assertRaises(ValueError):
            runner.parent_driver({'files': files}, selected_operational=True, live_local=True)


if __name__ == '__main__':
    unittest.main()
