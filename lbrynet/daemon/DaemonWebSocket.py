from __future__ import print_function
import six
import json
import datetime
import logging

from autobahn.twisted.websocket import WebSocketServerProtocol, WebSocketServerFactory
from autobahn.twisted.util import sleep
from twisted.internet import defer, reactor, protocol

from torba.basenetwork import BaseNetwork as TorbaBaseNetwork
from torba.baseledger import BaseLedger as TorbaBaseLedger
from pprint import pprint

log = logging.getLogger(__name__)


class LbryServerProtocol(WebSocketServerProtocol):
    def __init__(self, daemon=None):
        super(LbryServerProtocol, self).__init__()

        self.request_id = 0
        self.lookup_table = {}
        self.daemon = daemon

    def onConnect(self, request):
        log.info("WebSocket Client connecting: %s", request.peer)

        for ledger in self.daemon.session.wallet.ledgers.values():
            ledger.on_transaction.listen(self.process_transaction)
            ledger.network.on_connected.listen(self.process_connection)
            ledger.network.on_header.listen(self.process_header)
            ledger.network.on_status.listen(self.process_status)

    @defer.inlineCallbacks
    def process_transaction(self, response):
        pprint('--------------------------------')
        pprint('websocket.process_transaction!')
        pprint(response)
        pprint('--------------------------------')

    @defer.inlineCallbacks
    def process_connection(self, response):
        pprint('--------------------------------')
        pprint('websocket.process_connection!')
        pprint(response)
        pprint('--------------------------------')

    @defer.inlineCallbacks
    def process_header(self, response):
        pprint('--------------------------------')
        pprint('websocket.process_header!')
        pprint(response)
        pprint('--------------------------------')

    @defer.inlineCallbacks
    def process_status(self, response):
        pprint('--------------------------------')
        pprint('websocket.process_status!')
        pprint(response)
        pprint('--------------------------------')

    @defer.inlineCallbacks
    def onOpen(self):
        log.info("WebSocket connection open.")

        self.send_wallet_balance()

        # start sending messages every second ..
        while True:
            now = datetime.datetime.now().strftime('%H:%M:%S')
            msg = u"Hello, world! It's {} o'clock!".format(now)
            message = json.dumps({'$event': 'message', 'data': msg})
            self.sendMessage(message.encode('utf8'), isBinary=False)
            yield sleep(3)

    def _handle_event(self, event):
        assert isinstance(event, lbry_event.Event)
        log.info("Event occurred: %s", event.name)
        message = json.dumps({'$event': event.name, 'data': event.data})
        self.sendMessage(message.encode('utf8'), isBinary=False)

    def send_wallet_balance(self):
        balance = self.daemon.session.wallet.get_balance()
        message = json.dumps({'$event': 'wallet_balance', 'data': {'balance': balance}})
        self.sendMessage(message.encode('utf8'), isBinary=False)

    def send_routing_table(self):
        routing_table = yield self.daemon.jsonrpc_routing_table_get()
        self.sendMessage(self.encodeMessage({'$event': 'routing_table', 'data': {'routing_table': routing_table}}))

    # @defer.inlineCallbacks
    def onMessage(self, payload, isBinary):
        if isBinary:
            log.info("Binary message received: %s bytes", len(payload))
        else:
            log.info("Text message received: %s", payload.decode('utf8'))

        if not isBinary and payload.decode('utf8') == 'wallet_balance_get':
            self.send_wallet_balance()
        if not isBinary and payload.decode('utf8') == 'routing_table_get':
            self.send_routing_table()
        else:
            ## echo back message verbatim
            self.sendMessage('I got your message: {}'.format(payload), isBinary)

    @staticmethod
    def encodeMessage(message):
        return json.dumps(message).encode('utf8')

    def onClose(self, wasClean, code, reason):
        log.info("WebSocket connection closed: %s", reason)


class LbryServerFactory(WebSocketServerFactory):
    def __init__(self, uri, daemon=None):
        super(LbryServerFactory, self).__init__(uri)
        self.protocol = None
        self.daemon = daemon

    def buildProtocol(self, addr):
        self.protocol = LbryServerProtocol(self.daemon)
        self.protocol.factory = self
        self.protocol.daemon = self.daemon
        return self.protocol






import socket
from itertools import cycle
from twisted.application.internet import ClientService, CancelledError
from twisted.internet.endpoints import clientFromString
from twisted.protocols.basic import LineOnlyReceiver

from torba import __version__
from torba.stream import StreamController


def unicode2bytes(string):
    if isinstance(string, six.text_type):
        return string.encode('iso-8859-1')
    elif isinstance(string, list):
        return [unicode2bytes(s) for s in string]
    return string


def bytes2unicode(maybe_bytes):
    if isinstance(maybe_bytes, bytes):
        return maybe_bytes.decode()
    elif isinstance(maybe_bytes, (list, tuple)):
        return [bytes2unicode(b) for b in maybe_bytes]
    return maybe_bytes


