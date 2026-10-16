Build packages from the repository root in a disposable Debian build environment:

```sh
sudo apt-get install build-essential debhelper dh-sequence-dkms python3 perl
dpkg-buildpackage -b -us -uc
```

The package stages `kernel/` and `scripts/` together under `/usr/src/mars-VERSION/` and uses the same Kbuild and Makefile as a direct build. Staging excludes module objects and generated headers. `debian/rules clean` never restores or overwrites repository files. The configuration/build helper regressions run during package construction.

Install the resulting `mars-dkms` and `mars-tools` packages in a disposable VM with matching kernel headers and the target kernel's compiler. Clang kernels require clang, llvm and lld. DKMS registers, builds and installs the module; the package does not load it or create cluster metadata. The optional `mars.service` is installed disabled, and its start requires the `/mars` filesystem and cluster UUID. Configure the network policy in `/etc/modprobe.d/mars.conf` before enabling the service. See `INSTALL` for signing and service ordering.

Before releasing a package, verify these operations for every supported target kernel:

```sh
sudo apt install ./mars-dkms_*.deb ./mars-tools_*.deb
dkms status
modinfo -F vermagic mars
modinfo -F signer mars
sudo dkms autoinstall -k NEW_KERNEL_RELEASE
# After shutting down resources in the guest:
sudo apt remove mars-dkms mars-tools
dkms status
```

Also test a Secure Boot guest with the intended certificate enrolled, an upgrade while the old module is loaded, and removal with the module unloaded. Package upgrades request a reboot when the loaded and installed module versions differ; they never force-unload an active storage module. The former global legacy AIO sysctl override is no longer installed because the modern backend uses SIO.
