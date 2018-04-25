class _Contact(object):
    """ Encapsulation for remote contact

    This class contains information on a single remote contact, and also
    provides a direct RPC API to the remote node which it represents
    """

    def __init__(self, id, ipAddress, udpPort, networkProtocol, firstComm):
        self.id = id
        self.address = ipAddress
        self.port = udpPort
        self._networkProtocol = networkProtocol
        self.commTime = firstComm
        self.lastReplied = None
        self.failedRPCs = 0

    def __eq__(self, other):
        if isinstance(other, _Contact):
            return self.id == other.id
        elif isinstance(other, str):
            return self.id == other
        else:
            return False

    def __ne__(self, other):
        if isinstance(other, _Contact):
            return self.id != other.id
        elif isinstance(other, str):
            return self.id != other
        else:
            return True

    def compact_ip(self):
        compact_ip = reduce(
            lambda buff, x: buff + bytearray([int(x)]), self.address.split('.'), bytearray())
        return str(compact_ip)

    def __str__(self):
        return '<%s.%s object; IP address: %s, UDP port: %d>' % (
            self.__module__, self.__class__.__name__, self.address, self.port)

    def __getattr__(self, name):
        """ This override allows the host node to call a method of the remote
        node (i.e. this contact) as if it was a local function.

        For instance, if C{remoteNode} is a instance of C{Contact}, the
        following will result in C{remoteNode}'s C{test()} method to be
        called with argument C{123}::
         remoteNode.test(123)

        Such a RPC method call will return a Deferred, which will callback
        when the contact responds with the result (or an error occurs).
        This happens via this contact's C{_networkProtocol} object (i.e. the
        host Node's C{_protocol} object).
        """

        def _sendRPC(*args, **kwargs):
            return self._networkProtocol.sendRPC(self, name, args, **kwargs)

        return _sendRPC


class _ContactManager(object):  # don't make duplicate Contact objects, otherwise failures can't be counted
    def __init__(self):
        self._contacts = set()

    def get_contact(self, id, address, port):
        for contact in self._contacts:
            if contact.id == id and contact.address == address and contact.port == port:
                return contact

    def make_contact(self, id, ipAddress, udpPort, networkProtocol, firstComm=0, get_time=None):
        if not get_time:
            from twisted.internet import reactor
            get_time = reactor.seconds
        contact = self.get_contact(id, ipAddress, udpPort)
        if contact:
            return contact
        contact = _Contact(id, ipAddress, udpPort, networkProtocol, firstComm or get_time())
        self._contacts.add(contact)
        return contact


ContactManager = _ContactManager()
Contact = ContactManager.make_contact
