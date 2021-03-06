# -*- coding: utf-8 -*-

import contextlib
import os
import sys
from textwrap import dedent
import time
import traceback
import unittest

import ptvsd
from ptvsd.wrapper import INITIALIZE_RESPONSE  # noqa
from tests.helpers._io import captured_stdio
from tests.helpers.pydevd._live import LivePyDevd
from tests.helpers.script import set_lock, find_line
from tests.helpers.workspace import Workspace, PathEntry

from . import (
    VSCFixture,
    VSCTest,
)


class Fixture(VSCFixture):
    def __init__(self, source, new_fake=None):
        self._pydevd = LivePyDevd(source)
        super(Fixture, self).__init__(
            new_fake=new_fake,
            start_adapter=self._pydevd.start,
        )

    @property
    def _proc(self):
        return self._pydevd.binder.ptvsd.proc

    @property
    def binder(self):
        return self._pydevd.binder

    @property
    def thread(self):
        return self._pydevd.thread

    def install_sig_handler(self):
        self._pydevd._ptvsd.install_sig_handler()


class TestBase(VSCTest):

    FIXTURE = Fixture

    FILENAME = None
    SOURCE = ''

    def setUp(self):
        super(TestBase, self).setUp()
        self._pathentry = PathEntry()

        self._filename = None
        if self.FILENAME is not None:
            self.set_source_file(self.FILENAME, self.SOURCE)

    def tearDown(self):
        super(TestBase, self).tearDown()
        self._pathentry.cleanup()

    @property
    def pathentry(self):
        return self._pathentry

    @property
    def workspace(self):
        try:
            return vars(self)['_workspace']
            #return self._workspace
        except KeyError:
            self._workspace = Workspace()
            self.addCleanup(self._workspace.cleanup)
            return self._workspace

    @property
    def filename(self):
        return None if self._filename is None else self._filePath

    def _new_fixture(self, new_daemon):
        self.assertIsNotNone(self._filename)
        return self.FIXTURE(self._filename, new_daemon)

    def set_source_file(self, filename, content=None):
        self.assertIsNone(self._fix)
        if content is not None:
            filename = self.pathentry.write(filename, content=content)
        self.pathentry.install()
        self._filePath = filename
        self._filename = 'file:' + filename

    def set_module(self, name, content=None):
        self.assertIsNone(self._fix)
        if content is not None:
            self.write_module(name, content)
        self.pathentry.install()
        self._filename = 'module:' + name


##################################
# lifecycle tests


class LifecycleTests(TestBase, unittest.TestCase):

    FILENAME = 'spam.py'

    # Give some time for thread notification to arrive before finishing.
    SOURCE = 'import time;time.sleep(.5)'

    @contextlib.contextmanager
    def running(self):
        addr = (None, 8888)
        with self.fake.start(addr):
            #with self.fix.install_sig_handler():
            yield

    def test_launch(self):
        addr = (None, 8888)
        with self.fake.start(addr):
            with self.vsc.wait_for_event('initialized'):
                # initialize
                req_initialize = self.send_request('initialize', {
                    'adapterID': 'spam',
                })

                # attach
                req_attach = self.send_request('attach')

            # configuration
            with self.vsc.wait_for_event('thread'):
                req_config = self.send_request('configurationDone')

            # Normal ops would go here.

            # end
            with self.wait_for_events(['exited', 'terminated']):
                self.fix.binder.done()
            # TODO: Send a "disconnect" request?
            self.fix.binder.wait_until_done()
            received = self.vsc.received

        self.assert_vsc_received(
            received,
            [
                self.new_event(
                    'output',
                    category='telemetry',
                    output='ptvsd',
                    data={'version': ptvsd.__version__}),
                self.new_response(req_initialize, **INITIALIZE_RESPONSE),
                self.new_event('initialized'),
                self.new_response(req_attach),
                self.new_response(req_config),
                self.new_event(
                    'process',
                    **dict(
                        name=sys.argv[0],
                        systemProcessId=os.getpid(),
                        isLocalProcess=True,
                        startMethod='attach',
                    )),
                self.new_event('thread', reason='started', threadId=1),
                #self.new_event('exited', exitCode=0),
                #self.new_event('terminated'),
            ])


##################################
# "normal operation" tests


class VSCFlowTest(TestBase):
    @contextlib.contextmanager
    def launched(self, port=8888, **kwargs):
        kwargs.setdefault('process', False)
        kwargs.setdefault('disconnect', False)
        with self.lifecycle.launched(port=port, hide=True, **kwargs):
            yield
            self.fix.binder.done(close=False)

        try:
            self.fix.binder.wait_until_done()
        except Exception as ex:
            formatted_ex = traceback.format_exc()
            if hasattr(self, 'vsc') and hasattr(self.vsc, 'received'):
                message = """
    Session Messages:
    -----------------
    {}

    Original Error:
    ---------------
    {}""".format(os.linesep.join(self.vsc.received), formatted_ex)
                raise Exception(message)
            else:
                raise


