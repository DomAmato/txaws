# Copyright (C) 2009 Robert Collins <robertc@robertcollins.net>
# Licenced under the txaws licence available at /LICENSE in the txaws source.

import os

from twisted.internet import reactor
from twisted.internet.defer import succeed
from twisted.python.filepath import FilePath
from twisted.web import server, static, util
from twisted.web.client import HTTPPageGetter, HTTPClientFactory
from twisted.protocols.policies import WrappingFactory
from twisted.test.proto_helpers import StringTransport

from txaws.tests import TXAWSTestCase
from txaws.credentials import AWSCredentials
from txaws.ec2 import client

from txaws.tests import TXAWSTestCase
from txaws.ec2.exception import EC2Error
from txaws.ec2.tests.payload import (
    sample_describe_instances_result, sample_terminate_instances_result,
    sample_ec2_error_message)


class FakeHTTPPageGetter(HTTPPageGetter):

    transport = StringTransport

    def connectionLost(self, reason):
        HTTPPageGetter.connectionLost(self, reason)
        #import pdb;pdb.set_trace()


class FakeHTTPFactory(HTTPClientFactory):

    protocol = FakeHTTPPageGetter
    test_payload = ""

    def connectionMade(self):
        content_length = len(self.test_payload)
        self.dataReceived(
            "GET /dummy HTTP/1.0\r\nHost: example.net\r\n"
            "Content-Length: %s\r\n\r\n%s" % (
                content_length, self.test_payload))


class FactoryWrapper(object):

    def __init__(self, payload):
        self.payload = payload

    def __call__(self, url, *args, **kwds):
        FakeHTTPFactory.test_payload = self.payload
        return FakeHTTPFactory(url, *args, **kwds)


class ReservationTestCase(TXAWSTestCase):

    def test_reservation_creation(self):
        reservation = client.Reservation(
            "id1", "owner", groups=["one", "two"])
        self.assertEquals(reservation.reservation_id, "id1")
        self.assertEquals(reservation.owner_id, "owner")
        self.assertEquals(reservation.groups, ["one", "two"])


class TestEC2Client(TXAWSTestCase):

    def test_init_no_creds(self):
        os.environ['AWS_SECRET_ACCESS_KEY'] = 'foo'
        os.environ['AWS_ACCESS_KEY_ID'] = 'bar'
        ec2 = client.EC2Client()
        self.assertNotEqual(None, ec2.creds)

    def test_init_no_creds_non_available_errors(self):
        self.assertRaises(ValueError, client.EC2Client)

    def test_init_explicit_creds(self):
        creds = 'foo'
        ec2 = client.EC2Client(creds=creds)
        self.assertEqual(creds, ec2.creds)

    def check_parsed_instances(self, results):
        instance = results[0]
        self.assertEquals(instance.instance_id, "i-abcdef01")
        self.assertEquals(instance.instance_state, "running")
        reservation = instance.reservation
        self.assertEquals(reservation.reservation_id, "r-cf24b1a6")
        self.assertEquals(reservation.owner_id, "123456789012")
        group = reservation.groups[0]
        self.assertEquals(group, "default")

    def test_parse_reservation(self):
        ec2 = client.EC2Client(creds='foo')
        results = ec2._parse_instances(sample_describe_instances_result)
        self.check_parsed_instances(results)

    def test_describe_instances(self):
        class StubQuery(object):
            def __init__(stub, action, creds):
                self.assertEqual(action, 'DescribeInstances')
                self.assertEqual('foo', creds)
            def submit(self):
                return succeed(sample_describe_instances_result)
        ec2 = client.EC2Client(creds='foo', query_factory=StubQuery)
        d = ec2.describe_instances()

        def check_instances(reservation):
            self.assertEqual(1, len(reservation))
            self.assertEqual('i-abcdef01', reservation[0].instance_id)
            self.assertEqual('running', reservation[0].instance_state)
        d.addCallback(self.check_parsed_instances)
        return d

    def test_terminate_instances(self):
        class StubQuery(object):
            def __init__(stub, action, creds, other_params):
                self.assertEqual(action, 'TerminateInstances')
                self.assertEqual('foo', creds)
                self.assertEqual(
                    {'InstanceId.1': 'i-1234', 'InstanceId.2': 'i-5678'},
                    other_params)
            def submit(self):
                return succeed(sample_terminate_instances_result)
        ec2 = client.EC2Client(creds='foo', query_factory=StubQuery)
        d = ec2.terminate_instances('i-1234', 'i-5678')
        def check_transition(changes):
            self.assertEqual([('i-1234', 'running', 'shutting-down'),
                ('i-5678', 'shutting-down', 'shutting-down')], sorted(changes))
        return d