class StratumClientProtocol(LineOnlyReceiver):
    delimiter = b'\n'
    MAX_LENGTH = 100000

    def __init__(self):
        self.request_id = 0
        self.lookup_table = {}
        self.session = {}

        self.on_disconnected_controller = StreamController()
        self.on_disconnected = self.on_disconnected_controller.stream

    def _get_id(self):
        self.request_id += 1
        return self.request_id

    @property
    def _ip(self):
        return self.transport.getPeer().host

    def get_session(self):
        return self.session

    def connectionMade(self):
        try:
            self.transport.setTcpNoDelay(True)
            self.transport.setTcpKeepAlive(True)
            self.transport.socket.setsockopt(
                socket.SOL_TCP, socket.TCP_KEEPIDLE, 120
                # Seconds before sending keepalive probes
            )
            self.transport.socket.setsockopt(
                socket.SOL_TCP, socket.TCP_KEEPINTVL, 1
                # Interval in seconds between keepalive probes
            )
            self.transport.socket.setsockopt(
                socket.SOL_TCP, socket.TCP_KEEPCNT, 5
                # Failed keepalive probles before declaring other end dead
            )
        except Exception as err:
            # Supported only by the socket transport,
            # but there's really no better place in code to trigger this.
            log.warning("Error setting up socket: %s", err)

    def connectionLost(self, reason=None):
        self.on_disconnected_controller.add(True)

    def lineReceived(self, line):

        try:
            # `line` comes in as a byte string but `json.loads` automatically converts everything to
            # unicode. For keys it's not a big deal but for values there is an expectation
            # everywhere else in wallet code that most values are byte strings.
            message = json.loads(
                line, object_hook=lambda obj: {
                    k: unicode2bytes(v) for k, v in obj.items()
                }
            )
        except (ValueError, TypeError):
            raise ValueError("Cannot decode message '{}'".format(line.strip()))

        if message.get('id'):
            try:
                d = self.lookup_table.pop(message['id'])
                if message.get('error'):
                    d.errback(RuntimeError(*message['error']))
                else:
                    d.callback(message.get('result'))
            except KeyError:
                raise LookupError(
                    "Lookup for deferred object for message ID '{}' failed.".format(message['id']))
        elif message.get('method') in self.network.subscription_controllers:
            controller = self.network.subscription_controllers[message['method']]
            controller.add(message.get('params'))
        else:
            log.warning("Cannot handle message '%s'" % line)

    def rpc(self, method, *args):
        message_id = self._get_id()
        message = json.dumps({
            'id': message_id,
            'method': method,
            'params': [bytes2unicode(arg) for arg in args]
        })
        self.sendLine(message.encode('latin-1'))
        d = self.lookup_table[message_id] = defer.Deferred()
        return d


class StratumClientFactory(protocol.ClientFactory):

    protocol = StratumClientProtocol

    def __init__(self, network):
        self.network = network
        self.client = None

    def buildProtocol(self, addr):
        client = self.protocol()
        client.factory = self
        client.network = self.network
        self.client = client
        return client


class BaseNetwork:

    def __init__(self, config):
        self.config = config
        self.client = None
        self.service = None
        self.running = False

        self._on_connected_controller = StreamController()
        self.on_connected = self._on_connected_controller.stream

        self._on_header_controller = StreamController()
        self.on_header = self._on_header_controller.stream

        self._on_status_controller = StreamController()
        self.on_status = self._on_status_controller.stream

        self.subscription_controllers = {
            b'blockchain.headers.subscribe': self._on_header_controller,
            b'blockchain.address.subscribe': self._on_status_controller,
        }

    @defer.inlineCallbacks
    def start(self):
        for server in cycle(self.config['default_servers']):
            endpoint = clientFromString(reactor, 'tcp:{}:{}'.format(*server))
            self.service = ClientService(endpoint, StratumClientFactory(self))
            self.service.startService()
            try:
                self.client = yield self.service.whenConnected(failAfterFailures=2)
                yield self.ensure_server_version()
                self._on_connected_controller.add(True)
                yield self.client.on_disconnected.first
            except CancelledError:
                return
            except Exception as e:
                pass
            finally:
                self.client = None
            if not self.running:
                return

    def stop(self):
        self.running = False
        if self.service is not None:
            self.service.stopService()
        if self.is_connected:
            return self.client.on_disconnected.first
        else:
            return defer.succeed(True)

    @property
    def is_connected(self):
        return self.client is not None and self.client.connected

    def rpc(self, list_or_method, *args):
        if self.is_connected:
            return self.client.rpc(list_or_method, *args)
        else:
            raise ConnectionError("Attempting to send rpc request when connection is not available.")

    def ensure_server_version(self, required='1.2'):
        return self.rpc('server.version', __version__, required)

    def broadcast(self, raw_transaction):
        return self.rpc('blockchain.transaction.broadcast', raw_transaction)

    def get_history(self, address):
        return self.rpc('blockchain.address.get_history', address)

    def get_transaction(self, tx_hash):
        return self.rpc('blockchain.transaction.get', tx_hash)

    def get_merkle(self, tx_hash, height):
        return self.rpc('blockchain.transaction.get_merkle', tx_hash, height)

    def get_headers(self, height, count=10000):
        return self.rpc('blockchain.block.headers', height, count)

    def subscribe_headers(self):
        return self.rpc('blockchain.headers.subscribe', True)

    def subscribe_address(self, address):
        return self.rpc('blockchain.address.subscribe', address)


