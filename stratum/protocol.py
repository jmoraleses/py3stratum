import json
#import jsonical
import time
import socket

from twisted.protocols.basic import NetstringReceiver,LineOnlyReceiver
from twisted.internet import defer, reactor, error
from twisted.python.failure import Failure

#import services
from . import stats
from . import signature
from . import custom_exceptions
from . import connection_registry
from . import settings

from . import logger
log = logger.get_logger('protocol')

class RequestCounter(object):
    def __init__(self):
        self.on_finish = defer.Deferred()
        self.counter = 0
        
    def set_count(self, cnt):
        self.counter = cnt
        
    def decrease(self):
        self.counter -= 1
        if self.counter <= 0:
            self.finish()

    def finish(self):
        if not self.on_finish.called:
            self.on_finish.callback(True)
                
class Protocol(LineOnlyReceiver):
    delimiter = '\n'
    
    def _get_id(self):
        self.request_id += 1
        return self.request_id

    def _get_ip(self):
        return self.proxied_ip or self.transport.getPeer().host

    def get_ident(self):
        # Get global unique ID of connection
        return "%s:%s" % (self.proxied_ip or self.transport.getPeer().host, "%x" % id(self))
    
    def get_session(self):
        return self.session
        
    def connectionMade(self):
        try:
            self.transport.setTcpNoDelay(True)
            self.transport.setTcpKeepAlive(True)
            self.transport.socket.setsockopt(socket.SOL_TCP, socket.TCP_KEEPIDLE, 120) # Seconds before sending keepalive probes
            self.transport.socket.setsockopt(socket.SOL_TCP, socket.TCP_KEEPINTVL, 1) # Interval in seconds between keepalive probes
            self.transport.socket.setsockopt(socket.SOL_TCP, socket.TCP_KEEPCNT, 5) # Failed keepalive probles before declaring other end dead
        except:
            # Supported only by the socket transport,
            # but there's really no better place in code to trigger this.
            pass

        # Read settings.TCP_PROXY_PROTOCOL documentation
        self.expect_tcp_proxy_protocol_header = self.factory.__dict__.get('tcp_proxy_protocol_enable', False)
        self.proxied_ip = None # IP obtained from TCP proxy protocol
        
        self.request_id = 0    
        self.lookup_table = {}
        self.event_handler = self.factory.event_handler()
        self.on_disconnect = defer.Deferred()
        self.on_finish = None # Will point to defer which is called
                        # once all client requests are processed
        
        # Initiate connection session
        self.session = {}
        
        stats.PeerStats.client_connected(self._get_ip())
        log.debug("Connected %s" % self.transport.getPeer().host)
        connection_registry.ConnectionRegistry.add_connection(self)
    
    def transport_write(self, data):
        '''Overwrite this if transport needs some extra care about data written
        to the socket, like adding message format in websocket.''' 
        try:
            self.transport.write(data.encode('utf-8'))
        #except AttributeError:
            # Transport is disconnected
        #    pass
        except Exception as Err:
            print(Err)
        
    def connectionLost(self, reason):
        if self.on_disconnect != None and not self.on_disconnect.called:
            self.on_disconnect.callback(self)
            self.on_disconnect = None
 
        stats.PeerStats.client_disconnected(self._get_ip())
        connection_registry.ConnectionRegistry.remove_connection(self)
        self.transport = None # Fixes memory leak (cyclic reference)
 
    def writeJsonRequest(self, method, params, is_notification=False):
        request_id = None if is_notification else self._get_id()
        print(str(request_id),str(method), str(params))
        data = params
        if isinstance(data, (tuple)):
            new_data = []
            for key in data:
                skey = False
                
                #print(type(key), key)
                if isinstance(key, (list)):
                    sub_data = []
                    for sub_key in key:
                        #print('subkey',type(sub_key), sub_key)
                        skey = True
                        if isinstance(sub_key, (bytes)):
                            str_sub_key = sub_key.decode('utf-8', 'replace')
                            #str_sub_key = str(sub_key).replace("b'",'$').replace("'",'@')
                            #str_sub_key = sub_key.decode('utf-8', 'replace')
                        else:
                            str_sub_key = sub_key
                        
                        sub_data.append(str_sub_key)
                if skey:
                    str_key = sub_data
                else:
                    if isinstance(key, (bytes)):
                        str_key = key.decode('utf-8', 'replace')
                        #str_key = str(key).replace("b'",'$').replace("'",'@')
                        #str_key = key.decode('utf-8', 'replace')
                    else:
                        str_key = key

                new_data.append(str_key)

            data = new_data

        params = data
        print(params)

        serialized = json.dumps({'id': request_id, 'method': method, 'params': params})

        #serialized = str(serialized).replace("$","b'").replace("@","'") + "\n"
        #serialized = str(serialized).replace("$","").replace("@","") + "\n"

        if self.factory.debug:
            log.debug("< %s" % serialized)
        print('serialized:',serialized)
        print('serialized.encoded:',serialized)
        self.transport_write("%s\n" % serialized)
        return request_id
        
    def writeJsonResponse(self, data, message_id, use_signature=False, sign_method='', sign_params=[]):
        print('writeJsonResponse_1')
        print(data)

        if isinstance(data, tuple):
            new_data = []
            for key in data:
                if isinstance(key, (bytes)):
                    key = key.decode('utf-8', 'replace')
                    #key = str(key)
                    #key = str(key).replace("b'",'$').replace("'",'@')
                new_data.append(key)
            data = new_data
            #data = str(data).replace("b'",'').replace("'",'')
        print(data)
        print('writeJsonResponse_2')
        if use_signature:
            print('writeJsonResponse_3')
            serialized = signature.jsonrpc_dumps_sign(self.factory.signing_key, self.factory.signing_id, False,\
                message_id, sign_method, sign_params, data, None)
        else:
            print('writeJsonResponse_4')
            serialized = json.dumps({'id': message_id, 'result': data, 'error': None})
            #serialized = {'id': message_id, 'result': data, 'error': None}

            #serialized = str(serialized).replace("$","b'").replace("@","'") + "\n"
            #serialized = str(serialized).replace("$","").replace("@","") + "\n"

        print('writeJsonResponse_5')
        if self.factory.debug:
            print('writeJsonResponse_6')
            log.debug("< %s" % serialized)
        print('serialized:',serialized)
        print('writeJsonResponse_7')
        print('serialized.encoded:',serialized)
        self.transport_write("%s\n" % serialized)

    def writeJsonError(self, code, message, traceback, message_id, use_signature=False, sign_method='', sign_params=[]):       
        if use_signature:
            serialized = signature.jsonrpc_dumps_sign(self.factory.signing_key, self.factory.signing_id, False,\
                message_id, sign_method, sign_params, None, (code, message, traceback))
        else:
            serialized = json.dumps({'id': message_id, 'result': None, 'error': (code, message, traceback)})
        
        self.transport_write("%s\n" % serialized)

    def writeGeneralError(self, message, code=-1):
        log.error(message)
        return self.writeJsonError(code, message, None, None)
            
    def process_response(self, data, message_id, sign_method, sign_params, request_counter):
        print('Result received: {}'.format(data))
        self.writeJsonResponse(data.result, message_id, data.sign, sign_method, sign_params)
        request_counter.decrease()
        
            
    def process_failure(self, failure, message_id, sign_method, sign_params, request_counter):
        print('lofasz2')
        print(failure.value)
        if not isinstance(failure.value, custom_exceptions.ServiceException):
            print('lofasz3')
            # All handled exceptions should inherit from ServiceException class.
            # Throwing other exception class means that it is unhandled error
            # and we should log it.
            log.exception(failure)
            print('lofasz4')
        print('lofasz5')    
        sign = False
        code = getattr(failure.value, 'code', -1)
        print('lofasz6')
        #if isinstance(failure.value, services.ResultObject):
        #    # Strip ResultObject
        #    sign = failure.value.sign
        #    failure.value = failure.value.result
        
        if message_id != None:
            # Other party doesn't care of error state for notifications
            if settings.DEBUG:
                tb = failure.getBriefTraceback()
            else:
                tb = None
            self.writeJsonError(code, failure.getErrorMessage(), tb, message_id, sign, sign_method, sign_params)
                
        request_counter.decrease()
        
    def dataReceived(self, data, request_counter=None):
        '''Original code from Twisted, hacked for request_counter proxying.
        request_counter is hack for HTTP transport, didn't found cleaner solution how
        to indicate end of request processing in asynchronous manner.
        
        TODO: This would deserve some unit test to be sure that future twisted versions
        will work nicely with this.'''
        from struct import pack
        print('data:',data)

        if request_counter == None:
            request_counter = RequestCounter()
            
        lines  = (self._buffer+data).split(b'\n')
        self._buffer = lines.pop(-1)
        request_counter.set_count(len(lines))
        self.on_finish = request_counter.on_finish
        
        for line in lines:
            print(line)
            if self.transport.disconnecting:
                request_counter.finish()
                return
            if len(line) > self.MAX_LENGTH:
                request_counter.finish()
                return self.lineLengthExceeded(line)
            else:
                try:
                    self.lineReceived(line, request_counter)
                except Exception as exc:
                    request_counter.finish()
                    #log.exception("Processing of message failed")
                    log.warning("Failed message: %s from %s" % (str(exc), self._get_ip()))
                    return error.ConnectionLost('Processing of message failed')
                    
        if len(self._buffer) > self.MAX_LENGTH:
            request_counter.finish()
            return self.lineLengthExceeded(self._buffer)        
        
    def lineReceived(self, line, request_counter):
        print(line)
        if self.expect_tcp_proxy_protocol_header:
            # This flag may be set only for TCP transport AND when TCP_PROXY_PROTOCOL
            # is enabled in server config. Then we expect the first line of the stream
            # may contain proxy metadata.

            # We don't expect this header during this session anymore
            self.expect_tcp_proxy_protocol_header = False
            
            if line.startswith('PROXY'):
                self.proxied_ip = line.split()[2]

                # Let's process next line
                request_counter.decrease()
                return
            
        try:
            message = json.loads(line)
        except:
            #self.writeGeneralError("Cannot decode message '%s'" % line)
            request_counter.finish()
            raise custom_exceptions.ProtocolException("Cannot decode message '%s'" % line.strip())
        
        if self.factory.debug:
            log.debug("> %s" % message)
        
        msg_id = message.get('id', 0)
        msg_method = message.get('method')
        msg_params = message.get('params')
        msg_result = message.get('result')
        msg_error = message.get('error')
                                
        if msg_method:
            # It's a RPC call or notification
            try:
                result = self.event_handler._handle_event(msg_method, msg_params, connection_ref=self)
                if result == None and msg_id != None:
                    # event handler must return Deferred object or raise an exception for RPC request
                    raise custom_exceptions.MethodNotFoundException("Event handler cannot process method '%s'" % msg_method)
            except Exception as exc:
                print(exc)
                failure = Failure()
                self.process_failure(failure, msg_id, msg_method, msg_params, request_counter)
            else:
                   
                if msg_id == None:
                    # It's notification, don't expect the response
                    request_counter.decrease()
                else:
                    # It's a RPC call
                    print(result)
                    result.addCallback(self.process_response, msg_id, msg_method, msg_params, request_counter)
                    print('lofasz1')
                    result.addErrback(self.process_failure, msg_id, msg_method, msg_params, request_counter)
                    print('lofasz_99')
                    
                 
            
        elif msg_id:
            # It's a RPC response
            # Perform lookup to the table of waiting requests.
            request_counter.decrease()
           
            try:
                meta = self.lookup_table[msg_id]
                del self.lookup_table[msg_id]
            except KeyError:
                # When deferred object for given message ID isn't found, it's an error
                raise custom_exceptions.ProtocolException("Lookup for deferred object for message ID '%s' failed." % msg_id)  
            # If there's an error, handle it as errback
            # If both result and error are null, handle it as a success with blank result
            if msg_error != None:
                meta['defer'].errback(custom_exceptions.RemoteServiceException(msg_error[0], msg_error[1], msg_error[2]))
            else:
                meta['defer'].callback(msg_result) 
        else:
            request_counter.decrease()
            raise custom_exceptions.ProtocolException("Cannot handle message '%s'" % line)
          
    def rpc(self, method, params, is_notification=False):
        '''
            This method performs remote RPC call.

            If method should expect an response, it store
            request ID to lookup table and wait for corresponding
            response message.
        ''' 

        request_id = self.writeJsonRequest(method, params, is_notification)

        if is_notification:
            return

        d = defer.Deferred()
        self.lookup_table[request_id] = {'defer': d, 'method': method, 'params': params}
        return d
                    
class ClientProtocol(Protocol):
    def connectionMade(self):
        Protocol.connectionMade(self)
        self.factory.client = self
                
        if self.factory.timeout_handler:
            self.factory.timeout_handler.cancel()
            self.factory.timeout_handler = None

        if isinstance(getattr(self.factory, 'after_connect', None), list):
            log.debug("Resuming connection: %s" % self.factory.after_connect)
            for cmd in self.factory.after_connect:
                self.rpc(cmd[0], cmd[1])
            
        if not self.factory.on_connect.called:
            d = self.factory.on_connect 
            self.factory.on_connect = defer.Deferred()
            d.callback(self.factory)
            
            
        #d = self.rpc('node.get_peers', [])
        #d.addCallback(self.factory.add_peers)
                
    def connectionLost(self, reason):
        self.factory.client = None

        if self.factory.timeout_handler:
            self.factory.timeout_handler.cancel()
            self.factory.timeout_handler = None
        
        if not self.factory.on_disconnect.called:
            d = self.factory.on_disconnect
            self.factory.on_disconnect = defer.Deferred()
            d.callback(self.factory)
            
        Protocol.connectionLost(self, reason)
