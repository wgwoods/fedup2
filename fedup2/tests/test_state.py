# test_state.py - tests for fedup2.state
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
from ..state import State

from tempfile import mkstemp, mkdtemp
import os


class TestStateBasic(unittest.TestCase):
    # pylint: disable=protected-access
    def setUp(self):
        State.statefile = ''
        self.state = State()

    def test_set(self):
        '''state: test setting a property'''
        p = "wow this is totally a datadir path"
        self.state.datadir = p
        self.assertEqual(self.state._conf.get("persist", "datadir"), p)

    def test_set_invalid(self):
        '''state: setting a property using an invalid type raises TypeError'''
        with self.assertRaises(TypeError):
            self.state.datadir = None

    def test_get(self):
        '''state: test getting a property'''
        self.state._conf.add_section("persist")
        self.state._conf.set("persist", "datadir", "A VERY COOL VALUE")
        self.assertEqual(self.state.datadir, "A VERY COOL VALUE")

    def test_get_missing(self):
        '''state: getting property with missing config item returns None'''
        self.assertTrue(self.state.datadir is None)

    def test_del(self):
        '''state: test deleting a property'''
        self.state.datadir = "doomed"
        del self.state.datadir
        self.assertTrue(self.state.datadir is None)
        self.assertFalse(self.state._conf.has_option("persist", "datadir"))

    def test_cmdline(self):
        '''state: test the cmdline property'''
        argv = ['/usr/bin/cowsay', '-e\tx', '-T\'\'', 'i am made of meat']
        self.state.cmdline = argv
        self.assertEqual(self.state.cmdline, argv)

    def test_cmdline_missing(self):
        '''state: missing cmdline also returns None'''
        self.assertEqual(self.state.cmdline, None)

    def test_summarize(self):
        '''state: make sure summarize() works'''
        target = "TacOS 4u"
        self.assertFalse(target in self.state.summarize())
        # if we have a target, the summary should mention it
        self.state.upgrade_target = target
        inprog_msg = self.state.summarize()
        self.assertTrue(target in inprog_msg)
        # different messages for in-progress vs. ready-to-go
        self.state.upgrade_ready = 1
        self.assertTrue(target in self.state.summarize())
        self.assertNotEqual(self.state.summarize(), inprog_msg)
        # if we're now unready again, we should have the in-progress message
        del self.state.upgrade_ready
        self.assertEqual(self.state.summarize(), inprog_msg)

class TestStateWithFile(unittest.TestCase):
    def setUp(self):
        # it'd probably be better to use a mock file here
        self.fd, self.tmpfile = mkstemp(prefix='state.')
        State.statefile = self.tmpfile
        self.state = State()

    def _read_data(self):
        with open(self.tmpfile) as inf:
            return inf.read()

    def tearDown(self):
        os.unlink(self.tmpfile)
        os.close(self.fd)

    def test_write(self):
        '''state: test State.write()'''
        self.state.datadir = "/data"
        self.state.write()
        data = self._read_data()
        self.assertEqual(data.strip(), '[persist]\ndatadir = /data')

    def test_context(self):
        '''state: test State as context manager'''
        target = "TacOS 4u"
        with self.state:
            self.state.upgrade_target = target
        newstate = State()
        self.assertEqual(newstate.upgrade_target, target)

    def test_clear(self):
        '''state: test State.clear()'''
        osname = "TacOS 17"
        with self.state as state:
            self.state.current_system = osname
        self.state = State()
        self.assertEqual(self.state.current_system, osname)
        with self.state as state:
            state.clear()
        self.assertEqual(self._read_data(), '')

class TestStatePackageList(unittest.TestCase):
    def setUp(self):
        self.tmpdir = mkdtemp(prefix='state.')
        self.pkglist = [
            self.tmpdir + '/fake-1.rpm',
            self.tmpdir + '/subdir/fake-2.rpm'
        ]
        self.state = State()
        self.state.datadir = self.tmpdir

    def tearDown(self):
        for p in os.listdir(self.tmpdir):
            os.unlink(os.path.join(self.tmpdir,p))
        os.rmdir(self.tmpdir)

    def test_no_datadir(self):
        '''state: write_packagelist() raises TypeError if datadir is None'''
        del self.state.datadir
        with self.assertRaises(TypeError):
            self.state.write_packagelist(self.pkglist)
        self.assertEqual(self.state.read_packagelist(), [])

    def test_packagelist(self):
        '''state: write_packagelist(), read_packagelist()'''
        self.state.write_packagelist(self.pkglist)
        self.assertEqual(self.state.read_packagelist(), self.pkglist)

    def test_no_packagelist_file(self):
        '''state: read_packagelist() returns [] if file missing'''
        self.assertEqual(self.state.read_packagelist(), [])

    def test_packagelist_file_contents(self):
        '''state: package.list contains paths relative to its location'''
        packagelist_data = ''.join(os.path.relpath(p,self.tmpdir)+'\n'
                                   for p in self.pkglist)
        self.state.write_packagelist(self.pkglist)
        with open(os.path.join(self.tmpdir, "package.list")) as inf:
            self.assertEqual(inf.read(), packagelist_data)
