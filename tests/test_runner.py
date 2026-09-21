import sys
import tempfile
import time
import unittest
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from types import SimpleNamespace
from unittest.mock import patch
from sentinel_1_stack import runner
from sentinel_1_stack.runner import command_groups, execute_group, unwrap_enabled
from sentinel_1_stack.workspace import WorkspaceError


class RunnerTests(unittest.TestCase):
    def test_unwrap_limit_can_change_on_resume_without_rerunning_completed_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            processing, logs = root/'processing', root/'logs'
            processing.mkdir()
            logs.mkdir()
            groups = [[['a'], ['b'], ['c']], [['d']]]
            steps = [('run_15_filter_coherence', [[['filter']]]), ('run_16_unwrap', groups)]
            args = SimpleNamespace(config=root/'config.yaml', dry_run=False, resume=False, unwrap_jobs=2)
            with patch.object(runner, 'execution_plan', return_value=(processing, logs, 'same', steps, True)), \
                    patch.object(runner, 'execute_group', side_effect=[None, WorkspaceError('killed')]) as execute, \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(runner.run(args), 2)
                self.assertEqual(execute.call_args_list[-1].args[0], [['a'], ['b']])
                args.resume, args.unwrap_jobs = True, 1
                execute.side_effect = None
                execute.reset_mock()
                self.assertEqual(runner.run(args), 0)
                self.assertEqual([c.args[0] for c in execute.call_args_list],
                                 [[['a']], [['b']], [['c']], [['d']]])
                state = json.loads((logs/'run.json').read_text())
                self.assertEqual(state['steps']['run_15_filter_coherence']['attempts'], 1)
                self.assertEqual(state['steps']['run_16_unwrap']['attempts'], 2)
                self.assertEqual(state['steps']['run_16_unwrap']['unwrap_jobs'], 1)
                self.assertNotIn('error', state)
                self.assertEqual(len(state['steps']['run_16_unwrap']['logs']), 4)

    def test_invalid_unwrap_limit_does_not_touch_project(self):
        with patch.object(runner, 'execution_plan') as plan, redirect_stderr(io.StringIO()):
            for value in (0, -1, True):
                self.assertEqual(runner.run(SimpleNamespace(unwrap_jobs=value)), 2)
            plan.assert_not_called()

    def test_sigkill_is_reported_as_possible_oom(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(WorkspaceError) as caught:
                execute_group([[sys.executable, '-c', 'import os,signal; os.kill(os.getpid(),signal.SIGKILL)']],
                              root, [root/'killed.log'])
            self.assertIn('SIGKILL', str(caught.exception))
            self.assertIn('断定できません', str(caught.exception))
            self.assertIn('--resume --unwrap-jobs 1', str(caught.exception))

    def test_esd_failure_shows_japanese_guidance_but_other_failures_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'failure.log'
            path.write_text('Exception: Coherence threshold too strict. No points left for reliable ESD estimate\n')
            message = runner.failure_guidance(path)
            self.assertIn('NESD の有効画素不足', message)
            self.assertIn('processing.esd_coherence_threshold', message)
            self.assertIn('scripts/run.sh prepare config/project.yaml', message)
            self.assertIn('--resume', message)
            path.write_text('Some unrelated failure')
            self.assertEqual(runner.failure_guidance(path), '')

    def test_resume_skips_success_and_can_add_unwrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            processing, logs = root/'processing', root/'logs'
            processing.mkdir()
            logs.mkdir()
            steps = [('run_01', [[['first']]]), ('run_02', [[['second']]])]
            args = SimpleNamespace(config=root/'config.yaml', dry_run=False, resume=False)
            with patch.object(runner, 'execution_plan', return_value=(processing, logs, 'same', steps, False)) as plan, \
                    patch.object(runner, 'execute_group', side_effect=[None, WorkspaceError('failure')]) as execute, \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(runner.run(args), 2)
                state = json.loads((logs/'run.json').read_text())
                self.assertEqual(state['steps']['run_01']['status'], 'complete')
                self.assertEqual(state['steps']['run_02']['status'], 'failed')
                args.resume = True
                execute.side_effect = None
                execute.reset_mock()
                plan.return_value = (processing, logs, 'same', steps + [('run_03_unwrap', [[['unwrap']]])], True)
                self.assertEqual(runner.run(args), 0)
                self.assertEqual([x.args[0] for x in execute.call_args_list], [[['second']], [['unwrap']]])

    def test_native_log_is_stored_in_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            processing, logs = root/'processing', root/'logs'
            processing.mkdir()
            logs.mkdir()
            (processing/'isce.log').write_text('previous\n')
            runner.route_native_log(processing, logs)
            with (processing/'isce.log').open('a') as stream:
                stream.write('new\n')
            self.assertEqual((logs/'isce.log').read_text(), 'previous\nnew\n')

    def test_unwrap_is_default_and_single_yaml_override(self):
        self.assertTrue(unwrap_enabled({}))
        self.assertTrue(unwrap_enabled({'processing': {'unwrap': True}}))
        self.assertFalse(unwrap_enabled({'processing': {'unwrap': False}}))
        with self.assertRaises(WorkspaceError):
            unwrap_enabled({'processing': {'unwrap': 'false'}})

    def test_background_wait_and_trailing_group(self):
        self.assertEqual(list(command_groups('a -x 1 &\nb &\nwait\nc\nd &\n')),
                         [[['a', '-x', '1'], ['b']], [['c']], [['d']]])
        with self.assertRaises(WorkspaceError):
            list(command_groups('a && b'))

    def test_success_waits_for_every_child_and_captures_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = [root/'a.log', root/'b.log']
            execute_group([[sys.executable, '-c', 'print("one")'],
                           [sys.executable, '-c', 'import time; time.sleep(0.2); print("two")']], root, logs)
            self.assertIn('one', logs[0].read_text())
            self.assertIn('two', logs[1].read_text())

    def test_failure_cancels_sibling_instead_of_being_hidden_by_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            start = time.monotonic()
            with self.assertRaisesRegex(WorkspaceError, '7'):
                execute_group([[sys.executable, '-c', 'raise SystemExit(7)'],
                               [sys.executable, '-c', 'import time; time.sleep(30)']],
                              root, [root/'a.log', root/'b.log'])
            self.assertLess(time.monotonic()-start, 10)