class BreakpointTests(VSCFlowTest, unittest.TestCase):

    FILENAME = 'spam.py'
    SOURCE = dedent("""
        from __future__ import print_function

        class MyError(RuntimeError):
            pass

        #class Counter(object):
        #    def __init__(self, start=0):
        #        self._next = start
        #    def __repr__(self):
        #        return '{}(start={})'.format(type(self).__name__, self._next)
        #    def __int__(self):
        #        return self._next - 1
        #    __index__ = __int__
        #    def __iter__(self):
        #        return self
        #    def __next__(self):
        #        value = self._next
        #        self._next += 1
        #    def peek(self):
        #        return self._next
        #    def inc(self, diff=1):
        #        self._next += diff

        # <a>
        def inc(value, count=1):
            # <b>
            result = value + count
            return result

        # <c>
        x = 1
        # <d>
        x = inc(x)
        # <e>
        y = inc(x, 2)
        # <f>
        z = inc(3)
        # <g>
        print(x, y, z)
        # <h>
        raise MyError('ka-boom')
        """)

    def _set_lock(self, label=None, script=None):
        if script is None:
            if not os.path.exists(self.filename):
                script = self.SOURCE
        lockfile = self.workspace.lockfile()
        return set_lock(self.filename, lockfile, label, script)

    def test_no_breakpoints(self):
        self.lifecycle.requests = []
        config = {
            'breakpoints': [],
            'excbreakpoints': [],
        }
        with captured_stdio() as (stdout, _):
            with self.launched(config=config):
                # Allow the script to run to completion.
                time.sleep(1.)
        out = stdout.getvalue()

        for req, _ in self.lifecycle.requests:
            self.assertNotEqual(req['command'], 'setBreakpoints')
            self.assertNotEqual(req['command'], 'setExceptionBreakpoints')
        self.assertIn('2 4 4', out)
        self.assertIn('ka-boom', out)

    def test_breakpoints_single_file(self):
        done1, _ = self._set_lock('d')
        done2, script = self._set_lock('h')
        lineno = find_line(script, 'b')
        self.lifecycle.requests = []  # Trigger capture.
        config = {
            'breakpoints': [{
                'source': {
                    'path': self.filename
                },
                'breakpoints': [
                    {
                        'line': lineno
                    },
                ],
            }],
            'excbreakpoints': [],
        }
        with captured_stdio(out=True, err=True) as (stdout, stderr):
            #with self.wait_for_event('exited', timeout=3):
            with self.launched(config=config):
                with self.fix.hidden():
                    _, tid = self.get_threads(self.thread.name)
                with self.wait_for_event('stopped'):
                    done1()
                with self.wait_for_event('stopped'):
                    with self.wait_for_event('continued'):
                        req_continue1 = self.send_request(
                            'continue', {
                                'threadId': tid,
                            })
                with self.wait_for_event('stopped'):
                    with self.wait_for_event('continued'):
                        req_continue2 = self.send_request(
                            'continue', {
                                'threadId': tid,
                            })
                with self.wait_for_event('continued'):
                    req_continue_last = self.send_request(
                        'continue', {
                            'threadId': tid,
                        })

                # Allow the script to run to completion.
                received = self.vsc.received
                done2()
        out = stdout.getvalue()
        err = stderr.getvalue()

        got = []
        for req, resp in self.lifecycle.requests:
            if req['command'] == 'setBreakpoints':
                got.append(req['arguments'])
            self.assertNotEqual(req['command'], 'setExceptionBreakpoints')
        self.assertEqual(got, config['breakpoints'])

        self.assert_contains(received, [
            self.new_event('stopped',
                           reason='breakpoint',
                           threadId=tid,
                           text=None,
                           description=None,
                           ),
            self.new_response(req_continue1, allThreadsContinued=True),
            self.new_event('continued', threadId=tid),
            self.new_event('stopped',
                           reason='breakpoint',
                           threadId=tid,
                           text=None,
                           description=None,
                           ),
            self.new_response(req_continue2, allThreadsContinued=True),
            self.new_event('continued', threadId=tid),
            self.new_event('stopped',
                           reason='breakpoint',
                           threadId=tid,
                           text=None,
                           description=None,
                           ),
            self.new_response(req_continue_last, allThreadsContinued=True),
            self.new_event('continued', threadId=tid),
        ])
        self.assertIn('2 4 4', out)
        self.assertIn('ka-boom', err)

    def test_exception_breakpoints(self):
        self.vsc.PRINT_RECEIVED_MESSAGES = True
        done, script = self._set_lock('h')
        self.lifecycle.requests = []  # Trigger capture.
        config = {
            'breakpoints': [],
            'excbreakpoints': [
                {
                    'filters': ['raised']
                },
            ],
        }
        with captured_stdio() as (stdout, _):
            with self.launched(config=config):
                with self.fix.hidden():
                    _, tid = self.get_threads(self.thread.name)
                with self.wait_for_event('stopped'):
                    done()
                with self.wait_for_event('continued'):
                    req_continue_last = self.send_request('continue', {
                        'threadId': tid,
                    })
                # Allow the script to run to completion.
                received = self.vsc.received
        out = stdout.getvalue()

        got = []
        for req, resp in self.lifecycle.requests:
            if req['command'] == 'setExceptionBreakpoints':
                got.append(req['arguments'])
            self.assertNotEqual(req['command'], 'setBreakpoints')
        self.assertEqual(got, config['excbreakpoints'])
        self.assert_contains(received, [
            self.new_event('stopped', **dict(
                 reason='exception',
                 threadId=tid,
                 text='__main__.MyError',
                 description='ka-boom')),
            self.new_response(req_continue_last, allThreadsContinued=True),
            self.new_event('continued', **dict(threadId=tid, )),
        ])
        self.assertIn('2 4 4', out)
        self.assertIn('ka-boom', out)


