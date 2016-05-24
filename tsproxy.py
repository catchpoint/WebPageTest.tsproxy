#!/usr/bin/python
"""
Copyright 2016 Google Inc. All Rights Reserved.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import asyncore
import signal
import socket
import sys

server = None
in_pipe = None
out_pipe = None
connections = {}

########################################################################################################################
#   Traffic-shaping pipe (just passthrough for now)
########################################################################################################################
class TSPipe():
  PIPE_IN = 0
  PIPE_OUT = 1

  def __init__(self, direction):
    self.direction = direction

  def SendMessage(self, message):
    global connections
    connection_id = message['connection']
    if connection_id in connections:
      peer = 'server'
      if self.direction == self.PIPE_IN:
        peer = 'client'
      if peer in connections[connection_id]:
        try:
          connections[connection_id][peer].handle_message(message)
        except:
          try:
            connections[connection_id]['server'].close()
          except:
            pass
          try:
            connections[connection_id]['client'].close()
          except:
            pass
          del connections[connection_id]


########################################################################################################################
#   TCP Client
########################################################################################################################
class TCPConnection(asyncore.dispatcher):
  STATE_ERROR = -1
  STATE_IDLE = 0
  STATE_RESOLVING = 1
  STATE_RESOLVED = 2
  STATE_CONNECTING = 3
  STATE_CONNECTED = 4

  def __init__(self, client_id):
    asyncore.dispatcher.__init__(self)
    self.client_id = client_id
    self.state = self.STATE_IDLE
    self.buffer = '';
    self.addr = None

  def SendMessage(self, type, message):
    message['message'] = type
    message['connection'] = self.client_id
    in_pipe.SendMessage(message)

  def handle_message(self, message):
    if message['message'] == 'resolve':
      self.HandleResolve(message)
    if message['message'] == 'connect':
      self.HandleConnect(message)
    if message['message'] == 'data' and 'data' in message and len(message['data']) and self.state == self.STATE_CONNECTED:
      self.buffer += message['data']
    if message['message'] == 'closed':
      if self.state != self.STATE_ERROR:
        self.state = self.STATE_ERROR
        if self.connected:
          self.close()

  def handle_error(self):
    print '[{0:d}] Error'.format(self.client_id)
    if self.state == self.STATE_CONNECTING:
      self.SendMessage('connected', {'success': False, 'address': self.addr})
    if self.state != self.STATE_ERROR:
      self.state = self.STATE_ERROR
      self.SendMessage('closed', {})
    self.handle_error()

  def handle_close(self):
    self.close()
    print '[{0:d}] Closed'.format(self.client_id)
    if self.state == self.STATE_CONNECTING:
      self.SendMessage('connected', {'success': False, 'address': self.addr})
    if self.state != self.STATE_ERROR:
      self.state = self.STATE_ERROR
      self.SendMessage('closed', {})

  def writable(self):
    if self.state == self.STATE_CONNECTING:
      self.state = self.STATE_CONNECTED
      self.SendMessage('connected', {'success': True, 'address': self.addr})
      print '[{0:d}] Connected'.format(self.client_id)
    return (len(self.buffer) > 0)

  def handle_write(self):
    self.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sent = self.send(self.buffer)
    #print '[{0:d}] TCP => {1:d} byte(s)'.format(self.client_id, sent)
    self.buffer = self.buffer[sent:]

  def handle_read(self):
    data = self.recv(1460)
    if data:
      #print '[{0:d}] TCP <= {1:d} byte(s)'.format(self.client_id, len(data))
      self.SendMessage('data', {'data': data})

  def HandleResolve(self, message):
    #TODO (pmeenan): Run the actual lookup in a thread asynchronously
    if 'hostname' in message:
      hostname = message['hostname']
    port = 0
    if 'port' in message:
      port = message['port']
    print '[{0:d}] Resolving {1}:{2:d}'.format(self.client_id, hostname, port)
    self.state = self.STATE_RESOLVING
    try:
      addresses = socket.getaddrinfo(hostname, port)
    except:
      addresses = ()
      print '[{0:d}] Resolving {1}:{2:d} FAILED'.format(self.client_id, hostname, port)
    self.state = self.STATE_RESOLVED
    self.SendMessage('resolved', {'addresses': addresses})

  def HandleConnect(self, message):
    if 'addresses' in message and len(message['addresses']):
      print '[{0:d}] Connecting'.format(self.client_id)
      self.state = self.STATE_CONNECTING
      self.addr = message['addresses'][0]
      self.create_socket(self.addr[0], socket.SOCK_STREAM)
      self.connect(self.addr[4])


########################################################################################################################
#   Socks5 Server
########################################################################################################################
class Socks5Server(asyncore.dispatcher):

  def __init__(self, host, port):
    global in_pipe
    global out_pipe
    asyncore.dispatcher.__init__(self)
    in_pipe = TSPipe(TSPipe.PIPE_IN)
    out_pipe = TSPipe(TSPipe.PIPE_OUT)
    self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
    self.set_reuse_addr()
    self.bind((host, port))
    self.listen(5)
    self.current_client_id = 0

  def handle_accept(self):
    global connections
    pair = self.accept()
    if pair is not None:
      sock, addr = pair
      self.current_client_id += 1
      print '[{0:d}] Incoming connection from {1}'.format(self.current_client_id, repr(addr))
      connections[self.current_client_id] = {
        'client' : Socks5Connection(sock, self.current_client_id),
        'server' : None
      }


# Socks5 reference: https://en.wikipedia.org/wiki/SOCKS#SOCKS5
class Socks5Connection(asyncore.dispatcher):
  STATE_ERROR = -1
  STATE_WAITING_FOR_HANDSHAKE = 0
  STATE_WAITING_FOR_CONNECT_REQUEST = 1
  STATE_RESOLVING = 2
  STATE_CONNECTING = 3
  STATE_CONNECTED = 4

  def __init__(self, connected_socket, client_id):
    asyncore.dispatcher.__init__(self, connected_socket)
    self.client_id = client_id
    self.state = self.STATE_WAITING_FOR_HANDSHAKE
    self.ip = None
    self.addresses = None
    self.hostname = None
    self.port = None
    self.requested_address = None
    self.buffer = ''
    self.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

  def SendMessage(self, type, message):
    message['message'] = type
    message['connection'] = self.client_id
    out_pipe.SendMessage(message)

  def handle_message(self, message):
    if message['message'] == 'resolved':
      self.HandleResolved(message)
    if message['message'] == 'connected':
      self.HandleConnected(message)
    if message['message'] == 'data' and 'data' in message and len(message['data']) and self.state == self.STATE_CONNECTED:
      self.send(message['data'])
    if message['message'] == 'closed':
      if self.state != self.STATE_ERROR:
        self.state = self.STATE_ERROR
        if self.connected:
          self.close()

  def writable(self):
    return (len(self.buffer) > 0)

  def handle_write(self):
    sent = self.send(self.buffer)
    self.buffer = self.buffer[sent:]

  def handle_read(self):
    global connections
    data = self.recv(1460) #Consume in packet-sized chunks (max)
    data_len = len(data)
    if data_len:
      if self.state == self.STATE_CONNECTED:
        self.SendMessage('data', {'data': data})
      elif self.state == self.STATE_WAITING_FOR_HANDSHAKE:
        self.state = self.STATE_ERROR #default to an error state, set correctly if things work out
        if data_len >= 2 and ord(data[0]) == 0x05:
          supports_no_auth = False
          auth_count = ord(data[1])
          if data_len == auth_count + 2:
            for i in range(auth_count):
              offset = i + 2
              if ord(data[offset]) == 0:
                supports_no_auth = True
          if supports_no_auth:
            # Respond with a message that "No Authentication" was agreed to
            print '[{0:d}] New Socks5 client'.format(self.client_id)
            response = chr(0x05) + chr(0x00)
            self.state = self.STATE_WAITING_FOR_CONNECT_REQUEST
            self.buffer += response
      elif self.state == self.STATE_WAITING_FOR_CONNECT_REQUEST:
        self.state = self.STATE_ERROR #default to an error state, set correctly if things work out
        if data_len >= 10 and ord(data[0]) == 0x05 and ord(data[2]) == 0x00:
          if ord(data[1]) == 0x01: #TCP connection (only supported method for now)
            connections[self.client_id]['server'] = TCPConnection(self.client_id)
          self.requested_address = data[3:]
          port_offset = 0
          if ord(data[3]) == 0x01:
            port_offset = 8
            self.ip = '{0:d}.{1:d}.{2:d}.{3:d}'.format(ord(data[4]), ord(data[5]), ord(data[6]), ord(data[7]))
          elif ord(data[3]) == 0x03:
            name_len = ord(data[4])
            if data_len >= 6 + name_len:
              port_offset = 5 + name_len
              self.hostname = data[5:5 + name_len]
          elif ord(data[3]) == 0x04 and data_len >= 22:
            port_offset = 20
            self.ip = '';
            for i in range(16):
              self.ip += '{0:02x}'.format(ord(data[4 + i]))
              if i % 2 and i < 15:
                self.ip += ':'
          if port_offset and connections[self.client_id]['server'] is not None:
            self.port = 256 * ord(data[port_offset]) + ord(data[port_offset + 1])
            if self.port:
              if self.ip is None and self.hostname is not None:
                self.state = self.STATE_RESOLVING
                self.SendMessage('resolve', {'hostname': self.hostname, 'port': self.port})
              elif self.ip is not None:
                self.state = self.STATE_CONNECTING
                self.addresses = socket.getaddrinfo(self.ip, self.port)
                self.SendMessage('connect', {'addresses': self.addresses, 'port': self.port})

  def handle_close(self):
    self.close()
    if self.state != self.STATE_ERROR:
      self.state = self.STATE_ERROR
      self.SendMessage('closed', {})

  def HandleResolved(self, message):
    if 'addresses' in message:
      self.state = self.STATE_CONNECTING
      self.addresses = message['addresses']
      self.SendMessage('connect', {'addresses': self.addresses, 'port': self.port})

  def HandleConnected(self, message):
    if 'success' in message and self.state == self.STATE_CONNECTING:
      response = chr(0x05)
      if message['success']:
        response += chr(0x00)
        self.state = self.STATE_CONNECTED
      else:
        response += chr(0x01)
        self.state = self.STATE_ERROR
      response += chr(0x00)
      response += self.requested_address
      self.buffer += response


########################################################################################################################
#   Main Entry Point
########################################################################################################################
def main():
  global server
  import argparse
  parser = argparse.ArgumentParser(description='Traffic-shaping socks5 proxy.',
                                   prog='tsproxy')
  parser.add_argument('-p', '--port', type=int, default=1080, help="Server port (defaults to 1080)")
  parser.add_argument('-i', '--interface', default='localhost', help="Server interface address (defaults to localhost)")
  options = parser.parse_args()

  print 'Starting Socks5 proxy server on {0}:{1:d}'.format(options.interface, options.port)
  signal.signal(signal.SIGINT, signal_handler)
  server = Socks5Server(options.interface, options.port)
  asyncore.loop(timeout = 0.001, use_poll = True)

def signal_handler(signal, frame):
  global server
  print('Exiting...')
  del server
  sys.exit(0)

if '__main__' == __name__:
  main()
