#!/usr/bin/env python3

# Copyright (c) Facebook, Inc. and its affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""
General utilities for helping writing ParlAI unit and integration tests.
"""

import os
import unittest
import contextlib
import tempfile
import shutil
import io
import signal
from typing import Tuple, Dict, Any
from parlai.core.opt import Opt
import parlai.utils.logging as logging
from parlai.utils.io import PathManager


try:
    import torch

    TORCH_AVAILABLE = True
    GPU_AVAILABLE = torch.cuda.device_count() > 0
except ImportError:
    TORCH_AVAILABLE = False
    GPU_AVAILABLE = False

try:
    import torchvision  # noqa: F401

    VISION_AVAILABLE = True
except ImportError:
    VISION_AVAILABLE = False

try:
    import git

    git_ = git.Git()
    GIT_AVAILABLE = True
except ImportError:
    git_ = None
    GIT_AVAILABLE = False

try:
    import subword_nmt  # noqa: F401

    BPE_INSTALLED = True
except ImportError:
    BPE_INSTALLED = False

try:
    import maskrcnn_benchmark  # noqa: F401
    import cv2  # noqa: F401

    DETECTRON_AVAILABLE = True
except ImportError:
    DETECTRON_AVAILABLE = False


def is_this_circleci():
    """
    Return if we are currently running in CircleCI.
    """
    return bool(os.environ.get('CIRCLECI'))


def skipUnlessTorch(testfn, reason='pytorch is not installed'):
    """
    Decorate a test to skip if torch is not installed.
    """
    return unittest.skipUnless(TORCH_AVAILABLE, reason)(testfn)


def skipIfGPU(testfn, reason='Test is CPU-only'):
    """
    Decorate a test to skip if a GPU is available.

    Useful for disabling hogwild tests.
    """
    return unittest.skipIf(GPU_AVAILABLE, reason)(testfn)


def skipUnlessGPU(testfn, reason='Test requires a GPU'):
    """
    Decorate a test to skip if no GPU is available.
    """
    return unittest.skipUnless(GPU_AVAILABLE, reason)(testfn)


def skipUnlessBPE(testfn, reason='Test requires subword NMT'):
    """
    Decorate a test to skip if BPE is not installed.
    """
    return unittest.skipUnless(BPE_INSTALLED, reason)(testfn)


def skipIfCircleCI(testfn, reason='Test disabled in CircleCI'):
    """
    Decorate a test to skip if running on CircleCI.
    """
    return unittest.skipIf(is_this_circleci(), reason)(testfn)


def skipUnlessVision(testfn, reason='torchvision not installed'):
    """
    Decorate a test to skip unless torchvision is installed.
    """
    return unittest.skipUnless(VISION_AVAILABLE, reason)(testfn)


def skipUnlessDetectron(
    testfn, reason='maskrcnn_benchmark and/or opencv not installed'
):
    """
    Decorate a test to skip unless maskrcnn_benchmark and opencv are installed.
    """
    return unittest.skipUnless(DETECTRON_AVAILABLE, reason)(testfn)


class retry(object):
    """
    Decorator for flaky tests. Test is run up to ntries times, retrying on failure.

    :param ntries:
        the number of tries to attempt
    :param log_retry:
        if True, prints to stdout on retry to avoid being seen as "hanging"

    On the last time, the test will simply fail.

    >>> @retry(ntries=10)
    ... def test_flaky(self):
    ...     import random
    ...     self.assertLess(0.5, random.random())
    """

    def __init__(self, ntries=3, log_retry=False):
        self.ntries = ntries
        self.log_retry = log_retry

    def __call__(self, testfn):
        """
        Call testfn(), possibly multiple times on failureException.
        """
        from functools import wraps

        @wraps(testfn)
        def _wrapper(testself, *args, **kwargs):
            for _ in range(self.ntries - 1):
                try:
                    return testfn(testself, *args, **kwargs)
                except testself.failureException:
                    if self.log_retry:
                        logging.debug("Retrying {}".format(testfn))
            # last time, actually throw any errors there may be
            return testfn(testself, *args, **kwargs)

        return _wrapper


def git_ls_files(root=None, skip_nonexisting=True):
    """
    List all files tracked by git.
    """
    filenames = git_.ls_files(root).split('\n')
    if skip_nonexisting:
        filenames = [fn for fn in filenames if PathManager.exists(fn)]
    return filenames


def git_ls_dirs(root=None):
    """
    List all folders tracked by git.
    """
    dirs = set()
    for fn in git_ls_files(root):
        dirs.add(os.path.dirname(fn))
    return list(dirs)


def git_changed_files(skip_nonexisting=True):
    """
    List all the changed files in the git repository.

    :param bool skip_nonexisting:
        If true, ignore files that don't exist on disk. This is useful for
        disregarding files created in master, but don't exist in HEAD.
    """
    fork_point = git_.merge_base('origin/master', 'HEAD').strip()
    filenames = git_.diff('--name-only', fork_point).split('\n')
    if skip_nonexisting:
        filenames = [fn for fn in filenames if PathManager.exists(fn)]
    return filenames


def git_commit_messages():
    """
    Output each commit message between here and master.
    """
    fork_point = git_.merge_base('origin/master', 'HEAD').strip()
    messages = git_.log(fork_point + '..HEAD')
    return messages


def is_new_task_filename(filename):
    """
    Check if a given filename counts as a new task.

    Used in tests and test triggers, and only here to avoid redundancy.
    """
    return (
        'parlai/tasks' in filename
        and 'README' not in filename
        and 'task_list.py' not in filename
    )


@contextlib.contextmanager
def capture_output():
    """
    Suppress all logging output into a single buffer.

    Use as a context manager.

    >>> with capture_output() as output:
    ...     print('hello')
    >>> output.getvalue()
    'hello'
    """
    sio = io.StringIO()
    with contextlib.redirect_stdout(sio), contextlib.redirect_stderr(sio):
        yield sio


@contextlib.contextmanager
def tempdir():
    """
    Create a temporary directory.

    Use as a context manager so the directory is automatically cleaned up.

    >>> with tempdir() as tmpdir:
    ...    print(tmpdir)  # prints a folder like /tmp/randomname
    """
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@contextlib.contextmanager
def timeout(time: int = 30):
    """
    Raise a timeout if a function does not return in time `time`.

    Use as a context manager, so that the signal class can reset it's alarm for
    `SIGALARM`

    :param int time:
        Time in seconds to wait for timeout. Default is 30 seconds.
    """
    assert time >= 0, 'Time specified in timeout must be nonnegative.'

    def _handler(signum, frame):
        raise TimeoutError

    signal.signal(signal.SIGALRM, _handler)
    signal.alarm(time)

    try:
        yield
    except TimeoutError as e:
        raise e
    finally:
        signal.signal(signal.SIGALRM, signal.SIG_IGN)


def train_model(opt: Opt) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Run through a TrainLoop.

    If model_file is not in opt, then this helper will create a temporary
    directory to store the model, dict, etc.

    :return: (stdout, valid_results, test_results)
    :rtype: (str, dict, dict)
    """
    import parlai.scripts.train_model as tms

    opt = Opt(opt)
    with tempdir() as tmpdir:
        if 'model_file' not in opt:
            opt = opt.fork(model_file=os.path.join(tmpdir, 'model'))
        if 'dict_file' not in opt:
            opt = opt.fork(dict_file=opt['model_file'] + '.dict')
        # Parse verification
        valid, test = tms.TrainModel.main(**opt)

    return valid, test


