A Python wrapper for the Fantasy Premier League API
===================================================

.. image:: https://travis-ci.org/amosbastian/fpl.svg?branch=master
    :target: https://travis-ci.org/amosbastian/fpl

.. image:: https://badge.fury.io/py/fpl.svg
    :target: https://pypi.org/project/fpl/

.. image:: https://img.shields.io/badge/Python-3.6%2B-blue.svg
    :target: https://pypi.org/project/fpl/

.. image:: https://pepy.tech/badge/fpl
    :target: https://pepy.tech/project/fpl


.. note:: The latest version of **fpl** is asynchronous, and requires Python 3.6+!

If you're interested in helping out the development of **fpl**, or have
suggestions and ideas then please don't hesitate to create an issue on GitHub,
join our `Discord server <https://discord.gg/cjY37fv>`_ or send an email to
`amosbastian@gmail.com <mailto:amosbastian@gmail.com>`_!

--------------

**A simple example**::

    import aiohttp
    import asyncio
    from fpl import FPL
    async def main():
        async with aiohttp.ClientSession() as session:
            fpl = FPL(session)
            player = await fpl.get_player(302)
        print(player)
    ...
    # Python 3.7+
    asyncio.run(main())
    ...
    # Python 3.6
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    Fernandes - Midfielder - Man Utd

With **fpl** you can easily use the Fantasy Premier League API in all your
Python scripts, exactly how you expect it to work.

.. note:: If you are using **fpl** in an environment such as a Jupyter notebook, then you might need to use `nest_asyncio` instead of `asyncio`.

The User Guide
--------------

This part of the documentation is mostly an introduction on how to use **fpl**
and install it - including information for people newer to `asyncio`.

.. toctree::
   :maxdepth: 2

   user/installation
   user/quickstart
   user/examples

The Class Documentation / Guide
-------------------------------

This part of the documentation is for people who want or need more information
bout specific functions and classes found in **fpl**.

.. toctree::
   :maxdepth: 2

   classes/classic_league
   classes/fixture
   classes/fpl
   classes/gameweek
   classes/h2h_league
   classes/player
   classes/team
   classes/user


The Contributor Guide
---------------------

If you want to help **fpl** out and contribute to the project, be it via
development, suggestions, hunting bugs etc. then this part of the
documentation is for you!

.. toctree::
   :maxdepth: 2

   contributing/contributing
   contributing/authors
