import os

from twisted.internet import reactor
from twisted.protocols.policies import WrappingFactory
from twisted.python import log
from twisted.python.filepath import FilePath
from twisted.web.client import HTTPClientFactory
from twisted.web import server, static

from txaws.client.base import BaseQuery
from txaws.testing.base import TXAWSTestCase


class BaseQueryTestCase(TXAWSTestCase):

    def setUp(self):
        self.cleanupServerConnections = 0
        name = self.mktemp()
        os.mkdir(name)
        FilePath(name).child("file").setContent("0123456789")
        r = static.File(name)
        self.site = server.Site(r, timeout=None)
        self.wrapper = WrappingFactory(self.site)
        self.port = self._listen(self.wrapper)
        self.portno = self.port.getHost().port

    def tearDown(self):
        # If the test indicated it might leave some server-side connections
        # around, clean them up.
        connections = self.wrapper.protocols.keys()
        # If there are fewer server-side connections than requested,
        # that's okay.  Some might have noticed that the client closed
        # the connection and cleaned up after themselves.
        for n in range(min(len(connections), self.cleanupServerConnections)):
            proto = connections.pop()
            log.msg("Closing %r" % (proto,))
            proto.transport.loseConnection()
        if connections:                                                                                              
            log.msg("Some left-over connections; this test is probably buggy.")
        return self.port.stopListening()

    def _listen(self, site):
        return reactor.listenTCP(0, site, interface="127.0.0.1")

    def _get_url(self, path):
        return "http://127.0.0.1:%d/%s" % (self.portno, path)

    def test_creation(self):
        query = BaseQuery("an action", "creds", "http://endpoint")
        self.assertEquals(query.factory, HTTPClientFactory)
        self.assertEquals(query.action, "an action")
        self.assertEquals(query.creds, "creds")
        self.assertEquals(query.endpoint, "http://endpoint")

    def test_get_page(self):
        query = BaseQuery("an action", "creds", "http://endpoint")
        d = query.get_page(self._get_url("file"))
        d.addCallback(self.assertEquals, "0123456789")
        return d
