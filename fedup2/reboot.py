# reboot.py - handle the 'reboot' command
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

import libmount

from os import symlink
from os.path import dirname
from dnf.util import ensure_dir
from subprocess import check_output, PIPE
from .mounts import write_mount_unit, MOUNT_UNIT_DIR

import logging
log = logging.getLogger("fedup2.reboot")

__all__ = (
    'Bootprep',
    'reboot',
    'MAGIC_SYMLINK',
)

MAGIC_SYMLINK='/system-update'

def reboot():
    log.info("initiating reboot")
    cmd = ["systemctl","reboot"]
    return check_output(cmd, stderr=PIPE)

class Bootprep(object):
    def __init__(self, cli):
        self.cli = cli

    def prep_mounts(self):
        # What filesystems hold the packages we're gonna use?
        mountinfo = libmount.Table('/proc/self/mountinfo')
        pkg_dirs = set(dirname(p) for p in self.cli.state.read_packagelist())
        pkg_mounts = set(mountinfo.find_mountpoint(path) for path in pkg_dirs)
        # Write mount units for everything the upgrade will need.
        # (it's OK if they're redundant - systemd will sort it out.)
        ensure_dir(MOUNT_UNIT_DIR)
        for mnt in pkg_mounts:
            write_mount_unit(mnt)

    def prep_boot(self):
        # make the magic symlink
        symlink(self.cli.state.datadir, "/system-update")
