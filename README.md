# Playstation1Toolchain-Installer-Windows

A simple installer to help get the ground running with the Playstation 1 Open Source SDK.

Tested on Windows 11, quick install is as follows:
- main.py install --deps <br /><br />


 
Usage:<br /> 
- main.py list
- main.py install
- main.py install --pick
- main.py install --version v0.24
- main.py install --deps
- main.py install --deps --cmake-version 4.2.5
- main.py update
- main.py update --version v0.24
- main.py update --pick
- main.py deps
- main.py deps --cmake-version 4.2.5
- main.py uninstall
- main.py uninstall --pick
- main.py uninstall --version v0.24

 <br />  <br />  <br />  <br />  <br />  <br /> 




# Not affiliated/associated with the fantastic people below.
## PSn00bSDK

PSn00bSDK is an open source homebrew software development kit for the original
Sony PlayStation, consisting of a C/C++ compiler toolchain and a set of
libraries that provide a layer of abstraction over the raw hardware in order to
make game and app development easier. A CMake-based build system, CD-ROM image
packing tool (`mkpsxiso`) and asset conversion utilities are also provided.


## Credits

Main developers/authors:

* **Lameguy64** (John "Lameguy" Wilbert Villamor)
* **spicyjpeg**

Contributors:

* **Silent**, **G4Vi**, **Chromaryu**: `mkpsxiso` and `dumpsxiso` (maintained
  as a [separate repo](https://github.com/Lameguy64/mkpsxiso)).

Honorable mentions:

* **Soapy**: wrote the original version of the `inline_c.h` header containing
  GTE macros.
* **ijacquez**: helpful suggestions for getting C++ working.
* **Nicolas Noble**: author of the
  [pcsx-redux](https://github.com/grumpycoders/pcsx-redux) emulator, OpenBIOS
  and other projects which proved invaluable during development.

Helpful contributors can be found in the changelog.

References used:

* [Martin Korth's psx-spx document](http://problemkaputt.de/psx-spx.htm) and the
  [community-maintained version](https://psx-spx.consoledev.net).
* MIPS and System V ABI specs (for the dynamic linker).
* Tails92's PSXSDK project (during PSn00bSDK's infancy).

Additional references can be found in individual source files.
