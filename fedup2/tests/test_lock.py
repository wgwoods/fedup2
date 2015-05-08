# test_lock.py - unit tests for fedup2.lock
#
# Copyright (c) 2015 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Author: Will Woods <wwoods@redhat.com>

import unittest
from ..lock import lock, unlock, LockError, PidLock, PidLockError
from tempfile import mktemp
from os import unlink, getpid
from os.path import exists
from multiprocessing import Process, Pipe #pylint: disable=no-name-in-module
from contextlib import contextmanager

class LockTimeoutError(IOError):
    pass

class TestLockSimple(unittest.TestCase):
    '''locking tests that only require one process'''
    def setUp(self):
        self.f = open(mktemp(), 'w+')

    def tearDown(self):
        self.f.close()

    def test_lock_ok(self):
        '''test lock()'''
        lock(self.f)

    def test_unlock_ok(self):
        '''test unlock()'''
        unlock(self.f)

    def test_lock_block_ok(self):
        '''test blocking lock (block=True)'''
        lock(self.f, block=True)

    def test_readlock_readonly(self):
        '''test readonly lock (share=True)'''
        with open(self.f.name, "r") as f:
            lock(f, share=True)

    def test_writelock_readonly(self):
        '''check that lock(share=False) on a readonly file (r) raises IOError'''
        with open(self.f.name, "r") as f:
            with self.assertRaises(IOError):
                lock(f, share=False)

    def test_writelock_readwrite(self):
        '''check that lock(share=False) on a readwrite file (r+) works'''
        with open(self.f.name, "r+") as f:
            lock(f, share=False)

class TestLockMultiProc(unittest.TestCase):
    def setUp(self):
        (self.inpipe, self.outpipe) = Pipe()
        self.filename = mktemp()
        self.p = None
        self.f = None

    def tearDown(self):
        self.inpipe.close()
        self.outpipe.close()
        if self.f:
            self.f.close()

    def otherlock_start(self, block=False, share=False):
        def dolock(name, block, share):
            try:
                f = open(name, 'w+')
                lock(f, block=block, share=share)
            except Exception as e: #pylint: disable=broad-except
                self.outpipe.send(e)
            else:
                self.outpipe.send(True)
            # wait for the signal to exit
            if self.outpipe.poll(5):
                self.outpipe.recv()
            unlink(name)
            f.close()
        # okay, launch the process
        self.p = Process(target=dolock, args=(self.filename,block,share))
        self.p.start()

    def otherlock_ready(self, timeout=5):
        # wait for the lock to be acquired, raise exception if needed
        if not self.inpipe.poll(timeout):
            raise LockTimeoutError("timeout waiting for other process")
        else:
            e = self.inpipe.recv()
            if e is not True:
                raise e

    def otherlock_finish(self):
        # tell the other side to exit
        self.inpipe.send(True)
        self.p.join()

    def lock(self, mode='a+', block=False, share=False):
        self.f = open(self.filename, mode)
        lock(self.f, block=block, share=share)

    def unlock(self):
        unlock(self.f)

    @contextmanager
    def otherlock(self, block=False, share=False):
        self.otherlock_start(block, share)
        self.otherlock_ready()
        yield
        self.otherlock_finish()

    def test_basic_operation(self):
        '''make sure our test fixtures work as expected'''
        self.assertFalse(exists(self.filename))
        self.otherlock_start()
        self.assertTrue(self.p.is_alive())
        self.otherlock_ready()
        self.assertTrue(exists(self.filename))
        self.otherlock_finish()
        self.assertFalse(exists(self.filename))

    def test_lock_fail(self):
        '''test that trying to lock a locked file raises LockError'''
        with self.otherlock():
            with self.assertRaises(LockError):
                self.lock()

    def test_lock_share_fail(self):
        '''test that trying to read-lock a locked file raises LockError'''
        with self.otherlock():
            with self.assertRaises(LockError):
                self.lock(share=True)

    def test_lock_share_ok(self):
        '''test that read-locking a read-locked file succeeds'''
        with self.otherlock(share=True):
            self.lock(share=True)

    def test_lock_block_wait(self):
        '''test that a blocking lock actually waits for the lock'''
        self.lock(share=False)
        self.otherlock_start(share=False, block=True)
        with self.assertRaises(LockTimeoutError):
            self.otherlock_ready(0.3)
        unlock(self.f)
        self.otherlock_ready(0.3)
        self.otherlock_finish()

class TestPidLock(unittest.TestCase):
    def setUp(self):
        self.pidlock = PidLock(mktemp())
        self.pid = getpid()
        (self.inpipe, self.outpipe) = Pipe()

    def tearDown(self):
        self.pidlock.remove()

    def test_pidlock_pid(self):
        '''check that PidLock file contains our correct pid'''
        with open(self.pidlock.name) as pidfile:
            self.assertEqual("%u\n" % self.pid, pidfile.read())

    def test_no_inherit(self):
        '''check that child processes do not inherit the PidLock'''
        def child_sees_lock(filename):
            lockf = open(filename, 'r+')
            try:
                lock(lockf)
            except LockError:
                self.outpipe.send(True)
            else:
                self.outpipe.send(False)
            finally:
                lockf.close()
        p = Process(target=child_sees_lock, args=(self.pidlock.name,))
        p.start()
        locked = self.inpipe.recv()
        self.assertNotEqual(p.pid, self.pid)
        p.join()
        self.assertTrue(locked)

    def test_pidlock(self):
        '''check whether PidLock raises PidLockError if locked'''
        def child_pidlock(filename):
            try:
                pidlock = PidLock(filename)
            except PidLockError as e:
                self.outpipe.send(e)
            else:
                self.outpipe.send(None)
                pidlock.remove()
        p = Process(target=child_pidlock, args=(self.pidlock.name,))
        p.start()
        if not self.inpipe.poll(5):
            raise LockTimeoutError("timeout waiting for other process")
        e = self.inpipe.recv()
        self.assertTrue(isinstance(e, PidLockError))
        self.assertEqual(e.pid, self.pid)
