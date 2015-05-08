# state.py - track upgrade state in a well-known place
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

# pylint: disable=wildcard-import,unused-wildcard-import

import os
try:
    from configparser import *
except ImportError:
    from ConfigParser import *

import shlex
try:
    from shlex import quote as _quote
except ImportError:
    from pipes import quote as _quote

from .i18n import _

from dnf.cli.format import format_number
from dnf.util import ensure_dir

import logging
log = logging.getLogger("fedup2.state")

__all__ = ['State']

PACKAGELIST = 'package.list'

def shelljoin(argv):
    return ' '.join(_quote(a) for a in argv)

def shellsplit(cmdstr):
    return shlex.split(cmdstr or '')

def _configprop(section, option, encode=None, decode=None, doc=None):
    # pylint: disable=protected-access
    def getprop(self):
        value = self._get(section, option)
        if callable(decode) and value is not None:
            value = decode(value)
        return value
    def setprop(self, value):
        if callable(encode):
            value = encode(value)
        self._set(section, option, value)
    def delprop(self):
        self._del(section, option)
    return property(getprop, setprop, delprop, doc)

class State(object):
    statefile = '/var/lib/system-upgrade/upgrade.state'
    def __init__(self):
        self._conf = RawConfigParser()
        self._conf.read(self.statefile)
        self.args = None

    def _get(self, section, option):
        try:
            return self._conf.get(section, option)
        except (NoSectionError, NoOptionError):
            return None

    def _set(self, section, option, value):
        if value is None: # ...probably need a broader check here
            raise TypeError('expected string, got %r' % type(value).__name__)
        try:
            self._conf.add_section(section)
        except DuplicateSectionError:
            pass
        self._conf.set(section, option, value)
        log.debug("set %s.%s=%s", section, option, value)

    def _del(self, section, option):
        try:
            self._conf.remove_option(section, option)
            log.debug("del %s.%s", section, option)
        except NoSectionError:
            pass

    def _items(self, section):
        try:
            return self._conf.items(section)
        except NoSectionError:
            return []

    def write(self):
        ensure_dir(os.path.dirname(self.statefile))
        with open(self.statefile, 'w') as outf:
            self._conf.write(outf)

    def clear(self):
        persist = self._items("persist")
        self._conf = RawConfigParser()
        log.debug("cleared all data")
        for name, val in persist:
            self._set("persist", name, val)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.write()

    # system info
    current_system = _configprop("system", "distro")

    # target system info. upgrade target implies upgrade in progress.
    upgrade_target = _configprop("upgrade", "target")
    upgrade_ready = _configprop("upgrade", "ready")
    enabled_repos = _configprop("upgrade", "enabled_repos")
    releasever = _configprop("upgrade", "releasever")

    # persistent stuff that we should keep after a cancel
    datadir = _configprop("persist", "datadir")
    cachedir = _configprop("persist", "cachedir")

    # info about the download process
    pkgs_total = _configprop("download", "pkgs_total")
    size_total = _configprop("download", "size_total")
    cmdline = _configprop("download", "cmdline",
                          encode=shelljoin,
                          decode=shellsplit)

    @property
    def packagelist(self):
        if not self.datadir:
            raise TypeError("datadir is not set")
        return os.path.join(self.datadir, PACKAGELIST)

    def read_packagelist(self):
        try:
            with open(self.packagelist) as listf:
                return [os.path.join(self.datadir, p.strip()) for p in listf]
        except (TypeError, IOError, OSError):
            return []

    def write_packagelist(self, pkgs):
        with open(self.packagelist, 'w') as outf:
            outf.writelines(os.path.relpath(p, self.datadir)+'\n' for p in pkgs)

    def clean_datadir(self):
        keepfiles = set(self.read_packagelist())
        keepfiles.add(self.packagelist)
        for f in os.listdir(self.datadir):
            fullpath = os.path.join(self.datadir, f)
            if fullpath not in keepfiles:
                log.info("removing %s from datadir", f)
                os.unlink(fullpath)

    def get_size_local(self):
        '''Return the total size (in bytes) of the packages in packagelist that
           are present in datadir, or None if there's no packagelist.
           You can get the download progress percentage with:
             100.0 * state.size_total / state.get_size_local()
        '''
        pkglist = self.read_packagelist()
        if pkglist:
            return sum(os.stat(f).st_size for f in pkglist if os.path.exists(f))

    def summarize(self):
        if not self.upgrade_target:
            msg = [
                _("No upgrade in progress.")
            ]
        elif not self.upgrade_ready:
            msg = [
                _("Upgrade to %s in progress.") % self.upgrade_target,
                _("Use 'fedup2 resume' to resume downloading."),
                _("Use 'fedup2 cancel' to cancel the upgrade."),
            ]
            localdata = self.get_size_local()
            if localdata:
                total = int(self.size_total)
                pct = 100.0*localdata/total
                msg[0] = _("Download of %s is %.1f%% complete (%s/%s)") % (
                    self.upgrade_target, pct,
                    format_number(localdata), format_number(total)
                )
        else:
            msg = [
                _("Ready for upgrade to %s.") % self.upgrade_target,
                _("Use 'fedup2 reboot' to start the upgrade."),
                _("Use 'fedup2 refresh' to check for new updates."),
                _("Use 'fedup2 cancel' to cancel the upgrade."),
            ]
        return "\n".join(msg)
