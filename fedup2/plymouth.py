from subprocess import call

__all__ = [
    'PlymouthOutput','message','progress','set_mode','ping'
]

PLYMOUTH = '/usr/bin/plymouth'

def message(msg):
    return call([PLYMOUTH, "display-message", "--text", msg]) == 0

def progress(percent):
    return call([PLYMOUTH, "system-update", "--progress", str(percent)]) == 0

def set_mode(mode):
    return call([PLYMOUTH, "change-mode", "--"+mode]) == 0

def ping():
    return call([PLYMOUTH, "--ping"]) == 0

class _PlymouthOutput(object):
    def __init__(self):
        self.msg = ""
        self.mode = ""
        self.percent = -1
        self.alive = ping()

    def ping(self):
        self.alive = ping()
        return self.alive

    def message(self, msg):
        if msg != self.msg:
            self.alive = message(msg)
            self.msg = msg

    def set_mode(self, mode):
        if mode != self.mode:
            self.alive = set_mode(mode)
            self.mode = mode

    def progress(self, percent):
        if percent != self.percent:
            self.alive = progress(percent)
            self.percent = percent

_PlymouthSingleton = _PlymouthOutput()

def PlymouthOutput():
    return _PlymouthSingleton
