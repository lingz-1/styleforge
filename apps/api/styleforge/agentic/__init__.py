"""Current Multi-Agent Harness package.

``harness.py`` is the assembly entry point. ``graph/`` owns the parent
LangGraph, ``agents/`` contains the Coordinator/Research/Stylist/Critic and
Extension subgraphs, ``runtime/`` enforces tool and prompt boundaries, and the
deterministic ``environment.py``/``gates.py`` modules own physical legality.
There is no shadow or legacy execution path in this package.
"""
