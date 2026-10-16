# SPDX-License-Identifier: GPL-2.0-or-later
# Canonical external module build; also used by distribution/DKMS packages.
# Normalize an empty CI variable before the kernel probes compiler flags.
ifeq ($(strip $(SOURCE_DATE_EPOCH)),)
unexport SOURCE_DATE_EPOCH
endif
ifneq ($(KERNELRELEASE),)
include $(src)/Kbuild
else
MARSSRC := $(CURDIR)
KDIR ?= /lib/modules/$(shell uname -r)/build
DESTDIR ?=

.PHONY: all default check-kernel install clean
all default: check-kernel
	$(MAKE) -C "$(KDIR)" M="$(MARSSRC)" modules

check-kernel:
	@test -r "$(KDIR)/Makefile" -a -r "$(KDIR)/.config" || \
	    { echo "Set KDIR to configured kernel headers with Makefile and .config" >&2; exit 1; }
	@test -r "$(KDIR)/Module.symvers" || \
	    { echo "Missing $(KDIR)/Module.symvers: build the target kernel first" >&2; exit 1; }

clean:
	$(MAKE) -C "$(KDIR)" M="$(MARSSRC)" clean

install: check-kernel
	$(MAKE) -C "$(KDIR)" M="$(MARSSRC)" INSTALL_MOD_PATH="$(DESTDIR)" modules_install
endif