def eval_model(
    opt, skip_valid=False, skip_test=False, valid_datatype='valid', test_datatype='test'
):
    """
    Run through an evaluation loop.

    :param opt:
        Any non-default options you wish to set.
    :param bool skip_valid:
        If true skips the valid evaluation, and the first return value will be None.
    :param bool skip_test:
        If true skips the test evaluation, and the second return value will be None.
    :param str valid_datatype:
        If custom datatype required for valid, e.g. train:evalmode, specify here

    :return: (valid_results, test_results)
    :rtype: (dict, dict)

    If model_file is not in opt, then this helper will create a temporary directory
    to store the model files, and clean up afterwards. You can keep the directory
    by disabling autocleanup
    """
    import parlai.scripts.eval_model as ems

    opt = Opt(opt)
    if opt.get('model_file') and not opt.get('dict_file'):
        opt = opt.fork(dict_file=opt['model_file'] + '.dict')

    if valid_datatype is None:
        valid_datatype = 'valid'
    if test_datatype is None:
        test_datatype = 'test'

    opt = opt.fork(datatype=valid_datatype)
    valid = None if skip_valid else ems.EvalModel.main(**opt)
    opt = opt.fork(datatype=test_datatype)
    test = None if skip_test else ems.EvalModel.main(**opt)

    return valid, test


