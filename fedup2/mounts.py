# mounts.py - stuff for making mount units
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

from os.path import join

MOUNT_UNIT_DIR = '/lib/systemd/system/fedup2-system-upgrade.service.wants'

MOUNT_UNIT_TEMPLATE = \
"""
# This unit was generated by fedup2.
[Unit]
Before=system-update.target

[Mount]
What={mount.source}
Where={mount.target}
Type={mount.fstype}
Options={opts}
"""

def mount_unit(mount):
    opts = mount.fs_options
    # TODO: fix up opts, e.g.:
    # if mount.fstype == "btrfs", might need subvol=mount.root[1:]
    # if mount.source is a loop device, need to add 'loop'
    # (but we also need to change mount.source to the backing file)
    return MOUNT_UNIT_TEMPLATE.format(mount=mount, opts=opts)

# see systemd/src/shared/unit-name.c:do_escape() for the original algorithm
def systemd_mount_escape(path):
    unitname = ''
    okchars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz:_'
    for c in path.strip('/'):
        if c == '/':
            unitname += '-'
        elif (c in okchars or (c == '.' and unitname)):
            unitname += c
        else:
            unitname += '\\x%02x' % ord(c)
    return unitname or '-'

def write_mount_unit(mount, unitdir=MOUNT_UNIT_DIR):
    unitname = systemd_mount_escape(mount.target)+'.mount'
    with open(join(unitdir, unitname), 'w') as outf:
        outf.write(mount_unit(mount))