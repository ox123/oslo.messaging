
# Copyright 2013 Red Hat, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import threading

from oslo.config import cfg
import testscenarios

from oslo import messaging
from tests import utils as test_utils

load_tests = testscenarios.load_tests_apply_scenarios


class ServerSetupMixin(object):

    class Server(object):
        def __init__(self, transport, topic, server, endpoint, serializer):
            target = messaging.Target(topic=topic, server=server)
            self._server = messaging.get_rpc_server(transport,
                                                    target,
                                                    [endpoint, self],
                                                    serializer=serializer)

        def stop(self, ctxt):
            # Check start() does nothing with a running server
            self._server.start()
            self._server.stop()
            self._server.wait()

        def start(self):
            self._server.start()

    class TestSerializer(object):

        def serialize_entity(self, ctxt, entity):
            return 's' + (entity or '')

        def deserialize_entity(self, ctxt, entity):
            return 'd' + (entity or '')

    def __init__(self):
        self.serializer = self.TestSerializer()

    def _setup_server(self, transport, endpoint, topic=None, server=None):
        server = self.Server(transport,
                             topic=topic or 'testtopic',
                             server=server or 'testserver',
                             endpoint=endpoint,
                             serializer=self.serializer)

        thread = threading.Thread(target=server.start)
        thread.daemon = True
        thread.start()

        return thread

    def _stop_server(self, client, server_thread, topic=None):
        if topic is not None:
            client = client.prepare(topic=topic)
        client.cast({}, 'stop')
        server_thread.join(timeout=30)

    def _setup_client(self, transport, topic='testtopic'):
        return messaging.RPCClient(transport,
                                   messaging.Target(topic=topic),
                                   serializer=self.serializer)


