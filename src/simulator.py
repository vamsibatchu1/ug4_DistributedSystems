#!/usr/bin/env python2.7


from collections import deque, namedtuple
import logging


SendAction = namedtuple('SendAction', 'destination payload')
ReceiveAction = namedtuple('ReceiveAction', 'sender payload')
PrintAction = namedtuple('PrintAction', 'payload')
MutexStartAction = namedtuple('MutexStartAction', '')
MutexEndAction = namedtuple('MutexEndAction', '')

Packet = namedtuple('Packet', 'sender destination payload time')
MutexReq = namedtuple('MutexReq', 'sender time')
MutexAck = namedtuple('MutexAck', 'sender destination')


class Process(object):
    def __init__(self, pid, actions, network, message_channel, mutex_channel):
        self.pid = pid
        self.actions = actions
        self.network = network
        self.message_channel = message_channel
        self.mutex_channel = mutex_channel
        self.clock = 0
        self.deferred_mutex_requests = set()
        self.mutex_req = None
        self.has_mutex = False

    def __repr__(self):
        return 'Process(pid={pid}, time={time})'.format(
            pid=self.pid, time=self.clock)

    def is_done(self):
        return len(self.actions) == 0

    def can_execute(self):
        # a process can execute an action if it has the mutex
        # or if no other process has the mutex
        other_mutex = any(process.has_mutex for process in self.network)
        return self.has_mutex or not other_mutex

    def execute_next(self):
        # respond to mutex requests
        self.handle_mutex_requests()
        # skip a turn if a different process has mutex or if the process
        # doesn't have any actions left to perform
        if self.is_done() or not self.can_execute():
            return
        # try to execute the next action
        # if the action was successful remove it from the action queue
        # otherwise try again next time
        if self.handle_action(self.actions[0]):
            self.actions.popleft()

    def lower_priority(self, mutex_req):
        # processes that asked for the mutex lock earlier or have a lower
        # process id have priority
        if self.clock > mutex_req.time:
            return True
        if self.clock < mutex_req.time:
            return False
        return self.pid > mutex_req.sender

    def handle_mutex_requests(self):
        # if the process doesn't need mutex or the process has lower priority
        # then approve all mutex requests
        # otherwise remember the mutex requests so that they can be approved
        # when the process releases the mutex
        reqs = (msg for msg in self.mutex_channel
                if isinstance(msg, MutexReq) and msg.sender != self.pid)
        acks = set()
        for req in reqs:
            ack = MutexAck(sender=self.pid, destination=req.sender)
            if self.mutex_req is None or self.lower_priority(req):
                acks.add(ack)
            else:
                self.deferred_mutex_requests.add(ack)
        self.mutex_channel.update(acks)

    def handle_action(self, action):
        if isinstance(action, SendAction):
            # send a message by putting it into the message pool
            self.clock += 1
            packet = Packet(
                sender=self.pid,
                destination=action.destination,
                payload=action.payload,
                time=self.clock)
            self.message_channel.append(packet)
            print 'sent {pid} {payload} {destination} {time}'.format(
                pid=self.pid, payload=packet.payload,
                destination=packet.destination, time=self.clock)
            return True

        elif isinstance(action, ReceiveAction):
            # try to receive a message
            for i in reversed(xrange(len(self.message_channel))):
                packet = self.message_channel[i]
                if packet.sender != action.sender:
                    continue
                if packet.destination != self.pid:
                    continue
                if packet.payload != action.payload:
                    continue
                self.clock = max(self.clock, packet.time) + 1
                print 'received {pid} {payload} {sender} {time}'.format(
                    pid=self.pid, payload=packet.payload,
                    sender=packet.sender, time=self.clock)
                del self.message_channel[i]
                return True
            # honor the happens-before relationship: wait for the message to
            # get sent before trying to receive it
            else:
                return False

        elif isinstance(action, PrintAction):
            # use the shared printer
            self.clock += 1
            print 'printed {pid} {payload} {time}'.format(
                pid=self.pid, payload=action.payload, time=self.clock)
            return True

        elif isinstance(action, MutexStartAction):
            # send a new mutex request
            if self.mutex_req is None:
                self.mutex_req = MutexReq(sender=self.pid, time=self.clock)
                self.mutex_channel.add(self.mutex_req)
            acks = set(msg for msg in self.mutex_channel
                       if isinstance(msg, MutexAck)
                       and msg.destination == self.pid)
            # wait until all processes have responded to the request
            if len(acks) != len(self.network) - 1:
                return False
            # acquire the mutex if all processes have responded to the request
            self.has_mutex = True
            self.mutex_channel.remove(self.mutex_req)
            return True

        elif isinstance(action, MutexEndAction):
            # release mutex lock and respond to all deferred mutex requests
            self.mutex_channel.update(self.deferred_mutex_requests)
            self.mutex_req = None
            self.has_mutex = False
            self.deferred_mutex_requests = set()
            return True


def parse_input(infile, **kwargs):
    network = kwargs.get('network', set())
    message_channel = kwargs.get('message_channel', deque())
    mutex_channel = kwargs.get('mutex_channel', set())
    process_name = None
    process_actions = deque()
    lines = (line.lower().strip() for line in infile if line.strip())
    for lineno, line in enumerate(lines, start=1):
        tokens = line.split()
        try:
            if line.startswith('begin process'):
                process_name = tokens[2]
            elif line.startswith('end process'):
                network.add(Process(
                    pid=process_name,
                    actions=process_actions,
                    network=network,
                    message_channel=message_channel,
                    mutex_channel=mutex_channel))
                process_name = None
                process_actions = deque()
            elif line.startswith('begin mutex'):
                process_actions.append(MutexStartAction())
            elif line.startswith('end mutex'):
                process_actions.append(MutexEndAction())
            elif line.startswith('send'):
                process_actions.append(SendAction(tokens[1], tokens[2]))
            elif line.startswith('recv'):
                process_actions.append(ReceiveAction(tokens[1], tokens[2]))
            elif line.startswith('print'):
                process_actions.append(PrintAction(tokens[1]))
            else:
                logging.warning(
                    'ignoring unknown command: "{command}" '
                    'on line {lineno} in file {filepath}'.format(
                        command=line, lineno=lineno, filepath=infile.name))
        except IndexError:
            logging.warning(
                'ignoring misformed command: "{command}" '
                'on line {lineno} in file {filepath}'.format(
                    command=line, lineno=lineno, filepath=infile.name))
    return network


def run_processes(network):
    # use a process manager to run the processes
    # processes are polleod in a round-robin manner until all are teminated
    while not all(process.is_done() for process in network):
        for process in network:
            process.execute_next()


def main(infile):
    network = parse_input(infile)
    run_processes(network)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(
        description='Simulator for the Ricart-Agrawala mutex algorithm')
    parser.add_argument('infile', type=argparse.FileType())
    args = parser.parse_args()
    infile = args.infile

    try:
        main(infile)
    finally:
        infile.close()
