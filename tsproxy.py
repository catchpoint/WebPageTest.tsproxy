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
import socket

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
        connections[connection_id][peer].handle_message(message)


########################################################################################################################
#   TCP Client
########################################################################################################################
class TCPConnection(asyncore.dispatcher_with_send):
  def __init__(self, client_id):
    self.client_id = client_id
    asyncore.dispatcher.__init__(self)

  def handle_message(self, message):
    print '{0:d} TCPConnection processing message {1}'.format(self.client_id, message['message'])


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
      print 'Incoming connection from %s' % repr(addr)
      self.current_client_id += 1
      connections[self.current_client_id] = {
        'client' : Socks5Connection(sock, self.current_client_id),
        'server' : None
      }


# Socks5 reference: https://en.wikipedia.org/wiki/SOCKS#SOCKS5
class Socks5Connection(asyncore.dispatcher_with_send):
  STATE_ERROR = -1
  STATE_WAITING_FOR_HANDSHAKE = 0
  STATE_WAITING_FOR_CONNECT_REQUEST = 1
  STATE_RESOLVING = 2
  STATE_CONNECTING = 3
  STATE_CONNECTED = 4

  def __init__(self, socket, client_id):
    asyncore.dispatcher_with_send.__init__(self, socket)
    self.client_id = client_id
    self.state = self.STATE_WAITING_FOR_HANDSHAKE
    self.ip = None
    self.hostname = None
    self.port = None

  def handle_read(self):
    global connections
    data = self.recv(1460) #Consume in packet-sized chunks (max)
    data_len = len(data)
    if data_len:
      print '{0:d}: received {1:d} bytes'.format(self.client_id, len(data))
      if self.state == self.STATE_CONNECTED:
        print 'Connected'
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
            print '{0:d}: Responding to valid handhake request'.format(self.client_id)
            response = chr(0x05) + chr(0x00)
            self.state = self.STATE_WAITING_FOR_CONNECT_REQUEST
            self.send(response)
      elif self.state == self.STATE_WAITING_FOR_CONNECT_REQUEST:
        self.state = self.STATE_ERROR #default to an error state, set correctly if things work out
        if data_len >= 10 and ord(data[0]) == 0x05 and ord(data[2]) == 0x00:
          if ord(data[1]) == 0x01: #TCP connection (only supported method for now)
            connections[self.client_id]['server'] = TCPConnection(self.client_id)
          port_offset = 0
          if ord(data[3]) == 0x01:
            port_offset = 8
            self.ip = '{0:d}.{1:d}.{2:d}.{3:d}'.format(ord(data[4]), ord(data[5]), ord(data[6]), ord(data[7]))
            print '{0:d}: Connect to IPv4 address: {1}'.format(self.client_id, self.ip)
          elif ord(data[3]) == 0x03:
            name_len = ord(data[4])
            if data_len >= 6 + name_len:
              port_offset = 5 + name_len
              self.hostname = data[5:5 + name_len]
              print '{0:d}: Connect to host: {1}'.format(self.client_id, self.hostname)
          elif ord(data[3]) == 0x04 and data_len >= 22:
            port_offset = 20
            self.ip = '';
            for i in range(16):
              self.ip += '{0:02x}'.format(ord(data[4 + i]))
              if i % 2 and i < 15:
                self.ip += ':'
            print '{0:d}: Connect to IPv6 address: {1}'.format(self.client_id, self.ip)
          if port_offset and connections[self.client_id]['server'] is not None:
            self.port = 256 * ord(data[port_offset]) + ord(data[port_offset + 1])
            print '{0:d}: Port: {1:d}'.format(self.client_id, self.port)
            if self.port:
              if self.ip is None and self.hostname is not None:
                self.state = self.STATE_RESOLVING
                self.SendMessage('resolve', {'hostname': self.hostname})
              elif self.ip is not None:
                self.state = self.STATE_CONNECTING
                self.SendMessage('connect', {'hostname': self.hostname})

  def SendMessage(self, type, message):
    message['message'] = type
    message['connection'] = self.client_id
    out_pipe.SendMessage(message)

  def handle_message(self, message):
    print '{0:d} Socks5Connection processing message {1}'.format(self.client_id, message['message'])


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
  server = Socks5Server(options.interface, options.port)
  asyncore.loop()

if '__main__' == __name__:
  main()
