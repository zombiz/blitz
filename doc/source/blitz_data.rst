blitz.data
==========

.. automodule:: blitz.data

The :mod:`blitz.data` module provides database utilities and models for both the client and server

- :mod:`blitz.data.database` - provides database abstraction layers for the server and client
- :mod:`blitz.data.models` - provides database models for the :class:`blitz.data.database.DatabaseClient`.

Additionally, it provides some classes for storing and manipulating data that are used by user interfaces.

--------------

.. toctree::
   :maxdepth: 2

   blitz_data_database
   blitz_data_models
   blitz_data_transforms

--------------


DataContainer
++++++++++++++

.. autoclass:: blitz.data.DataContainer
   :members:

DataTransform
++++++++++++++

.. autoclass:: blitz.data.BaseDataTransform
   :members:
