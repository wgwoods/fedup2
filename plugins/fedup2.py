# fedup2.py - implement 'system-upgrade' command
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
# Author(s): Will Woods <wwoods@redhat.com>

import os
import json

from argparse import ArgumentParser
from subprocess import call

import dnf
import dnf.cli
from dnf.i18n import _

import logging
log = logging.getLogger("dnf.plugin.fedup")


PLYMOUTH = '/usr/bin/plymouth'
DEFAULT_DATADIR = '/var/lib/fedup'
MAGIC_SYMLINK = '/system-update'
SYSTEMD_FLAG_FILE = '/system-update/.dnf-fedup2-upgrade'

NO_KERNEL_MSG = _("No new kernel packages were found.")
RELEASEVER_MSG = _("Need a --releasever greater than the current system version.")
DOWNLOAD_FINISHED_MSG = _("Download complete! Use 'dnf %s reboot' to start the upgrade.")


def reboot():
    call(["systemctl", "reboot"])

# Plymouth helper class + singleton object
class _PlymouthOutput(object):
    def __init__(self):
        self._last_args = dict()
        self.alive = None

    def _plymouth(self, cmd, *args):
        if cmd == '--ping' or args != self._last_args.get(cmd):
            self.alive = (call([PLYMOUTH, cmd] + args) == 0)
            self._last_args[cmd] = args
        return self.alive

    def ping(self):
        return self._plymouth("--ping")

    def message(self, msg):
        return self._plymouth("display-message", "--text", msg)

    def set_mode(self, mode):
        return self._plymouth("change-mode", "--"+mode)

    def progress(self, percent):
        return self._plymouth("system-update", "--progress", str(percent))
Plymouth = _PlymouthOutput()


# A couple checkXXX() functions in the style of dnf.cli.command.check*
def checkReleaseVer(conf):
    if dnf.rpm.detect_releasever(conf.installroot) == conf.releasever:
        raise dnf.cli.CliError(RELEASEVER_MSG)

def checkDataDir(datadir):
    if os.path.exists(datadir) and not os.path.isdir(datadir):
        raise dnf.cli.CliError(_("--datadir: File exists"))


# Holds the state of the upgrade between runs of the command.
# Would be nice if dnf.Base provided access to its persistor, but oh well
class State(object):
    statefile = '/var/lib/fedup/upgrade.state'
    def __init__(self):
        self._data = {}
        self._read()

    # helper function for creating the properties on the State object
    def _prop(section, option): # pylint: disable=no-self-argument
        # pylint: disable=protected-access
        def setprop(self, value):
            self._data.setdefault(section,{})[option] = value
        def getprop(self):
            return self._data.setdefault(section,{}).get(option)
        return property(getprop, setprop)

    def _read(self):
        try:
            self._data = json.load(open(self.statefile))
        except IOError:
            self._data = {}

    def write(self):
        dnf.util.ensure_dir(os.path.dirname(self.statefile))
        with open(self.statefile, 'w') as outf:
            json.dump(self._data, outf)

    def clear(self):
        if os.path.exists(self.statefile):
            os.unlink(self.statefile)
        self._read()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.write()

    download_status = _prop("download", "status")
    datadir = _prop("download", "datadir")

    upgrade_status = _prop("upgrade", "status")
    releasever = _prop("upgrade", "releasever")
    distro_sync = _prop("upgrade", "distro-sync")
    allow_erasing = _prop("upgrade", "allowerasing")
    best = _prop("upgrade", "best")


# This idea was borrowed from dnf-plugins-core!
class PluginArgumentParser(ArgumentParser):
    def __init__(self, cmd, **kwargs):
        prog='dnf %s' % cmd
        ArgumentParser.__init__(self, prog=prog, add_help=False, **kwargs)
    def error(self, message):
        raise AttributeError(message)
    def parse_known_args(self, args=None, namespace=None):
        try:
            return ArgumentParser.parse_known_args(self, args, namespace)
        except AttributeError as e:
            self.print_help()
            raise dnf.exceptions.Error(str(e))

