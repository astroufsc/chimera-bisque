chimera-bisque plugin
=====================

A chimera_ plugin for `Software Bisque`_ TheSky telescopes.

**Note:** This plugin is only valid for TheSky versions **5** and **6**, for TheSkyX, please use the `chimera-ascom`_ plugin instead.

Usage
-----

Install chimera_ on your computer, and then, this package. Edit the configuration file adding
a `TheSkyTelescope` like the example below. This package, as The Sky, only works on Windows.


Installation
------------

On the Windows machine running TheSky 5 or 6, install with pip:

::

    pip install -U git+https://github.com/astroufsc/chimera-bisque.git


Configuration Examples
----------------------

* The Sky version 6. Deny slews with altitude less than 15 deg (optional). `autoclose_thesky: False` makes chimera leave
TheSky when it closes, the default is `True`.

::

	telescope:
	  name: paramount
	  type: TheSkyTelescope
	  thesky: 6
	  min_alt: 15
	  autoclose_thesky: False

Tested Hardware
---------------

This plugin was tested on these hardware:

* Paramount ME on The Sky 6 


Contact
-------

For more information, contact us on chimera's discussion list:
https://groups.google.com/forum/#!forum/chimera-discuss

Bug reports and patches are welcome and can be sent over our GitHub page:
https://github.com/astroufsc/chimera-bisque/

.. _chimera: https://www.github.com/astroufsc/chimera/
.. _chimera-ascom: https://www.github.com/astroufsc/chimera-ascom/
.. _Software Bisque: http://www.bisque.com/
