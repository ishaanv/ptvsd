import unittest

from _pydevd_bundle.pydevd_comm import (
    CMD_LIST_THREADS,
    CMD_VERSION,
)

from . import OS_ID, HighlevelTestCase


# TODO: Make sure we are handling all args properly and sending the
# correct response/event bpdies.


class RequestTests(HighlevelTestCase):
    """
    lifecycle (in order), tested via test_lifecycle.py:

    initialize
    attach
    launch
    (setBreakpoints)
    (setExceptionBreakpoints)
    configurationDone
    (normal ops)
    disconnect

    Note that setFunctionBreakpoints may also be sent during
    configuration, but we do not support function breakpoints.

    normal operation (supported-only):

    threads
    stackTrace
    scopes
    variables
    setVariable
    evaluate
    pause
    continue
    next
    stepIn
    stepOut
    setBreakpoints
    setExceptionBreakpoints
    exceptionInfo
    """

    @unittest.skip('tested via test_lifecycle.py')
    def test_initialize(self):
        vsc, pydevd = self.new_fake()

        with vsc.start(None, 8888):
            try:
                pydevd.add_pending_response(CMD_VERSION, pydevd.VERSION)
                req = self.new_request('initialize',
                    adapterID='spam',
                )  # noqa
                with vsc.wait_for_response(req):
                    vsc.send_request(req)
            finally:
                self.disconnect(vsc)
                vsc._received.pop(-1)

        self.assertFalse(pydevd.failures)
        self.assertFalse(vsc.failures)
        vsc.assert_received(self, [
            self.new_response(0, req,
                supportsExceptionInfoRequest=True,
                supportsConfigurationDoneRequest=True,
                supportsConditionalBreakpoints=True,
                supportsSetVariable=True,
                supportsExceptionOptions=True,
                exceptionBreakpointFilters=[
                    {
                        'filter': 'raised',
                        'label': 'Raised Exceptions',
                        'default': 'true'
                    },
                    {
                        'filter': 'uncaught',
                        'label': 'Uncaught Exceptions',
                        'default': 'true'
                    },
                ],
            ),  # noqa
            self.new_event(1, 'initialized'),
        ])
        seq = 1000000000
        text = '\t'.join(['1.1', OS_ID, 'ID'])
        pydevd.assert_received(self, [
            (CMD_VERSION, seq, text),
        ])

    def test_threads(self):
        vsc, pydevd = self.new_fake()
        with self.launched(vsc, pydevd):
            pydevd.add_pending_response(CMD_LIST_THREADS, """
                <xml>
                <thread name="spam" id="10" />
                <thread name="pydevd.spam" id="11" />
                <thread name="" id="12" />
                </xml>
            """.strip().replace('\n', ''))
            req = self.new_request('threads')
            with vsc.wait_for_response(req):
                vsc.send_request(req)

        self.maxDiff = None
        self.assertFalse(pydevd.failures)
        self.assertFalse(vsc.failures)
        vsc.assert_received(self, [
            self.new_response(5, req,
                threads=[
                    {'id': 1, 'name': 'spam'},
                    {'id': 3, 'name': ''},
                ],
            ),  # noqa
        ])
        pydevd.assert_received(self, [
            # (cmdid, seq, text)
            (CMD_LIST_THREADS, 1000000002, ''),
        ])