class TestRPCServer(test_utils.BaseTestCase, ServerSetupMixin):

    def __init__(self, *args):
        super(TestRPCServer, self).__init__(*args)
        ServerSetupMixin.__init__(self)

    def setUp(self):
        super(TestRPCServer, self).setUp(conf=cfg.ConfigOpts())

    def test_constructor(self):
        transport = messaging.get_transport(self.conf, url='fake:')
        target = messaging.Target(topic='foo', server='bar')
        endpoints = [object()]
        serializer = object()

        server = messaging.get_rpc_server(transport, target, endpoints,
                                          serializer=serializer)

        self.assertTrue(server.conf is self.conf)
        self.assertTrue(server.transport is transport)
        self.assertTrue(server.target is target)
        self.assertTrue(isinstance(server.dispatcher, messaging.RPCDispatcher))
        self.assertTrue(server.dispatcher.endpoints is endpoints)
        self.assertTrue(server.dispatcher.serializer is serializer)
        self.assertTrue(server.executor is 'blocking')

    def test_no_target_server(self):
        transport = messaging.get_transport(self.conf, url='fake:')

        server = messaging.get_rpc_server(transport,
                                          messaging.Target(topic='testtopic'),
                                          [])
        try:
            server.start()
        except Exception as ex:
            self.assertTrue(isinstance(ex, messaging.ServerListenError), ex)
            self.assertEqual(ex.target.topic, 'testtopic')
        else:
            self.assertTrue(False)

    def test_no_server_topic(self):
        transport = messaging.get_transport(self.conf, url='fake:')
        target = messaging.Target(server='testserver')
        server = messaging.get_rpc_server(transport, target, [])
        try:
            server.start()
        except Exception as ex:
            self.assertTrue(isinstance(ex, messaging.ServerListenError), ex)
            self.assertEqual(ex.target.server, 'testserver')
        else:
            self.assertTrue(False)

    def _test_no_client_topic(self, call=True):
        transport = messaging.get_transport(self.conf, url='fake:')

        client = self._setup_client(transport, topic=None)

        method = client.call if call else client.cast

        try:
            method({}, 'ping', arg='foo')
        except Exception as ex:
            self.assertTrue(isinstance(ex, messaging.ClientSendError), ex)
            self.assertTrue(ex.target is not None)
        else:
            self.assertTrue(False)

    def test_no_client_topic_call(self):
        self._test_no_client_topic(call=True)

    def test_no_client_topic_cast(self):
        self._test_no_client_topic(call=False)

    def test_client_call_timeout(self):
        transport = messaging.get_transport(self.conf, url='fake:')

        finished = False
        wait = threading.Condition()

        class TestEndpoint(object):
            def ping(self, ctxt, arg):
                with wait:
                    if not finished:
                        wait.wait()

        server_thread = self._setup_server(transport, TestEndpoint())
        client = self._setup_client(transport)

        try:
            client.prepare(timeout=0).call({}, 'ping', arg='foo')
        except Exception as ex:
            self.assertTrue(isinstance(ex, messaging.MessagingTimeout), ex)
        else:
            self.assertTrue(False)

        with wait:
            finished = True
            wait.notify()

        self._stop_server(client, server_thread)

    def test_unknown_executor(self):
        transport = messaging.get_transport(self.conf, url='fake:')

        try:
            messaging.get_rpc_server(transport, None, [], executor='foo')
        except Exception as ex:
            self.assertTrue(isinstance(ex, messaging.ExecutorLoadFailure))
            self.assertEqual(ex.executor, 'foo')
        else:
            self.assertTrue(False)

    def test_cast(self):
        transport = messaging.get_transport(self.conf, url='fake:')

        class TestEndpoint(object):
            def __init__(self):
                self.pings = []

            def ping(self, ctxt, arg):
                self.pings.append(arg)

        endpoint = TestEndpoint()
        server_thread = self._setup_server(transport, endpoint)
        client = self._setup_client(transport)

        client.cast({}, 'ping', arg='foo')
        client.cast({}, 'ping', arg='bar')

        self._stop_server(client, server_thread)

        self.assertEqual(endpoint.pings, ['dsfoo', 'dsbar'])

    def test_call(self):
        transport = messaging.get_transport(self.conf, url='fake:')

        class TestEndpoint(object):
            def ping(self, ctxt, arg):
                return arg

        server_thread = self._setup_server(transport, TestEndpoint())
        client = self._setup_client(transport)

        self.assertEqual(client.call({}, 'ping', arg='foo'), 'dsdsfoo')

        self._stop_server(client, server_thread)

    def test_direct_call(self):
        transport = messaging.get_transport(self.conf, url='fake:')

        class TestEndpoint(object):
            def ping(self, ctxt, arg):
                return arg

        server_thread = self._setup_server(transport, TestEndpoint())
        client = self._setup_client(transport)

        direct = client.prepare(server='testserver')
        self.assertEqual(direct.call({}, 'ping', arg='foo'), 'dsdsfoo')

        self._stop_server(client, server_thread)

    def test_context(self):
        transport = messaging.get_transport(self.conf, url='fake:')

        class TestEndpoint(object):
            def ctxt_check(self, ctxt, key):
                return ctxt[key]

        server_thread = self._setup_server(transport, TestEndpoint())
        client = self._setup_client(transport)

        self.assertEqual(client.call({'dsa': 'b'},
                                     'ctxt_check',
                                     key='a'),
                         'dsb')

        self._stop_server(client, server_thread)


