# download.py - DNF extension object, for doin' upgrades
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

import os
import sys
import rpm
import dnf
import dnf.cli
import dnf.util

from .plymouth import PlymouthOutput
from .i18n import _

import logging
log = logging.getLogger("fedup2.download")

class DepsolveProgressCallback(dnf.cli.output.DepSolveProgressCallBack):
    """upgrade depsolving takes a while, so we need output to screen"""
    # NOTE: DNF calls this *after* it does hawkey stuff, while it's building
    # a Transaction object out of the hawkey goal results.
    # Right now (April 2015) the hawkey Python bindings don't expose
    # a callback hook for that (AFAICT).
    # So, if there's a pause before this starts.. that's what's going on.
    def __init__(self, cli):
        super(DepsolveProgressCallback, self).__init__()
        self.cli = cli
        self.count = 0
        self.total = None
        self.name = "finding updates"
        self.modecounter = dict()

    def bar(self):
        self.cli.progressbar(self.count, self.total, self.name)

    def start(self):
        super(DepsolveProgressCallback, self).start()
        self.bar()

    def pkg_added(self, pkg, mode):
        super(DepsolveProgressCallback, self).pkg_added(pkg, mode)
        if mode not in self.modecounter:
            self.modecounter[mode] = 0
        self.modecounter[mode] += 1
        if mode in ('ud','od'):
            self.count += 1
            self.bar()

    def end(self):
        super(DepsolveProgressCallback, self).end()
        if self.count != self.total:
            self.count = self.total
            self.bar()

class TransactionDisplay(dnf.cli.output.CliTransactionDisplay):
    def __init__(self, cli, testtrans=False):
        super(TransactionDisplay, self).__init__()
        self.cli = cli
        self.inst_count = 0
        self.inst_total = 0
        self.ply = PlymouthOutput()
        self.plymouth = self.ply.ping()
        self.testtrans = testtrans

    def _plyprog(self, cur, total, msg):
        self.ply.progress(int(100.0 * cur / total))
        self.ply.message("[%d/%d] %s" % (cur, total, msg))

    def event(self, package, action, te_cur, te_total, ts_cur, ts_total):
        super(TransactionDisplay, self).event(package,
            action, te_cur, te_total, ts_cur, ts_total)

        if self.plymouth and action in self.action:
            msg = "%s %s..." % (self.action.get(action), package)
            self._plyprog(ts_cur, ts_total, msg)

    def filelog(self, package, action):
        super(TransactionDisplay, self).filelog(package, action)

        if self.testtrans and action in (self.PKG_INSTALL, self.PKG_UPGRADE):
            self.inst_count += 1
            self.cli.progressbar(self.inst_count, self.inst_total,
                                 _("test upgrade"))
            if self.plymouth:
                msg = "%s %s..." % (self.fileaction.get(action), package)
                self._plyprog(self.inst_count, self.inst_total, msg)

class DNFWrapper(object):
    def __init__(self, cli):
        self.cli = cli
        self.base = None
        self.dlprogress = None
        self.transdisplay = None
        self.repodir = os.path.dirname(self.cli.state.statefile)
        self._get_base()

    @property
    def cachedir(self):
        return self.base.conf.cachedir

    def _subst(self, rawstr):
        return dnf.conf.parser.substitute(rawstr, self.base.conf.substitutions)

    def _get_base(self):
        """
        Work around a problem with dnf.Base():

        1) By default, the system $releasever is used to construct
           base.conf.cachedir - e.g. '/var/cache/dnf/x86_64/21'.
        2) If you pass a Conf object to dnf.Base(), it does not set up
           set up base.conf.cachedir - so you get just '/var/cache/dnf'.

        So here we borrow some code from dnf.Base._setup_default_conf to
        correctly set up base.conf.cachedir using our $releasever.
        """
        conf = dnf.conf.Conf()
        conf.releasever = self.cli.args.version
        self.base = dnf.Base(conf)
        conf = self.base.conf
        log.debug("before: conf.cachedir=%s", conf.cachedir)
        suffix = self._subst(dnf.const.CACHEDIR_SUFFIX)
        cache_dirs = dnf.conf.CliCache(conf.cachedir, suffix)
        conf.cachedir = cache_dirs.cachedir
        log.debug("after: conf.cachedir=%s", conf.cachedir)

    def setup(self, cacheonly=False):
        # activate cachedir etc.
        self.base.activate_persistor()
        # make sure datadir exists too
        dnf.util.ensure_dir(self.cli.args.datadir)
        # read repo config
        self.base.read_all_repos()
        # change pkgdir to our target dir
        self.base.repos.all().pkgdir = self.cli.args.datadir
        # apply cacheonly
        self.base.repos.all().md_only_cached = cacheonly
        # add progress callbacks
        self.dlprogress = dnf.cli.progress.MultiFileProgressMeter(fo=sys.stdout)
        self.transdisplay = TransactionDisplay(self.cli)
        self.base.repos.all().set_progress_bar(self.dlprogress)
        self.base.ds_callback = DepsolveProgressCallback(self.cli)
        # TODO: subclass dnf.cli.output.CliKeyImport to handle our key behavior
        #key_import = FedupCliKeyImport()
        #self.base.repos.all().set_key_import(key_import)

    def read_metadata(self):
        '''read rpmdb to find installed packages, get metadata for new pkgs.'''
        # may raise RepoError if a mandatory repo is unavailable
        self.base.fill_sack(load_system_repo=True, load_available_repos=True)
        return [r.id for r in self.base.repos.enabled()]

    def find_upgrade_packages(self, distro_sync=False):
        '''
        Find all available upgrades.
        returns: list of package objects.
        '''
        installed = len(self.base.doPackageLists('installed').installed)
        self.base.ds_callback.total = installed
        if distro_sync:
            self.base.distro_sync()
        else:
            self.base.upgrade_all()
        self.base.resolve() # XXX: allow_erasing? conf.best?
        downloads = self.base.transaction.install_set
        # remove rpm SIGINT handler (see dnf.cli.cli.BaseCli.do_transaction)
        del self.base.ts
        return downloads

    def download_packages(self, pkglist):
        self.base.download_packages(pkglist, self.dlprogress)

    def do_transaction(self, test=False):
        origflags = self.base.ts.getTsFlags()
        if test:
            self.base.ts.addTsFlag(rpm.RPMTRANS_FLAG_TEST)
            self.transdisplay.testtrans = True
        self.transdisplay.inst_total = len(self.base.transaction.install_set)
        self.base.do_transaction(self.transdisplay)
        self.base.ts.setFlags(origflags)