class TestQuery(TXAWSTestCase):

    def setUp(self):
        TXAWSTestCase.setUp(self)
        self.creds = AWSCredentials('foo', 'bar')
        self.twisted_client_test_setup()
        self.cleanupServerConnections = 0

    def tearDown(self):
        """Copied from twisted.web.test.test_webclient."""
        # If the test indicated it might leave some server-side connections
        # around, clean them up.
        connections = self.wrapper.protocols.keys()
        # If there are fewer server-side connections than requested,
        # that's okay.  Some might have noticed that the client closed                                               
        # the connection and cleaned up after themselves.
        for n in range(min(len(connections), self.cleanupServerConnections)):
            proto = connections.pop()
            msg("Closing %r" % (proto,))
            proto.transport.loseConnection()
        if connections:
            msg("Some left-over connections; this test is probably buggy.")
        return self.port.stopListening()

    def _listen(self, site):
        return reactor.listenTCP(0, site, interface="127.0.0.1")

    def twisted_client_test_setup(self):
        name = self.mktemp()
        os.mkdir(name)
        FilePath(name).child("file").setContent("0123456789")
        resource = static.File(name)
        resource.putChild("redirect", util.Redirect("/file"))
        self.site = server.Site(resource, timeout=None)
        self.wrapper = WrappingFactory(self.site)
        self.port = self._listen(self.wrapper)
        self.portno = self.port.getHost().port


    def get_url(self, path):
        return "http://127.0.0.1:%d/%s" % (self.portno, path)

    def test_init_minimum(self):
        query = client.Query('DescribeInstances', self.creds)
        self.assertTrue('Timestamp' in query.params)
        del query.params['Timestamp']
        self.assertEqual(
            {'AWSAccessKeyId': 'foo',
             'Action': 'DescribeInstances',
             'SignatureMethod': 'HmacSHA1',
             'SignatureVersion': '2',
             'Version': '2008-12-01'},
            query.params)

    def test_init_requires_action(self):
        self.assertRaises(TypeError, client.Query)

    def test_init_requires_creds(self):
        self.assertRaises(TypeError, client.Query, None)

    def test_init_other_args_are_params(self):
        query = client.Query('DescribeInstances', self.creds,
            {'InstanceId.0': '12345'},
            time_tuple=(2007,11,12,13,14,15,0,0,0))
        self.assertEqual(
            {'AWSAccessKeyId': 'foo',
             'Action': 'DescribeInstances',
             'InstanceId.0': '12345',
             'SignatureMethod': 'HmacSHA1',
             'SignatureVersion': '2',
             'Timestamp': '2007-11-12T13:14:15Z',
             'Version': '2008-12-01'},
            query.params)

    def test_sorted_params(self):
        query = client.Query('DescribeInstances', self.creds,
            {'fun': 'games'},
            time_tuple=(2007,11,12,13,14,15,0,0,0))
        self.assertEqual([
            ('AWSAccessKeyId', 'foo'),
            ('Action', 'DescribeInstances'),
            ('SignatureMethod', 'HmacSHA1'),
            ('SignatureVersion', '2'),
            ('Timestamp', '2007-11-12T13:14:15Z'),
            ('Version', '2008-12-01'),
            ('fun', 'games'),
            ], query.sorted_params())

    def test_encode_unreserved(self):
        all_unreserved = ('ABCDEFGHIJKLMNOPQRSTUVWXYZ'
            'abcdefghijklmnopqrstuvwxyz0123456789-_.~')
        query = client.Query('DescribeInstances', self.creds)
        self.assertEqual(all_unreserved, query.encode(all_unreserved))

    def test_encode_space(self):
        """This may be just 'url encode', but the AWS manual isn't clear."""
        query = client.Query('DescribeInstances', self.creds)
        self.assertEqual('a%20space', query.encode('a space'))

    def test_canonical_query(self):
        query = client.Query('DescribeInstances', self.creds,
            {'fu n': 'g/ames', 'argwithnovalue':'',
             'InstanceId.1': 'i-1234'},
            time_tuple=(2007,11,12,13,14,15,0,0,0))
        expected_query = ('AWSAccessKeyId=foo&Action=DescribeInstances'
            '&InstanceId.1=i-1234'
            '&SignatureMethod=HmacSHA1&SignatureVersion=2&'
            'Timestamp=2007-11-12T13%3A14%3A15Z&Version=2008-12-01&'
            'argwithnovalue=&fu%20n=g%2Fames')
        self.assertEqual(expected_query, query.canonical_query_params())

    def test_signing_text(self):
        query = client.Query('DescribeInstances', self.creds,
            time_tuple=(2007,11,12,13,14,15,0,0,0))
        signing_text = ('GET\nec2.amazonaws.com\n/\n'
            'AWSAccessKeyId=foo&Action=DescribeInstances&'
            'SignatureMethod=HmacSHA1&SignatureVersion=2&'
            'Timestamp=2007-11-12T13%3A14%3A15Z&Version=2008-12-01')
        self.assertEqual(signing_text, query.signing_text())

    def test_sign(self):
        query = client.Query('DescribeInstances', self.creds,
            time_tuple=(2007,11,12,13,14,15,0,0,0))
        query.sign()
        self.assertEqual('4hEtLuZo9i6kuG3TOXvRQNOrE/U=',
            query.params['Signature'])

    def test_get_page(self):
        """Copied from twisted.web.test.test_webclient."""
        factory_wrapper = FactoryWrapper(sample_ec2_error_message)
        query = client.Query(
            'DummyQuery', self.creds, time_tuple=(2009,8,17,13,14,15,0,0,0),
            factory=factory_wrapper)
        deferred = query.get_page(self.get_url("file"))
        deferred.addCallback(self.assertEquals, "0123456789")
        return deferred

    def test_submit_400_raise_error(self):
        """A 4xx response status from EC2 should raise a txAWS EC2Error."""
        factory_wrapper = FactoryWrapper(sample_ec2_error_message)

        def _checkError(x):
            import pdb;pdb.set_trace()

        query = client.Query(
            'BadQuery', self.creds, time_tuple=(2009,8,15,13,14,15,0,0,0),
            factory=factory_wrapper)
        return self.assertFailure(query.submit(), EC2Error)

    def skip_test_submit_400_check_payload_and_status(self):
        """
        """
        factory_wrapper = FactoryWrapper(sample_ec2_error_message)

        def _checkError(error):
            error_data = error.value.errors[0]
            self.assertEquals(error_data["Code"], "FakeRequestCode")
            self.assertEquals(error_data["Message"],
                              "Request has fakely erred.")

        query = client.Query(
            'BadQuery', self.creds, time_tuple=(2009,8,15,13,14,15,0,0,0),
            factory=factory_wrapper)
        deferred = query.submit()
        deferred.addErrback(_checkError)
        return deferred

    def test_submit_500(self):
        """
        A 5xx response status from EC2 should raise the original Twisted
        exception.
        """