class TestMultipleServers(test_utils.BaseTestCase, ServerSetupMixin):

    _exchanges = [
        ('same_exchange', dict(exchange1=None, exchange2=None)),
        ('diff_exchange', dict(exchange1='x1', exchange2='x2')),
    ]

    _topics = [
        ('same_topic', dict(topic1='t', topic2='t')),
        ('diff_topic', dict(topic1='t1', topic2='t2')),
    ]

    _server = [
        ('same_server', dict(server1=None, server2=None)),
        ('diff_server', dict(server1='s1', server2='s2')),
    ]

    _fanout = [
        ('not_fanout', dict(fanout1=None, fanout2=None)),
        ('fanout', dict(fanout1=True, fanout2=True)),
    ]

    _method = [
        ('call', dict(call1=True, call2=True)),
        ('cast', dict(call1=False, call2=False)),
    ]

    _endpoints = [
        ('one_endpoint',
         dict(multi_endpoints=False,
              expect1=['ds1', 'ds2'],
              expect2=['ds1', 'ds2'])),
        ('two_endpoints',
         dict(multi_endpoints=True,
              expect1=['ds1'],
              expect2=['ds2'])),
    ]

    @classmethod
    def generate_scenarios(cls):
        cls.scenarios = testscenarios.multiply_scenarios(cls._exchanges,
                                                         cls._topics,
                                                         cls._server,
                                                         cls._fanout,
                                                         cls._method,
                                                         cls._endpoints)

        # fanout call not supported
        def filter_fanout_call(scenario):
            params = scenario[1]
            fanout = params['fanout1'] or params['fanout2']
            call = params['call1'] or params['call2']
            return not (call and fanout)

        # listening multiple times on same topic/server pair not supported
        def filter_same_topic_and_server(scenario):
            params = scenario[1]
            single_topic = params['topic1'] == params['topic2']
            single_server = params['server1'] == params['server2']
            return not (single_topic and single_server)

        # fanout to multiple servers on same topic and exchange
        # each endpoint will receive both messages
        def fanout_to_servers(scenario):
            params = scenario[1]
            fanout = params['fanout1'] or params['fanout2']
            single_exchange = params['exchange1'] == params['exchange2']
            single_topic = params['topic1'] == params['topic2']
            multi_servers = params['server1'] != params['server2']
            if fanout and single_exchange and single_topic and multi_servers:
                params['expect1'] = params['expect1'][:] + params['expect1']
                params['expect2'] = params['expect2'][:] + params['expect2']
            return scenario

        # multiple endpoints on same topic and exchange
        # either endpoint can get either message
        def single_topic_multi_endpoints(scenario):
            params = scenario[1]
            single_exchange = params['exchange1'] == params['exchange2']
            single_topic = params['topic1'] == params['topic2']
            if single_topic and single_exchange and params['multi_endpoints']:
                params['expect_either'] = (params['expect1'] +
                                           params['expect2'])
                params['expect1'] = params['expect2'] = []
            else:
                params['expect_either'] = []
            return scenario

        for f in [filter_fanout_call, filter_same_topic_and_server]:
            cls.scenarios = filter(f, cls.scenarios)
        for m in [fanout_to_servers, single_topic_multi_endpoints]:
            cls.scenarios = map(m, cls.scenarios)

    def __init__(self, *args):
        super(TestMultipleServers, self).__init__(*args)
        ServerSetupMixin.__init__(self)

    def setUp(self):
        super(TestMultipleServers, self).setUp(conf=cfg.ConfigOpts())

    def test_multiple_servers(self):
        url1 = 'fake:///' + (self.exchange1 or '')
        url2 = 'fake:///' + (self.exchange2 or '')

        transport1 = messaging.get_transport(self.conf, url=url1)
        if url1 != url2:
            transport2 = messaging.get_transport(self.conf, url=url1)
        else:
            transport2 = transport1

        class TestEndpoint(object):
            def __init__(self):
                self.pings = []

            def ping(self, ctxt, arg):
                self.pings.append(arg)

            def alive(self, ctxt):
                return 'alive'

        if self.multi_endpoints:
            endpoint1, endpoint2 = TestEndpoint(), TestEndpoint()
        else:
            endpoint1 = endpoint2 = TestEndpoint()

        thread1 = self._setup_server(transport1, endpoint1,
                                     topic=self.topic1, server=self.server1)
        thread2 = self._setup_server(transport2, endpoint2,
                                     topic=self.topic2, server=self.server2)

        client1 = self._setup_client(transport1, topic=self.topic1)
        client2 = self._setup_client(transport2, topic=self.topic2)

        client1 = client1.prepare(server=self.server1)
        client2 = client2.prepare(server=self.server2)

        if self.fanout1:
            client1.call({}, 'alive')
            client1 = client1.prepare(fanout=True)
        if self.fanout2:
            client2.call({}, 'alive')
            client2 = client2.prepare(fanout=True)

        (client1.call if self.call1 else client1.cast)({}, 'ping', arg='1')
        (client2.call if self.call2 else client2.cast)({}, 'ping', arg='2')

        self.assertTrue(thread1.isAlive())
        self._stop_server(client1.prepare(fanout=None),
                          thread1, topic=self.topic1)
        self.assertTrue(thread2.isAlive())
        self._stop_server(client2.prepare(fanout=None),
                          thread2, topic=self.topic2)

        def check(pings, expect):
            self.assertEqual(len(pings), len(expect))
            for a in expect:
                self.assertTrue(a in pings)

        if self.expect_either:
            check(endpoint1.pings + endpoint2.pings, self.expect_either)
        else:
            check(endpoint1.pings, self.expect1)
            check(endpoint2.pings, self.expect2)


TestMultipleServers.generate_scenarios()