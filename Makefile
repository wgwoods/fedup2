SED=sed
INSTALL=install -p
SYSTEMD_UNIT_DIR=/lib/systemd/system
PYTHON=python3
VERSION=v1
RELEASE_TAG=v1

GENFILES = fedup2/version.py
PYTHON_FILES = fedup2/*.py fedup2/tests/*.py fedup2.py setup.py
SYSTEMD_UNIT = fedup2-system-upgrade.service

all: build

build: $(GENFILES) $(PYTHON_FILES)
	$(PYTHON) setup.py build

test: $(PYTHON_FILES)
	$(PYTHON) -m unittest discover

install: build
	$(PYTHON) setup.py install --skip-build --root $(DESTDIR)/
	$(INSTALL) -d $(DESTDIR)$(SYSTEMD_UNIT_DIR)
	$(INSTALL) -m644 $(SYSTEMD_UNIT) $(DESTDIR)$(SYSTEMD_UNIT_DIR)

$(GENFILES): %: %.in
	$(SED) -e 's,@LIBEXECDIR@,$(LIBEXECDIR),g' \
	       -e 's,@VERSION@,$(VERSION),g' \
	       $< > $@

ARCHIVE = fedup2-$(RELEASE_TAG).tar.xz
archive: $(ARCHIVE)
$(ARCHIVE):
	git describe $(RELEASE_TAG) # check that RELEASE_TAG exists
	git archive --format=tar --prefix=fedup2-$(RELEASE_TAG)/ $(RELEASE_TAG) \
	  | xz -c > $@ || rm $@

SNAPSHOT_VERSION = $(shell git describe --tags --match 'v[0-9]*' 2>/dev/null)
SNAPSHOT = fedup2-$(SNAPSHOT_VERSION).tar.xz
snapshot: $(SNAPSHOT)
$(SNAPSHOT):
	git describe --tags --match 'v[0-9]*' # find the previous version tag
	git archive --format=tar --prefix=fedup2-$(SNAPSHOT)/ HEAD \
	  | xz -c > $@ || rm $@

clean:
	$(PYTHON) setup.py clean
	rm -rf build
	rm -f $(ARCHIVE) $(SNAPSHOT) $(GENFILES)
	rm -f fedup2/*.py[co] fedup2/tests/*.py[co]
	rm -rf fedup2/__pycache__

.PHONY: all build test install clean archive snapshot