def display_data(opt):
    """
    Run through a display data run.

    :return: (stdout_train, stdout_valid, stdout_test)
    :rtype: (str, str, str)
    """
    import parlai.scripts.display_data as dd

    parser = dd.setup_args()
    parser.set_params(**opt)
    popt = parser.parse_args([])

    with capture_output() as train_output:
        dd.display_data(popt.fork(datatype='train:stream'))
    with capture_output() as valid_output:
        dd.display_data(popt.fork(datatype='valid:stream'))
    with capture_output() as test_output:
        dd.display_data(popt.fork(datatype='test:stream'))

    return (train_output.getvalue(), valid_output.getvalue(), test_output.getvalue())


def display_model(opt) -> Tuple[str, str, str]:
    """
    Run display_model.py.

    :return: (stdout_train, stdout_valid, stdout_test)
    """
    import parlai.scripts.display_model as dm

    parser = dm.setup_args()
    parser.set_params(**opt)
    popt = parser.parse_args([])
    with capture_output() as train_output:
        # evalmode so that we don't hit train_step
        dm.display_model(popt.fork(datatype='train:evalmode:stream'))
    with capture_output() as valid_output:
        dm.display_model(popt.fork(datatype='valid:stream'))
    with capture_output() as test_output:
        dm.display_model(popt.fork(datatype='test:stream'))
    return (train_output.getvalue(), valid_output.getvalue(), test_output.getvalue())


class AutoTeacherTest:
    def _run_display_data(self, datatype, **kwargs):
        import parlai.scripts.display_data as dd

        dd.DisplayData.main(task=self.task, datatype=datatype, verbose=True, **kwargs)

    def test_train(self):
        """
        Test --datatype train.
        """
        return self._run_display_data('train')

    def test_train_stream(self):
        """
        Test --datatype train:stream.
        """
        return self._run_display_data('train:stream')

    def test_train_stream_ordered(self):
        """
        Test --datatype train:stream:ordered.
        """
        return self._run_display_data('train:stream:ordered')

    def test_valid(self):
        """
        Test --datatype valid.
        """
        return self._run_display_data('valid')

    def test_valid_stream(self):
        """
        Test --datatype valid:stream.
        """
        return self._run_display_data('valid:stream')

    def test_test(self):
        """
        Test --datatype test.
        """
        return self._run_display_data('test')

    def test_test_stream(self):
        """
        Test --datatype test:stream.
        """
        return self._run_display_data('test:stream')

    def test_bs2_train(self):
        """
        Test --datatype train.
        """
        return self._run_display_data('train', batchsize=2)

    def test_bs2_train_stream(self):
        """
        Test --datatype train:stream.
        """
        return self._run_display_data('train:stream', batchsize=2)

    def test_bs2_train_stream_ordered(self):
        """
        Test --datatype train:stream:ordered.
        """
        return self._run_display_data('train:stream:ordered', batchsize=2)

    def test_bs2_valid(self):
        """
        Test --datatype valid.
        """
        return self._run_display_data('valid', batchsize=2)

    def test_bs2_valid_stream(self):
        """
        Test --datatype valid:stream.
        """
        return self._run_display_data('valid:stream', batchsize=2)

    def test_bs2_test(self):
        """
        Test --datatype test.
        """
        return self._run_display_data('test', batchsize=2)

    def test_bs2_test_stream(self):
        """
        Test --datatype test:stream.
        """
        return self._run_display_data('test:stream', batchsize=2)