# DNF-API-TODO: need a way to let a Command override the display object
# that gets passed to do_transaction() so that the upgrade can talk to plymouth
class PlymouthTransactionDisplay(dnf.cli.output.CliTransactionDisplay):
    def __init__(self):
        super(PlymouthTransactionDisplay, self).__init__()
        self.current = 0
        self.total = 0

    def event(self, package, action, te_cur, te_total, ts_cur, ts_total):
        super(PlymouthTransactionDisplay, self).event(package,
            action, te_cur, te_total, ts_cur, ts_total)

        if Plymouth.alive and action in self.action:
            self._update_plymouth(action, package, ts_cur, ts_total)

    def _update_plymouth(self, action, package, current, total):
        if current == self.current and total == self.total:
            return
        (self.current, self.total) = (current, total)
        Plymouth.progress(int(100.0 * current / total))
        Plymouth.message("[%d/%d] %s %s..." % (
                         current, total, self.action.get(action), package))

# The plugin object that registers the command
class FedupPlugin(dnf.Plugin):
    name = 'fedup2'
    def __init__(self, base, cli):
        super(FedupPlugin, self).__init__(base, cli)
        self.conf = None
        self.base = base
        if cli:
            cli.register_command(FedupCommand)

# Here's the big Command class. This is where the action is!! WHEEEE!!
class FedupCommand(dnf.cli.Command):
    # pylint: disable=unused-argument
    aliases = ('fedup2','fedup','system-upgrade')
    summary = _("Prepare system for upgrade to a new release")
    usage = "[%s] [download --releasever=%s|reboot|clean]" % (
                _("OPTIONS"), _("VERSION")
    )

    def __init__(self, cli):
        super(FedupCommand, self).__init__(cli)
        self.opts = None
        self.state = State()

    def parse_args(self, extargs):
        p = PluginArgumentParser(self.aliases[0])
        p.add_argument('--distro-sync', default=False, action='store_true',
            help=_("downgrade packages if the new release's version is older"))
        p.add_argument('--datadir', default=DEFAULT_DATADIR,
            help=_("save downloaded data to this location"))
        p.add_argument('action',
            choices=('download','clean','reboot','upgrade'),
            help=_("action to perform"))

        opts, dummy = p.parse_known_args(extargs)

        if not opts.action:
            dnf.cli.commands.err_mini_usage(self.cli, self.cli.base.basecmd)
            raise dnf.cli.CliError

        return opts

    # Call sub-functions (like configure_download()) for each possible action.
    # (this tidies things up quite a bit.)
    def configure(self, args):
        self.opts = self.parse_args(args)
        self._call_sub("configure", args)
    def doCheck(self, basecmd, extcmds):
        self._call_sub("check", basecmd, extcmds)
    def run(self, extcmds):
        self._call_sub("run", extcmds)
    def run_transaction(self):
        self._call_sub("transaction")
    def _call_sub(self, name, *args):
        subfunc = getattr(self, name+'_'+self.opts.action, None)
        if callable(subfunc):
            subfunc(*args)

    # == configure_*: set up action-specific demands ==========================

    def configure_download(self, args):
        self.cli.demands.root_user = True
        self.cli.demands.resolving = True
        self.cli.demands.available_repos = True
        self.cli.demands.sack_activation = True
        self.base.repos.all().pkgdir = self.opts.datadir
        # ...don't actually install anything now, though!
        self.base.conf.tsflags.append("test")

    def configure_reboot(self, args):
        self.cli.demands.root_user = True

    def configure_upgrade(self, args):
        # same as the download, but offline and non-interactive. so..
        self.cli.demands.root_user = True
        self.cli.demands.resolving = True
        self.cli.demands.available_repos = True
        self.cli.demands.sack_activation = True
        # use the saved values for --datadir, --best, --allowerasing
        self.base.repos.all().pkgdir = self.state.datadir
        self.base.conf.best = self.state.best
        self.cli.demands.allow_erasing = self.state.allow_erasing
        # don't try to get new metadata, 'cuz we're offline
        self.cli.demands.cacheonly = True
        # and don't ask any questions (we confirmed all this beforehand)
        self.base.conf.assumeyes = True

    def configure_clean(self, args):
        self.cli.demands.root_user = True

    # == check_*: do any action-specific checks ===============================

    def check_download(self, basecmd, extargs):
        dnf.cli.commands.checkGPGKey(self.base, self.cli)
        dnf.cli.commands.checkEnabledRepo(self.base)
        checkReleaseVer(self.base.conf)
        checkDataDir(self.opts.datadir)

    def check_reboot(self, basecmd, extargs):
        if not self.state.download_status == 'complete':
            raise dnf.cli.CliError(_("system is not ready for upgrade"))

    def check_upgrade(self, basecmd, extargs):
        if not self.state.upgrade_status == 'ready':
            raise dnf.cli.CliError(_("use '%s reboot' to begin the upgrade") % basecmd)

    # == run_*: run the action/prep the transaction ===========================

    def run_reboot(self, extcmds):
        # make the magic symlink
        os.symlink(self.state.datadir, MAGIC_SYMLINK)
        # write releasever into the flag file so it can be read by systemd
        with open(SYSTEMD_FLAG_FILE, 'w') as flagfile:
            flagfile.write("RELEASEVER=%s\n" % self.state.releasever)
        # set upgrade_status so that the upgrade can run
        with self.state:
            self.state.upgrade_status = 'ready'
        reboot()

    def run_download(self, extcmds):
        # Mark everything in the world for upgrade/sync
        if self.opts.distro_sync:
            self.base.distro_sync()
        else:
            self.base.upgrade_all()

        if self.opts.datadir == DEFAULT_DATADIR:
            dnf.util.ensure_dir(self.opts.datadir)

        with self.state:
            self.state.download_status = 'downloading'
            self.state.datadir = self.opts.datadir

    def run_upgrade(self, extcmds):
        # Delete symlink ASAP to avoid reboot loops
        os.unlink(MAGIC_SYMLINK)
        # change the upgrade status (so we can detect crashed upgrades later)
        with self.state:
            self.state.upgrade_status = 'incomplete'
        # reset the splash mode and let the user know we're running
        Plymouth.set_mode("updates")
        Plymouth.progress(0)
        Plymouth.message(_("Starting system upgrade. This will take a while."))
        # set up the upgrade transaction
        if self.state.distro_sync:
            self.base.distro_sync()
        else:
            self.base.upgrade_all()

    def run_clean(self, extcmds):
        if self.state.datadir:
            log.info(_("Cleaning up downloaded data..."))
            dnf.util.clear_dir(self.state.datadir)
        self.state.clear()

    # == transaction_*: do stuff after a successful transaction ===============

    def transaction_download(self):
        # sanity check: we got a kernel, right?
        downloads = self.cli.base.transaction.install_set
        if not any(p.name.startswith('kernel') for p in downloads):
            raise dnf.exceptions.Error(NO_KERNEL_MSG)
        # Okay! Write out the state so the upgrade can use it.
        with self.state:
            self.state.download_status = 'complete'
            self.state.distro_sync = self.opts.distro_sync
            self.state.best = self.base.conf.best
            self.state.allow_erasing = self.cli.demands.allow_erasing
            self.state.releasever = self.base.conf.releasever
        log.info(DOWNLOAD_FINISHED_MSG, self.base.basecmd)

    def transaction_upgrade(self):
        Plymouth.message(_("Upgrade complete! Cleaning up and rebooting..."))
        self.run_clean([])
        reboot()