@unittest.skip('Needs fixing when running with code coverage')
class UnicodeBreakpointTests(BreakpointTests):
    FILENAME = u'汉语a2.py'


class LogpointTests(TestBase, unittest.TestCase):
    FILENAME = 'spam.py'
    SOURCE = """
        a = 1
        b = 2
        c = 3
        d = 4
        """

    @contextlib.contextmanager
    def closing(self, exit=True):
        def handle_msg(msg, _):
            with self.wait_for_event('output'):
                self.req_disconnect = self.send_request('disconnect')

        with self.wait_for_event('terminated', handler=handle_msg):
            if exit:
                with self.wait_for_event('exited'):
                    yield
            else:
                yield

    @contextlib.contextmanager
    def running(self):
        addr = (None, 8888)
        with self.fake.start(addr):
            yield

    def test_basic(self):
        with open(self.filename) as scriptfile:
            script = scriptfile.read()
        donescript, wait = self.workspace.lockfile().wait_for_script()
        done, waitscript = self.workspace.lockfile().wait_in_script()
        with open(self.filename, 'w') as scriptfile:
            scriptfile.write(script + donescript + waitscript)
        addr = (None, 8888)
        with self.fake.start(addr):
            with self.vsc.wait_for_event('output'):
                pass

            with self.vsc.wait_for_event('initialized'):
                req_initialize = self.send_request('initialize', {
                    'adapterID': 'spam',
                })
                req_attach = self.send_request(
                    'attach', {'debugOptions': ['RedirectOutput']})
                req_breakpoints = self.send_request(
                    'setBreakpoints', {
                        'source': {
                            'path': self.filename
                        },
                        'breakpoints': [
                            {
                                'line': '4',
                                'logMessage': '{a}+{b}=3'
                            },
                        ],
                    })
            with self.vsc.wait_for_event('output'):  # 1+2=3
                with self.vsc.wait_for_event('thread'):
                    req_config = self.send_request('configurationDone')

            wait()
            received = self.vsc.received
            done()

            self.fix.binder.done(close=False)
            self.fix.binder.wait_until_done()
            with self.closing():
                self.fix.binder.ptvsd.close()

        self.assert_vsc_received(received, [
            self.new_event(
                'output',
                category='telemetry',
                output='ptvsd',
                data={'version': ptvsd.__version__}),
            self.new_response(req_initialize, **INITIALIZE_RESPONSE),
            self.new_event('initialized'),
            self.new_response(req_attach),
            self.new_response(
                req_breakpoints,
                **dict(breakpoints=[{
                    'id': 1,
                    'verified': True,
                    'line': '4'
                }])),
            self.new_response(req_config),
            self.new_event(
                'process',
                **dict(
                    name=sys.argv[0],
                    systemProcessId=os.getpid(),
                    isLocalProcess=True,
                    startMethod='attach',
                )),
            self.new_event('thread', reason='started', threadId=1),
            self.new_event(
                'output',
                **dict(
                    category='stdout',
                    output='1+2=3' + os.linesep,
                )),
        ])
