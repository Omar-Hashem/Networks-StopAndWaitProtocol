import socket
from validator import isValidPacket
import packet
import pickle
import random
import threading
import time

AF_INET = 4
AF_INET6 = 6
_BUFFER_SIZE = 1024
_MAX_TIMEOUT_ACK_THRESHOLD = 100
_INITAL_TIME_OUT_INTERVAL = 0.5

class socketTCP:

    def __init__(self, port, socket_family, loss_probability):
        self.port = port
        self.socket_family = socket_family
        self.loss_probability = loss_probability
        self.cum_prob = 0.0
        self.udt_send_cnt = 0
        self.udt_rcv_cnt = 0
        self.send_state = 0
        self.receive_state = 0
        self.dest_addr = 0  # to be setted by address/connect (IP,Port)
        self.number_of_receivers = 100

        # dynamic timeout parameters
        self.alpha = 0.125
        self.beta = 0.25
        self.estimated_RTT = _INITAL_TIME_OUT_INTERVAL
        self.dev_RTT = _INITAL_TIME_OUT_INTERVAL / 2.0

        if socket_family == AF_INET:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        else:
            self.socket = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)

        self.socket.settimeout(_INITAL_TIME_OUT_INTERVAL)

    def bind(self):  # if port = 0, then OS picks free port for the socket
        # We need to clear this port, later
        try:
            self.socket.bind(('0.0.0.0', self.port))

            self.port = self.socket.getsockname()[1]

            return True
        except socket.error:
            return False

    def _drop(self):
        if self.cum_prob >= 1.0:
            self.cum_prob = self.cum_prob - 1.0
            return True
        else:
            return False

    def _udt_send(self, data, dest_ip, dest_port):
        self.udt_send_cnt = self.udt_send_cnt + 1
        self.cum_prob = self.cum_prob + self.loss_probability

        if not self._drop():
            self.socket.sendto(data, (dest_ip, dest_port))

    def _udt_receive(self):
        self.udt_rcv_cnt = self.udt_rcv_cnt + 1

        data, address = self.socket.recvfrom(_BUFFER_SIZE)  # returns (data, address = (ip, port))

        my_packet = pickle.loads(data)

        return (my_packet, address)

    def timeout_enhance(self, sample_RTT):
        self.estimated_RTT = (1 - self.alpha) * self.estimated_RTT + self.alpha * sample_RTT
        self.dev_RTT = (1 - self.beta) * self.dev_RTT + self.beta * abs(self.estimated_RTT - sample_RTT)
        # self.socket.settimeout(self.estimated_RTT + 4 * self.dev_RTT)

    def _rdt_send(self, data, dest_ip, dest_port):

        # RTT is the time between send & valid ACK

        data = packet.get_data_packet(5555, self.send_state, data)

        send_time = time.time()

        self._udt_send(pickle.dumps(data), dest_ip, dest_port)

        # State is sender waits for ACK

        cnt = 0
        while True:
            cnt = cnt + 1
            try:
                ack_data, address = self._udt_receive()
                if not isinstance(ack_data, packet.ack_packet):
                    continue
                if not isValidPacket(ack_data):
                    continue
                if ack_data.ack_no != self.send_state:
                    continue

                sample_RTT = time.time() - send_time
                self.timeout_enhance(sample_RTT)
                self.send_state = 1 - self.send_state  # Change state 0 -> 1 and vice versa
                break
            except socket.timeout:
                if cnt == _MAX_TIMEOUT_ACK_THRESHOLD:
                    self.send_state = 1 - self.send_state
                    break

                send_time = time.time()
                self._udt_send(pickle.dumps(data), dest_ip, dest_port)
                continue

    def send_ack(self, checkSum, ackNum, address):
        ack = packet.get_ack_packet(checkSum, ackNum)
        self._udt_send(pickle.dumps(ack), address[0], address[1])

    def _rdt_receive(self):
        while True:
            try:
                data, address = self._udt_receive()

                if not isValidPacket(data):
                    self.send_ack(5555, 1 - self.receive_state, address)
                    continue

                if data.seq_no != self.receive_state:
                    self.send_ack(5555, 1 - self.receive_state, address)
                    continue

                # data, valid, has correct sequence number, no loss
                self.send_ack(5555, self.receive_state, address)  # Last Ack might not reach sender

                self.receive_state = 1 - self.receive_state  # Change state 0 -> 1 and vice versa

                return (data.data, address)
            except socket.timeout:
                continue

    def send(self, data):
        self._rdt_send(data, self.dest_addr[0], self.dest_addr[1])

    def receive(self):
        return self._rdt_receive()[0]

    def accept(self):
        self.receive_state = 0
        self.send_state = 0

        data, address = self._rdt_receive()
        # data is number x

        s = make_socket(0, self.socket_family, self.loss_probability)

        if not isinstance(data, int):
            return (False, s)

        if threading.activeCount() - 1 >= self.number_of_receivers:
            self._rdt_send((data + 5, s.port), address[0], address[1])
            return (False, s)

        s.dest_addr = address

        if not s.bind():
            self._rdt_send((data + 5, s.port), address[0], address[1])
            return (False, s)

        self._rdt_send((data + 1, s.port), address[0], address[1])

        data, address = self._rdt_receive()

        self.receive_state = 0
        self.send_state = 0

        if data == s.port + 1:
            return (True, s)
        else:
            return (False, s)

    def connect(self, dest_ip, dest_port):
        # client SYN(X) where x is random
        # server SYNACK(ACK=X+1,Y) let Y = new port
        # client SYNACK(Y+1)

        self.receive_state = 0
        self.send_state = 0

        x = random.randint(1, 100000)
        self._rdt_send(x, dest_ip, dest_port)

        data, address = self._rdt_receive()

        if data[0] == x + 1:
            self.dest_addr = (dest_ip, data[1])
            self._rdt_send(data[1] + 1, dest_ip, dest_port)
        else:
            self.receive_state = 0
            self.send_state = 0
            return False

        self.receive_state = 0
        self.send_state = 0
        return True

    def listen(self, limit_of_receivers):
        self.number_of_receivers = limit_of_receivers

    def close(self):
        self.socket.close()

def make_socket(port, socket_family, loss_probability):
    return socketTCP(port, socket_family, loss_probability)















