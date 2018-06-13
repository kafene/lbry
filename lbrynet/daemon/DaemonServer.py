import logging
import os

from twisted.web import server, guard, resource
from twisted.internet import defer, reactor, error
from twisted.cred import portal

from autobahn.twisted.websocket import WebSocketServerFactory

from lbrynet import conf
from lbrynet.daemon.Daemon import Daemon
from lbrynet.daemon.DaemonWebSocket import LbryServerProtocol, LbryServerFactory
from lbrynet.daemon.auth.auth import PasswordChecker, HttpPasswordRealm
from lbrynet.daemon.auth.util import initialize_api_key_file

log = logging.getLogger(__name__)


class IndexResource(resource.Resource):
    def getChild(self, name, request):
        request.setHeader('cache-control', 'no-cache, no-store, must-revalidate')
        request.setHeader('expires', '0')
        return self if name == '' else resource.Resource.getChild(self, name, request)


class DaemonServer(object):
    def __init__(self, analytics_manager=None):
        self._daemon = None
        self.root = None
        self.server_port = None
        self.analytics_manager = analytics_manager
        self.websocket_server = None

    def _setup_server(self, use_auth):
        self.root = IndexResource()
        self._daemon = Daemon(self.analytics_manager)
        self.root.putChild("", self._daemon)
        # TODO: DEPRECATED, remove this and just serve the API at the root
        self.root.putChild(conf.settings['API_ADDRESS'], self._daemon)

        lbrynet_server = get_site_base(use_auth, self.root)

        try:
            self.server_port = reactor.listenTCP(
                conf.settings['api_port'], lbrynet_server, interface=conf.settings['api_host'])
            log.info("lbrynet API listening on TCP %s:%i", conf.settings['api_host'], conf.settings['api_port'])
        except error.CannotListenError:
            log.info('Daemon already running, exiting app')
            raise

        return defer.succeed(True)

    def _setup_websocket_server(self):
        host, port = conf.settings['websocket_host'], conf.settings['websocket_port']

        factory = LbryServerFactory(u"ws://{}:{}".format(host, port), self._daemon)
        factory.protocol = LbryServerProtocol

        try:
            self.websocket_server = reactor.listenTCP(
                conf.settings['websocket_port'], factory,
                interface=conf.settings['websocket_host'])
            log.info("lbrynet WebSocket server listening on TCP 127.0.0.1:9000")
        except error.CannotListenError:
            log.info('WebSocket server already running, exiting app')
            raise

        return defer.succeed(True)

    @defer.inlineCallbacks
    def start(self, use_auth):
        yield self._setup_server(use_auth)
        yield self._setup_websocket_server()
        yield self._daemon.setup()

    def stop(self):
        if reactor.running:
            log.info("Stopping the reactor")
            reactor.fireSystemEvent("shutdown")


def get_site_base(use_auth, root):
    if use_auth:
        log.info("Using authenticated API")
        root = create_auth_session(root)
    else:
        log.info("Using non-authenticated API")
    return server.Site(root)


def create_auth_session(root):
    pw_path = os.path.join(conf.settings['data_dir'], ".api_keys")
    initialize_api_key_file(pw_path)
    checker = PasswordChecker.load_file(pw_path)
    realm = HttpPasswordRealm(root)
    portal_to_realm = portal.Portal(realm, [checker, ])
    factory = guard.BasicCredentialFactory('Login to lbrynet api')
    _lbrynet_server = guard.HTTPAuthSessionWrapper(portal_to_realm, [factory, ])
    return _lbrynet_server
